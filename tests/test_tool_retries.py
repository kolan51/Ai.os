"""Tests for @tool(retries=N) decorator."""
from __future__ import annotations

import pytest
from aios import Agent, tool


class RetryAgent(Agent):
    name = "retry_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...

    attempt: int = 0

    @tool(retries=2, backoff=0.01)
    async def flaky(self, x: str) -> str:
        """Flaky tool. x: input."""
        RetryAgent.attempt += 1
        if RetryAgent.attempt < 3:
            raise ValueError("not yet")
        return f"ok:{x}"

    @tool(retries=1, backoff=0.01)
    async def always_fails(self, x: str) -> str:
        """Always fails. x: input."""
        raise RuntimeError("permanent failure")

    @tool
    async def plain(self, x: str) -> str:
        """Plain tool, no retries. x: input."""
        return f"plain:{x}"


def test_tool_marker_preserved():
    from aios.tools.registry import _TOOL_MARKER
    assert getattr(RetryAgent.flaky, _TOOL_MARKER, False)
    assert getattr(RetryAgent.plain, _TOOL_MARKER, False)


def test_schema_preserved_on_retry_tool():
    schema = getattr(RetryAgent.flaky, "__aios_schema__", None)
    assert schema is not None
    assert "x" in schema["properties"]


@pytest.mark.asyncio
async def test_retries_eventually_succeed():
    RetryAgent.attempt = 0
    agent = RetryAgent()
    result = await agent.flaky("hello")
    assert result == "ok:hello"
    assert RetryAgent.attempt == 3


@pytest.mark.asyncio
async def test_retries_exhaust_raises():
    agent = RetryAgent()
    with pytest.raises(RuntimeError, match="permanent failure"):
        await agent.always_fails("x")


@pytest.mark.asyncio
async def test_plain_tool_not_wrapped():
    agent = RetryAgent()
    result = await agent.plain("hi")
    assert result == "plain:hi"


def test_tool_retries_attribute():
    assert getattr(RetryAgent.flaky, "__aios_retries__", 0) == 2
    assert getattr(RetryAgent.plain, "__aios_retries__", 0) == 0
