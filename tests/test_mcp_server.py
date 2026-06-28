"""Tests for the MCP server module (schema, message helpers, agent loading)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

from aios.mcp.server import _ok, _err, _tool_schema, _load_agent_class


def test_ok_message():
    msg = _ok(1, {"result": "hello"})
    assert msg["jsonrpc"] == "2.0"
    assert msg["id"] == 1
    assert msg["result"] == {"result": "hello"}


def test_err_message():
    msg = _err(2, -32601, "Method not found")
    assert msg["error"]["code"] == -32601
    assert "Method not found" in msg["error"]["message"]


# ── tool schema ────────────────────────────────────────────────────────────────

def _make_agent_file(tmp_path: Path, source: str) -> Path:
    f = tmp_path / "agent_mcp_test.py"
    f.write_text(textwrap.dedent(source))
    return f


def test_tool_schema_no_run_args(tmp_path):
    """Agent.run() with no params → generic prompt input."""
    f = _make_agent_file(tmp_path, """
        from aios import Agent
        class SimpleAgent(Agent):
            name = "simple"
            model = "claude-haiku-4-5-20251001"
            system_prompt = "You help."
            async def run(self): pass
    """)
    cls = _load_agent_class(f)
    schema = _tool_schema("simple", "", cls)
    assert schema["name"] == "simple"
    assert "prompt" in schema["inputSchema"]["properties"]
    assert "prompt" in schema["inputSchema"]["required"]


def test_tool_schema_with_run_args(tmp_path):
    """Agent.run(query: str) → query is a required input."""
    f = _make_agent_file(tmp_path, """
        from aios import Agent
        class QueryAgent(Agent):
            name = "query"
            model = "claude-haiku-4-5-20251001"
            system_prompt = "You help."
            async def run(self, query: str, limit: int = 5): pass
    """)
    cls = _load_agent_class(f)
    schema = _tool_schema("query", "A query agent", cls)
    props = schema["inputSchema"]["properties"]
    assert "query" in props
    assert props["query"]["type"] == "string"
    assert "limit" in props
    assert props["limit"]["type"] == "number"
    assert "query" in schema["inputSchema"]["required"]
    assert "limit" not in schema["inputSchema"]["required"]  # has default


def test_tool_schema_custom_description(tmp_path):
    f = _make_agent_file(tmp_path, """
        from aios import Agent
        class A(Agent):
            name = "a"
            model = "claude-haiku-4-5-20251001"
            system_prompt = "help"
            async def run(self): pass
    """)
    cls = _load_agent_class(f)
    schema = _tool_schema("a", "My custom desc", cls)
    assert schema["description"] == "My custom desc"


def test_tool_schema_uses_agent_description(tmp_path):
    f = _make_agent_file(tmp_path, """
        from aios import Agent
        class A(Agent):
            name = "a"
            model = "claude-haiku-4-5-20251001"
            system_prompt = "help"
            description = "From class attr"
            async def run(self): pass
    """)
    cls = _load_agent_class(f)
    schema = _tool_schema("a", "", cls)
    assert schema["description"] == "From class attr"


# ── agent loading ──────────────────────────────────────────────────────────────

def test_load_agent_class_success(tmp_path):
    f = _make_agent_file(tmp_path, """
        from aios import Agent
        class MyAgent(Agent):
            name = "my"
            model = "claude-haiku-4-5-20251001"
            system_prompt = "help"
            async def run(self): pass
    """)
    cls = _load_agent_class(f)
    assert cls.__name__ == "MyAgent"


def test_load_agent_class_no_agent_raises(tmp_path):
    f = tmp_path / "plain.py"
    f.write_text("x = 1\n")
    with pytest.raises(ImportError, match="No Agent subclass"):
        _load_agent_class(f)


def test_load_agent_class_missing_file(tmp_path):
    with pytest.raises((ImportError, FileNotFoundError, AttributeError)):
        _load_agent_class(tmp_path / "nonexistent.py")


def test_load_agent_class_picks_subclass_not_base(tmp_path):
    """Should not return the Agent base class itself."""
    f = _make_agent_file(tmp_path, """
        from aios import Agent
        class WorkerAgent(Agent):
            name = "worker"
            model = "claude-haiku-4-5-20251001"
            system_prompt = "work"
            async def run(self): pass
    """)
    cls = _load_agent_class(f)
    from aios.agent import Agent as BaseAgent
    assert cls is not BaseAgent
    assert issubclass(cls, BaseAgent)
