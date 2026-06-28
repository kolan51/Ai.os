from __future__ import annotations

import asyncio
import inspect
import json
import logging
import traceback
from abc import abstractmethod
from pathlib import Path
from typing import Any

from .config import load_env, validate_model_key
from .identity.core import AgentIdentity, load_identity
from .memory.store import MemoryStore
from .models.router import ModelRouter
from .runtime.checkpoint import CheckpointEngine
from .runtime.process import AIOS_DIR
from .scheduling import Scheduler, _SCHEDULE_MARKER, parse_interval
from .tools.registry import ToolRegistry

logger = logging.getLogger("aios")


class Agent:
    """
    Base class for all Ai.os agents.

    Subclass this, declare class-level config, add @tool methods, implement run().
    The runtime handles identity, memory, crash recovery, and model routing.

    Example::

        class ResearchAgent(Agent):
            name = "researcher"
            model = "claude-sonnet-4-6"

            @tool
            async def search_web(self, query: str) -> str:
                ...

            async def run(self):
                results = await self.search_web("AI papers")
                await self.memory.save("results", results)

        if __name__ == "__main__":
            ResearchAgent.launch()
    """

    # ── Agent config (override in subclass) ──────────────────────────────────
    name: str = "agent"
    model: str = "claude-sonnet-4-6"
    version: str = "1.0.0"
    description: str = ""
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    config: dict = {}

    # ── Runtime state (populated by _bootstrap) ───────────────────────────────
    identity: AgentIdentity
    memory: MemoryStore
    _router: ModelRouter
    _tools: ToolRegistry
    _checkpoint: CheckpointEngine
    _db_path: Path

    @classmethod
    def _db_path_for(cls) -> Path:
        path = AIOS_DIR / "data" / f"{cls.name}.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def _bootstrap(self) -> None:
        self._db_path = self._db_path_for()

        self.identity = await load_identity(
            name=self.name,
            model=self.model,
            version=self.version,
            config=self.config,
            db_path=self._db_path,
        )

        self.memory = MemoryStore(agent_id=self.identity.id, db_path=self._db_path)
        await self.memory.setup()

        self._checkpoint = CheckpointEngine(agent_id=self.identity.id, db_path=self._db_path)
        await self._checkpoint.setup()

        self._router = ModelRouter(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        self._tools = ToolRegistry()
        self._tools.register_from_agent(self)
        self._install_checkpointed_tools()

    def _install_checkpointed_tools(self) -> None:
        """
        Replace every @tool method on this instance with a checkpointed wrapper.
        The wrapper checks the cache before executing and saves the result after.
        """
        for defn in self._tools.all():
            original_fn = defn.fn
            tool_name = defn.name
            setattr(self, tool_name, self._make_checkpointed(original_fn, tool_name))

    def _make_checkpointed(self, fn: Any, tool_name: str) -> Any:
        agent = self

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_args = _bind_args(fn, args, kwargs)
            hit, cached = await agent._checkpoint.get_cached(tool_name, call_args)
            if hit:
                logger.debug("checkpoint replay: %s(%s)", tool_name, call_args)
                return cached
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            await agent._checkpoint.save_result(tool_name, call_args, result)
            return result

        wrapper.__name__ = tool_name
        return wrapper

    # ── Agent-to-agent ────────────────────────────────────────────────────────

    async def call_agent(self, agent_class: "type[Agent]", prompt: str) -> str:
        """
        Instantiate another agent class and ask it a single question.
        The child agent shares this agent's memory scope for read access.
        Returns the child's text response without starting a full lifecycle run.
        """
        child = agent_class()
        await child._bootstrap()

        # Give the child read-only access to this agent's memory
        child._parent_memory = self.memory  # type: ignore[attr-defined]

        logger.info("[%s] calling agent %s", self.name, agent_class.name)
        response = await child.think(prompt)
        logger.info("[%s] agent %s returned %d chars", self.name, agent_class.name, len(response))
        return response

    async def spawn_agent(self, agent_class: "type[Agent]") -> None:
        """
        Run another agent's full lifecycle in the background (fire-and-forget).
        The child runs independently with its own memory and checkpoint scope.
        """
        child = agent_class()
        asyncio.create_task(child._execute(), name=f"agent:{agent_class.name}")
        logger.info("[%s] spawned agent %s", self.name, agent_class.name)

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def think(self, prompt: str, context: list[dict] | None = None) -> str:
        """Single-shot LLM call — no tools, returns text."""
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})
        resp = await self._router.complete(messages=messages, system=self.system_prompt or None)
        return resp.content

    async def think_with_tools(
        self,
        prompt: str,
        context: list[dict] | None = None,
        max_iterations: int = 10,
    ) -> str:
        """
        Agentic loop: LLM selects and calls tools until it produces a final text response.
        All tool calls are checkpointed — crash here, resume here on restart.
        """
        messages: list[dict] = list(context or [])
        messages.append({"role": "user", "content": prompt})
        tools = self._tools.to_llm_format()

        for iteration in range(max_iterations):
            resp = await self._router.complete(
                messages=messages,
                tools=tools or None,
                system=self.system_prompt or None,
            )

            if resp.finish_reason == "stop" or not resp.tool_calls:
                return resp.content

            # Append assistant turn with tool calls
            messages.append({
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in resp.tool_calls
                ],
            })

            # Execute tool calls (checkpointing happens inside the wrapped methods)
            for tc in resp.tool_calls:
                try:
                    result = await self._tools.call(tc["name"], tc["arguments"])
                except Exception as exc:
                    result = f"Error: {exc}"
                    logger.warning("tool %s failed: %s", tc["name"], exc)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                })

        return "Max tool-call iterations reached."

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def run(self) -> None:
        """The agent's main logic. Override this."""
        ...

    async def on_start(self) -> None:
        """Called once before run(). Override for setup work."""

    async def on_stop(self) -> None:
        """Called after run() completes or crashes. Override for cleanup."""

    async def _execute(self) -> None:
        await self._bootstrap()
        run_id = await self._checkpoint.start_run()

        logger.info("[%s] run started — id=%s model=%s", self.name, run_id[:8], self.model)
        await self.memory.log_event("run_started", {"run_id": run_id})
        await self.on_start()

        error: str | None = None
        try:
            # Check if run() is decorated with @schedule
            run_method = type(self).run
            if getattr(run_method, _SCHEDULE_MARKER, False):
                interval_str = getattr(run_method, "__aios_interval__", "every 1h")
                seconds = parse_interval(interval_str)
                if seconds > 0:
                    scheduler = Scheduler(interval_seconds=seconds)
                    await scheduler.run_loop(self.run, self.memory)
                    return
            await self.run()
        except Exception:
            error = traceback.format_exc()
            logger.error("[%s] crashed:\n%s", self.name, error)
            await self.memory.log_event("run_crashed", {"error": error[:2000]})
        finally:
            await self.on_stop()
            await self._checkpoint.end_run(error=error)
            if error is None:
                await self.memory.log_event("run_completed", {"run_id": run_id})
                logger.info("[%s] run completed", self.name)

    @classmethod
    def launch(cls) -> None:
        """Entry point. Call at the bottom of your agent file."""
        load_env()
        validate_model_key(cls.model)
        import os
        log_level = os.environ.get("AIOS_LOG_LEVEL", "INFO").upper()
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
        asyncio.run(cls()._execute())


def _bind_args(fn: Any, args: tuple, kwargs: dict) -> dict:
    sig = inspect.signature(fn)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)
