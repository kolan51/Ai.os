"""Tests for built-in tool mixins — no real network calls."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from aios import Agent, FilesystemMixin, ShellMixin, HttpMixin, WebSearchMixin, tool


# ── Helpers ───────────────────────────────────────────────────────────────────

class FsAgent(Agent, FilesystemMixin):
    name = "fs_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


class ShAgent(Agent, ShellMixin):
    name = "shell_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


class NetAgent(Agent, HttpMixin):
    name = "http_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


class WebAgent(Agent, WebSearchMixin):
    name = "web_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


# ── FilesystemMixin ───────────────────────────────────────────────────────────

async def test_read_file_returns_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.txt").write_text("Hello, Ai.os!")
    agent = FsAgent()
    result = await agent.read_file("hello.txt")
    assert result == "Hello, Ai.os!"


async def test_read_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = FsAgent()
    result = await agent.read_file("missing.txt")
    assert "not found" in result.lower()


async def test_write_file_creates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = FsAgent()
    result = await agent.write_file("output.txt", "line1\nline2\n")
    assert "Written" in result
    assert (tmp_path / "output.txt").read_text() == "line1\nline2\n"


async def test_write_file_append(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = FsAgent()
    await agent.write_file("log.txt", "first\n")
    await agent.write_file("log.txt", "second\n", append=True)
    content = (tmp_path / "log.txt").read_text()
    assert content == "first\nsecond\n"


async def test_write_file_creates_parent_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = FsAgent()
    await agent.write_file("nested/deep/file.txt", "content")
    assert (tmp_path / "nested" / "deep" / "file.txt").exists()


async def test_list_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    agent = FsAgent()
    result = await agent.list_directory(".", "*.py")
    assert "a.py" in result
    assert "b.py" in result
    assert "c.txt" not in result


async def test_delete_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "del.txt"
    f.write_text("bye")
    agent = FsAgent()
    result = await agent.delete_file("del.txt")
    assert "Deleted" in result
    assert not f.exists()


async def test_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "exists.txt").write_text("")
    agent = FsAgent()
    assert await agent.file_exists("exists.txt") is True
    assert await agent.file_exists("nope.txt") is False


async def test_path_escape_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    agent = FsAgent()
    with pytest.raises(PermissionError):
        await agent.read_file("../../etc/passwd")


# ── ShellMixin ────────────────────────────────────────────────────────────────

async def test_run_python_success() -> None:
    agent = ShAgent()
    result = await agent.run_python("print('hello aios')")
    assert result["success"] is True
    assert "hello aios" in result["stdout"]


async def test_run_python_failure() -> None:
    agent = ShAgent()
    result = await agent.run_python("raise ValueError('oops')")
    assert result["success"] is False
    assert result["exit_code"] != 0


async def test_run_command_success() -> None:
    agent = ShAgent()
    result = await agent.run_command(["python", "-c", "print('ok')"])
    assert result["success"] is True
    assert "ok" in result["stdout"]


async def test_run_command_allowlist_blocked() -> None:
    class RestrictedAgent(Agent, ShellMixin):
        name = "restricted"
        model = "claude-sonnet-4-6"
        shell_allowed_commands = ["python"]
        async def run(self) -> None: ...

    agent = RestrictedAgent()
    result = await agent.run_command(["rm", "-rf", "/"])
    assert result["success"] is False
    assert "not allowed" in result["stderr"]


# ── HttpMixin ─────────────────────────────────────────────────────────────────

async def test_http_get_success() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.is_success = True
    mock_response.headers = {"content-type": "application/json"}
    mock_response.text = '{"status": "ok"}'
    mock_response.json.return_value = {"status": "ok"}

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.request = AsyncMock(return_value=mock_response)
        agent = NetAgent()
        result = await agent.http_get("https://example.com/api")

    assert result["status"] == 200
    assert result["ok"] is True
    assert result["body"] == {"status": "ok"}


async def test_http_get_network_error() -> None:
    import httpx
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        agent = NetAgent()
        result = await agent.http_get("https://unreachable.example")

    assert result["ok"] is False
    assert result["status"] == 0


# ── WebSearchMixin HTML stripper ──────────────────────────────────────────────

def test_strip_html_removes_tags() -> None:
    from aios.tools.builtin.web import _strip_html
    html = "<h1>Title</h1><p>Hello <b>world</b></p>"
    result = _strip_html(html)
    assert "<" not in result
    assert "Title" in result
    assert "Hello" in result
    assert "world" in result


def test_strip_html_removes_script() -> None:
    from aios.tools.builtin.web import _strip_html
    html = "<p>Content</p><script>alert('xss')</script><p>More</p>"
    result = _strip_html(html)
    assert "alert" not in result
    assert "Content" in result
    assert "More" in result
