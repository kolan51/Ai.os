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
from .scheduling import _SCHEDULE_MARKER, Scheduler, parse_interval
from .tools.registry import ToolRegistry
from .triggers import _TRIGGER_MARKER, WebhookServer

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
    max_tokens_per_run: int = 0  # 0 = unlimited; set to cap total LLM tokens this run
    stream_tokens: bool = False  # write token chunks to log in real time (visible in web UI)
    config: dict = {}

    # ── Runtime token accounting ──────────────────────────────────────────────
    _tokens_used: int = 0
    _llm_calls: int = 0

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

        self.logger = logging.getLogger(f"aios.{self.name}")
        self._install_checkpointed_tools()

        # Inject encrypted secrets into os.environ (skips already-set vars).
        # Silently skipped when the cryptography package is not installed.
        try:
            from .secrets import SecretsStore

            store = SecretsStore()
            await store.inject_to_env()
        except ImportError:
            pass

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

    async def call_agent(self, agent_class: type[Agent], prompt: str) -> str:
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

    async def spawn_agent(self, agent_class: type[Agent]) -> None:
        """
        Run another agent's full lifecycle in the background (fire-and-forget).
        The child runs independently with its own memory and checkpoint scope.
        """
        child = agent_class()
        asyncio.create_task(child._execute(), name=f"agent:{agent_class.name}")
        logger.info("[%s] spawned agent %s", self.name, agent_class.name)

    # ── Message bus ──────────────────────────────────────────────────────────

    async def publish(self, topic: str, payload: Any, ttl: int = 86_400) -> int:
        """Publish a message to a bus topic. Other agents can receive it with subscribe().

        Returns the message ID. TTL is in seconds (default 24h; 0 = no expiry).
        """
        from .bus.store import get_bus

        bus = get_bus()
        await bus.setup()
        msg_id = await bus.publish(topic, payload, sender=self.name, ttl=ttl)
        self.logger.debug("[%s] published to '%s' (id=%d)", self.name, topic, msg_id)
        return msg_id

    async def subscribe(self, topic: str, since: int = 0, limit: int = 100) -> tuple[list[dict], int]:
        """Poll a bus topic for messages newer than `since` (a message ID cursor).

        Returns (messages, new_cursor). Pass the cursor back on the next call to
        receive only new messages since last poll.

        Example::

            cursor = 0
            while True:
                msgs, cursor = await self.subscribe("alerts", since=cursor)
                for m in msgs:
                    await self.think(f"Handle alert: {m['payload']}")
                await asyncio.sleep(60)
        """
        from .bus.store import get_bus

        bus = get_bus()
        await bus.setup()
        return await bus.poll(topic, since=since, limit=limit)

    async def wait_for_message(self, topic: str, timeout: float = 30.0, since: int = 0) -> dict | None:
        """Block until a message arrives on topic, or timeout (seconds). Returns the message or None."""
        from .bus.store import get_bus

        bus = get_bus()
        await bus.setup()
        return await bus.wait(topic, timeout=timeout, since=since)

    # ── LLM helpers ───────────────────────────────────────────────────────────

    def _check_budget(self) -> None:
        if self.max_tokens_per_run > 0 and self._tokens_used >= self.max_tokens_per_run:
            raise RuntimeError(f"[{self.name}] token budget exceeded: {self._tokens_used} / {self.max_tokens_per_run} tokens used")

    def _record_usage(self, usage: dict) -> None:
        pt = usage.get("prompt_tokens", 0) or 0
        ct = usage.get("completion_tokens", 0) or 0
        self._tokens_used += pt + ct
        self._llm_calls += 1
        asyncio.create_task(self._checkpoint.record_llm_usage(pt, ct))

    async def think(self, prompt: str, context: list[dict] | None = None) -> str:
        """Single-shot LLM call — no tools, returns text.

        When ``stream_tokens = True`` on the agent class, token chunks are written
        to the log file in real time so the web UI log viewer shows them as they
        arrive (no extra infrastructure needed — the existing SSE tail picks them up).
        """
        self._check_budget()
        messages = list(context or [])
        messages.append({"role": "user", "content": prompt})

        if getattr(self, "stream_tokens", False):
            return await self._think_streaming(messages)

        resp = await self._router.complete(messages=messages, system=self.system_prompt or None)
        self._record_usage(resp.usage)
        return resp.content

    async def _think_streaming(self, messages: list[dict]) -> str:
        """Stream tokens to the log file; return the full concatenated response."""
        chunks: list[str] = []
        self.logger.info("[%s] ▶ streaming response…", self.name)
        async for token in self._router.stream(messages=messages, system=self.system_prompt or None):
            chunks.append(token)
            # Write each chunk so the SSE log tail delivers it in real time
            self.logger.debug("[stream] %s", token.replace("\n", "↵"))
        full = "".join(chunks)
        # Approximate usage (streaming doesn't return token counts from litellm)
        words = len(full.split())
        self._record_usage({"prompt_tokens": words, "completion_tokens": words, "total_tokens": words * 2})
        self.logger.info("[%s] ◀ streaming done (%d chars)", self.name, len(full))
        return full

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
            self._check_budget()
            resp = await self._router.complete(
                messages=messages,
                tools=tools or None,
                system=self.system_prompt or None,
            )

            self._record_usage(resp.usage)
            if resp.finish_reason == "stop" or not resp.tool_calls:
                return resp.content

            # Append assistant turn with tool calls
            messages.append(
                {
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
                }
            )

            # Execute tool calls (checkpointing happens inside the wrapped methods)
            for tc in resp.tool_calls:
                try:
                    result = await self._tools.call(tc["name"], tc["arguments"])
                except Exception as exc:
                    result = f"Error: {exc}"
                    logger.warning("tool %s failed: %s", tc["name"], exc)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, default=str),
                    }
                )

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
            run_method = type(self).run

            # @trigger decorator
            if getattr(run_method, _TRIGGER_MARKER, False):
                kind = getattr(run_method, "__aios_trigger_kind__", "webhook")
                opts = getattr(run_method, "__aios_trigger_opts__", {})
                if kind == "webhook":
                    server = WebhookServer(agent=self, **opts)
                    await server.serve()
                    return
                # "cron" kind falls through to @schedule handling below

            # @schedule decorator
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
            await _fire_alert_webhook(self.name, run_id, error)
        finally:
            await self.on_stop()
            await self._checkpoint.end_run(error=error)
            if error is None:
                await self.memory.log_event(
                    "run_completed",
                    {
                        "run_id": run_id,
                        "tokens": self._tokens_used,
                        "llm_calls": self._llm_calls,
                    },
                )
                logger.info(
                    "[%s] run completed — %d tokens across %d LLM call(s)",
                    self.name,
                    self._tokens_used,
                    self._llm_calls,
                )

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


async def _fire_alert_webhook(agent_name: str, run_id: str, error: str) -> None:
    """POST to AIOS_ALERT_WEBHOOK when an agent crashes. Silent if not configured."""
    import os

    url = os.environ.get("AIOS_ALERT_WEBHOOK", "").strip()
    if not url:
        return
    import datetime

    payload = {
        "agent": agent_name,
        "run_id": run_id,
        "status": "crashed",
        "error_summary": error.strip().splitlines()[-1][:300] if error else "",
        "error": error[:3000] if error else "",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(url, json=payload)
        logger.info("[%s] alert webhook fired → %s", agent_name, url)
    except Exception as exc:
        logger.warning("[%s] alert webhook failed: %s", agent_name, exc)


def _bind_args(fn: Any, args: tuple, kwargs: dict) -> dict:
    sig = inspect.signature(fn)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)
