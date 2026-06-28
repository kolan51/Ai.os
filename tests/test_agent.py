"""Integration tests for the Agent base class — bootstrap, tool wrapping, checkpoint replay."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aios import Agent, tool
from aios.runtime.checkpoint import CheckpointEngine
from aios.memory.store import MemoryStore


# ── Minimal concrete agent for testing ───────────────────────────────────────

class EchoAgent(Agent):
    name = "echo_test"
    model = "claude-sonnet-4-6"

    call_count: int = 0

    @tool
    async def echo(self, message: str) -> str:
        """Return the message back. message: The text to echo."""
        EchoAgent.call_count += 1
        return f"echo:{message}"

    async def run(self) -> None:
        result = await self.echo("hello")
        await self.memory.save("last_echo", result)


# ── Bootstrap ─────────────────────────────────────────────────────────────────

async def test_bootstrap_creates_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aios.agent.AIOS_DIR", tmp_path)
    monkeypatch.setattr("aios.runtime.process.AIOS_DIR", tmp_path)

    agent = EchoAgent()
    await agent._bootstrap()

    assert agent.identity.name == "echo_test"
    assert len(agent.identity.id) == 36  # UUID


async def test_bootstrap_identity_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aios.agent.AIOS_DIR", tmp_path)
    monkeypatch.setattr("aios.runtime.process.AIOS_DIR", tmp_path)

    agent1 = EchoAgent()
    await agent1._bootstrap()
    id1 = agent1.identity.id

    agent2 = EchoAgent()
    await agent2._bootstrap()
    id2 = agent2.identity.id

    assert id1 == id2  # same persistent identity


# ── Tool wrapping + checkpoint ────────────────────────────────────────────────

async def test_tool_result_is_checkpointed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aios.agent.AIOS_DIR", tmp_path)
    monkeypatch.setattr("aios.runtime.process.AIOS_DIR", tmp_path)

    EchoAgent.call_count = 0

    agent = EchoAgent()
    await agent._bootstrap()
    await agent._checkpoint.start_run()

    result1 = await agent.echo("test")
    assert result1 == "echo:test"
    assert EchoAgent.call_count == 1

    # Second call with same args → should replay from checkpoint, not re-execute
    result2 = await agent.echo("test")
    assert result2 == "echo:test"
    assert EchoAgent.call_count == 1  # not incremented


async def test_different_args_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aios.agent.AIOS_DIR", tmp_path)
    monkeypatch.setattr("aios.runtime.process.AIOS_DIR", tmp_path)

    EchoAgent.call_count = 0
    agent = EchoAgent()
    await agent._bootstrap()
    await agent._checkpoint.start_run()

    await agent.echo("hello")
    await agent.echo("world")  # different args

    assert EchoAgent.call_count == 2


# ── Memory integration ────────────────────────────────────────────────────────

async def test_memory_saved_during_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aios.agent.AIOS_DIR", tmp_path)
    monkeypatch.setattr("aios.runtime.process.AIOS_DIR", tmp_path)

    agent = EchoAgent()
    await agent._bootstrap()
    await agent._checkpoint.start_run()

    # Manually run the agent logic
    await agent.echo("hello")
    await agent.memory.save("last_echo", "echo:hello")

    value = await agent.memory.load("last_echo")
    assert value == "echo:hello"


# ── Scheduling ────────────────────────────────────────────────────────────────

def test_schedule_decorator_sets_marker() -> None:
    from aios.scheduling import schedule, _SCHEDULE_MARKER

    @schedule("every 5m")
    async def my_run(self):
        pass

    assert getattr(my_run, _SCHEDULE_MARKER, False) is True
    assert getattr(my_run, "__aios_interval__", "") == "every 5m"


def test_parse_interval_formats() -> None:
    from aios.scheduling import parse_interval

    assert parse_interval("every 1h") == 3600
    assert parse_interval("every 30m") == 1800
    assert parse_interval("every 1d") == 86400
    assert parse_interval("every 2 hours") == 7200
    assert parse_interval("6h") == 21600
    assert parse_interval("30m") == 1800
    assert parse_interval("0 */6 * * *") == 0  # cron → 0


# ── Config ────────────────────────────────────────────────────────────────────

def test_require_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    monkeypatch.delenv("SOME_TEST_KEY_XYZ", raising=False)
    from aios.config import require_key
    with pytest.raises(EnvironmentError, match="SOME_TEST_KEY_XYZ"):
        require_key("SOME_TEST_KEY_XYZ")


def test_require_key_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TEST_KEY_XYZ", "myvalue")
    from aios.config import require_key
    assert require_key("SOME_TEST_KEY_XYZ") == "myvalue"


def test_load_env_parses_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    env_file = tmp_path / ".env"
    env_file.write_text('TEST_FOO=bar\nTEST_BAZ="quoted"\n# comment\nINVALID\n')
    monkeypatch.delenv("TEST_FOO", raising=False)
    monkeypatch.delenv("TEST_BAZ", raising=False)

    from aios.config import load_env
    load_env(env_file)

    assert os.environ.get("TEST_FOO") == "bar"
    assert os.environ.get("TEST_BAZ") == "quoted"
