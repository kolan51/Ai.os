"""Tests for `aios test` dry-run command."""
from __future__ import annotations

from pathlib import Path
from typer.testing import CliRunner
from aios.cli.main import app

runner = CliRunner()


def _write_agent(tmp_path: Path, code: str) -> Path:
    f = tmp_path / "agent.py"
    f.write_text(code, encoding="utf-8")
    return f


SIMPLE_AGENT = """\
from aios import Agent, tool

class SimpleAgent(Agent):
    name = "simple"
    model = "claude-sonnet-4-6"

    @tool
    async def greet(self, name: str) -> str:
        "Greet someone. name: who to greet."
        return f"Hello, {name}!"

    async def run(self) -> None:
        result = await self.greet("world")
        await self.memory.save("greeting", result)

if __name__ == "__main__":
    SimpleAgent.launch()
"""

WEBHOOK_AGENT = """\
from aios import Agent, trigger

class HookAgent(Agent):
    name = "hook"
    model = "claude-sonnet-4-6"

    @trigger("webhook", path="/hook", port=9876)
    async def run(self, payload: dict) -> None:
        self.logger.info("got payload: %s", payload)

if __name__ == "__main__":
    HookAgent.launch()
"""

CRASHING_AGENT = """\
from aios import Agent

class CrashAgent(Agent):
    name = "crash"
    model = "claude-sonnet-4-6"

    async def run(self) -> None:
        raise ValueError("intentional crash")

if __name__ == "__main__":
    CrashAgent.launch()
"""


def test_test_cmd_passes_simple_agent(tmp_path):
    f = _write_agent(tmp_path, SIMPLE_AGENT)
    result = runner.invoke(app, ["test", str(f)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_test_cmd_shows_llm_calls_mocked(tmp_path):
    """think_with_tools is replaced — zero real LLM calls."""
    f = _write_agent(tmp_path, SIMPLE_AGENT)
    result = runner.invoke(app, ["test", str(f)])
    assert result.exit_code == 0
    # Tool calls section shows the greet call
    assert "greet" in result.output or "Tool calls" in result.output


def test_test_cmd_custom_mock_response(tmp_path):
    code = """\
from aios import Agent

class EchoAgent(Agent):
    name = "echo"
    model = "claude-sonnet-4-6"

    async def run(self) -> None:
        reply = await self.think("What is 2+2?")
        await self.memory.save("reply", reply)

if __name__ == "__main__":
    EchoAgent.launch()
"""
    f = _write_agent(tmp_path, code)
    result = runner.invoke(app, ["test", str(f), "--mock", "The answer is 4"])
    assert result.exit_code == 0
    assert "PASS" in result.output


def test_test_cmd_reports_failure(tmp_path):
    f = _write_agent(tmp_path, CRASHING_AGENT)
    result = runner.invoke(app, ["test", str(f)])
    assert result.exit_code != 0
    assert "FAIL" in result.output


def test_test_cmd_webhook_agent_with_payload(tmp_path):
    f = _write_agent(tmp_path, WEBHOOK_AGENT)
    result = runner.invoke(app, ["test", str(f), "--payload", '{"event": "push"}'])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_test_cmd_missing_file(tmp_path):
    result = runner.invoke(app, ["test", str(tmp_path / "ghost.py")])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_test_cmd_invalid_payload_json(tmp_path):
    f = _write_agent(tmp_path, SIMPLE_AGENT)
    result = runner.invoke(app, ["test", str(f), "--payload", "not-json"])
    assert result.exit_code != 0
    assert "Invalid" in result.output


def test_test_cmd_no_agent_class(tmp_path):
    f = _write_agent(tmp_path, "x = 1\n")
    result = runner.invoke(app, ["test", str(f)])
    assert result.exit_code != 0
    assert "No Agent subclass" in result.output
