"""Eval runner — golden-file regression testing for Ai.os agents.

Each eval case:
  - Seeds optional short-term memory keys
  - Mocks the LLM response(s) so no API key or real calls are needed
  - Runs Agent.run() and captures any returned string or memory["result"]
  - Compares against the golden expected output (substring or exact match)

YAML format::

    cases:
      - name: translates to French
        mock_response: "Bonjour"       # single LLM call mock
        mock_responses:                # OR: multiple sequential responses
          - "Step 1 done"
          - "Step 2 done"
        memory:                        # optional seed for long-term memory
          city: Paris
        expected: "Bonjour"            # substring match (default)
        exact: false                   # set true for exact equality
        skip: false                    # set true to skip this case
"""

from __future__ import annotations

import importlib.util
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class EvalResult:
    name: str
    passed: bool = False
    skipped: bool = False
    expected: str = ""
    actual: str = ""
    error: str = ""
    duration: float | None = None


# ── Agent loader (shared with MCP server) ─────────────────────────────────────


def _load_agent_class(agent_file: Path):
    from ..agent import Agent

    spec = importlib.util.spec_from_file_location("_aios_eval_agent", agent_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {agent_file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    for obj in vars(mod).values():
        if inspect.isclass(obj) and issubclass(obj, Agent) and obj is not Agent:
            return obj
    raise ImportError(f"No Agent subclass found in {agent_file}")


# ── Mock LLM builder ──────────────────────────────────────────────────────────


def _build_mock_response(text: str) -> MagicMock:
    """Build a litellm-style ModelResponse mock from plain text."""
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = len(text.split())
    resp.usage.completion_tokens = len(text.split())
    resp.usage.total_tokens = len(text.split()) * 2
    return resp


def _mock_completion(responses: list[str]):
    """Return an AsyncMock that cycles through the given text responses."""
    mocks = [_build_mock_response(r) for r in responses]
    call_count = [0]

    async def _side_effect(*args, **kwargs):
        idx = min(call_count[0], len(mocks) - 1)
        call_count[0] += 1
        return mocks[idx]

    return AsyncMock(side_effect=_side_effect)


# ── Case runner ───────────────────────────────────────────────────────────────


async def _run_case(agent_class, case: dict) -> EvalResult:
    name = case.get("name", "unnamed")
    result = EvalResult(name=name)

    if case.get("skip"):
        result.skipped = True
        return result

    # Determine mock responses
    if "mock_responses" in case:
        responses = [str(r) for r in case["mock_responses"]]
    elif "mock_response" in case:
        responses = [str(case["mock_response"])]
    else:
        responses = ["[mock LLM response]"]

    expected = str(case.get("expected", responses[0]))
    exact = bool(case.get("exact", False))
    result.expected = expected

    t0 = time.monotonic()
    try:
        # Instantiate agent
        agent = agent_class.__new__(agent_class)
        agent_class.__init__(agent)

        # Bootstrap with in-memory DB (no API calls, no disk state)
        await _safe_bootstrap(agent)

        # Seed memory if requested
        seed_mem: dict = case.get("memory", {}) or {}
        for k, v in seed_mem.items():
            await agent.memory.save(k, str(v))

        # Patch litellm.acompletion (used by ModelRouter)
        mock_fn = _mock_completion(responses)
        with patch("litellm.acompletion", mock_fn):
            run_result = await agent.run()

    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.actual = ""
        result.passed = False
        result.duration = time.monotonic() - t0
        return result

    result.duration = time.monotonic() - t0

    # Extract output: prefer run()'s return value, then long-term memory keys
    if run_result is not None:
        actual = str(run_result)
    else:
        actual = (
            await agent.memory.load("result")
            or await agent.memory.load("output")
            or await agent.memory.load("answer")
            or agent.memory.get("result")  # short-term fallback
            or ""
        )
    result.actual = str(actual) if actual else ""

    if exact:
        result.passed = result.actual == expected
    else:
        result.passed = expected.lower() in result.actual.lower()

    return result


async def _safe_bootstrap(agent) -> None:
    """Bootstrap the agent using in-memory SQLite so no disk state is needed."""
    import logging
    import tempfile

    from ..memory.store import MemoryStore
    from ..models.router import ModelRouter
    from ..runtime.checkpoint import CheckpointEngine
    from ..tools.registry import ToolRegistry

    agent_id = getattr(agent.__class__, "name", "eval_agent")

    # Use a temp file so all connections within this run share the same SQLite DB
    _tmp = tempfile.NamedTemporaryFile(suffix=".eval.db", delete=False)
    _tmp.close()
    db_path = Path(_tmp.name)

    agent.memory = MemoryStore(agent_id=agent_id, db_path=db_path)
    await agent.memory.setup()

    agent._checkpoint = CheckpointEngine(agent_id=agent_id, db_path=db_path)
    await agent._checkpoint.setup()

    model = getattr(agent.__class__, "model", "claude-haiku-4-5-20251001")
    temperature = getattr(agent.__class__, "temperature", 0.7)
    max_tokens = getattr(agent.__class__, "max_tokens", 4096)
    agent._router = ModelRouter(model=model, temperature=temperature, max_tokens=max_tokens)

    agent._tools = ToolRegistry()
    agent._tools.register_from_agent(agent)
    agent._install_checkpointed_tools()

    agent.logger = logging.getLogger(f"aios.eval.{agent_id}")
    agent._tokens_used = 0
    agent._llm_calls = 0

    # Stub identity (eval doesn't need persistent identity)
    agent.identity = MagicMock()
    agent.identity.id = agent_id


# ── Suite runner ──────────────────────────────────────────────────────────────


async def run_eval_suite(
    agent_file: Path,
    suite_path: Path,
    update: bool = False,
) -> list[EvalResult]:
    """Load YAML suite, run all cases, optionally update golden outputs."""
    try:
        import yaml  # PyYAML — common dev dependency
    except ImportError:
        # Minimal YAML parser for simple cases: fallback to json if yaml unavailable
        try:
            import json as yaml  # type: ignore

            _load = lambda p: json.load(open(p))  # noqa: E731
        except Exception:
            raise ImportError("Install PyYAML: pip install pyyaml")
    else:

        def _load(p: Path):
            import yaml as _y

            with open(p) as f:
                return _y.safe_load(f)

    data = _load(suite_path)
    if not isinstance(data, dict):
        data = {}
    cases: list[dict] = data.get("cases", [])

    agent_class = _load_agent_class(agent_file)
    results: list[EvalResult] = []

    for case in cases:
        r = await _run_case(agent_class, case)
        results.append(r)

        if update and not r.skipped and not r.error:
            case["expected"] = r.actual

    if update and cases:
        try:
            import yaml

            with open(suite_path, "w") as f:
                yaml.safe_dump({"cases": cases}, f, default_flow_style=False, allow_unicode=True)
        except ImportError:
            import json

            with open(suite_path, "w") as f:
                json.dump({"cases": cases}, f, indent=2)

    return results
