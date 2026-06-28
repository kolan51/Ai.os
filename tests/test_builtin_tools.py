"""Tests for built-in tool mixins — no real network calls."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from aios import Agent, DiscordMixin, EmailMixin, FilesystemMixin, NotionMixin, PostgresMixin, ShellMixin, HttpMixin, SlackMixin, WebSearchMixin, tool


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


# ── SlackMixin ────────────────────────────────────────────────────────────────

class SlAgent(Agent, SlackMixin):
    name = "slack_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


async def test_slack_send_message_calls_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    agent = SlAgent()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"ok": True, "ts": "1234567890.000100"}

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        result = await agent.slack_send_message("#general", "Hello!")

    assert "sent" in result.lower()
    assert "#general" in result


async def test_slack_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    agent = SlAgent()
    with pytest.raises(OSError, match="SLACK_BOT_TOKEN"):
        await agent.slack_send_message("#test", "hi")


async def test_slack_api_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-bad")
    agent = SlAgent()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        with pytest.raises(RuntimeError, match="channel_not_found"):
            await agent.slack_send_message("#nonexistent", "test")


async def test_slack_list_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    agent = SlAgent()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "ok": True,
        "channels": [
            {"id": "C001", "name": "general", "num_members": 100, "topic": {"value": "General chat"}},
            {"id": "C002", "name": "alerts", "num_members": 5, "topic": {"value": ""}},
        ],
    }

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
        channels = await agent.slack_list_channels()

    assert len(channels) == 2
    assert channels[0]["name"] == "general"
    assert channels[0]["id"] == "C001"


# ── PostgresMixin ─────────────────────────────────────────────────────────────

class PgAgent(Agent, PostgresMixin):
    name = "pg_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


async def test_pg_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    agent = PgAgent()
    with pytest.raises(OSError, match="POSTGRES_URL"):
        await agent.pg_query("SELECT 1")


async def test_pg_missing_asyncpg_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/test")
    monkeypatch.setitem(sys.modules, "asyncpg", None)  # type: ignore[arg-type]
    agent = PgAgent()
    with pytest.raises((ImportError, TypeError)):
        await agent.pg_query("SELECT 1")


async def test_pg_count_rejects_bad_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/test")
    agent = PgAgent()
    with pytest.raises(ValueError, match="Invalid table name"):
        await agent.pg_count("users; DROP TABLE users--")


async def test_pg_query_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/test")
    agent = PgAgent()

    mock_row = MagicMock()
    mock_row.keys.return_value = ["id", "name"]
    mock_row.__getitem__ = lambda self, k: {"id": 1, "name": "Alice"}[k]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[mock_row])
    mock_conn.close = AsyncMock()

    fake_asyncpg = MagicMock()
    fake_asyncpg.connect = AsyncMock(return_value=mock_conn)

    monkeypatch.setitem(sys.modules, "asyncpg", fake_asyncpg)
    rows = await agent.pg_query("SELECT id, name FROM users LIMIT 1")

    assert len(rows) == 1
    assert rows[0]["id"] == 1
    assert rows[0]["name"] == "Alice"


# -- EmailMixin ----------------------------------------------------------------

class EmAgent(Agent, EmailMixin):
    name = "email_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


async def test_email_missing_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    agent = EmAgent()
    with pytest.raises(OSError, match="EMAIL_SMTP_HOST"):
        await agent.send_email("to@example.com", "Test", "Body")


async def test_email_send_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_ADDRESS", "from@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    agent = EmAgent()

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
        result = await agent.send_email("to@example.com", "Hello", "World")

    assert "sent" in result.lower()
    assert "to@example.com" in result
    mock_smtp.sendmail.assert_called_once()


async def test_email_html_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_ADDRESS", "from@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    agent = EmAgent()

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_smtp
        result = await agent.send_html_email("to@example.com", "Report", "<h1>Done</h1>")

    assert "sent" in result.lower()
    assert "to@example.com" in result


# -- DiscordMixin --------------------------------------------------------------

class DiAgent(Agent, DiscordMixin):
    name = "discord_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


async def test_discord_send_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        result = await DiAgent().discord_send("Hello Discord!")

    assert "Discord" in result


async def test_discord_missing_webhook_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with pytest.raises(OSError, match="DISCORD_WEBHOOK_URL"):
        await DiAgent().discord_send("test")


async def test_discord_send_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        result = await DiAgent().discord_send_embed("Alert", "Something happened", color=0xFF0000)

    assert "Alert" in result


# -- NotionMixin ---------------------------------------------------------------

class NoAgent(Agent, NotionMixin):
    name = "notion_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


async def test_notion_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    with pytest.raises(OSError, match="NOTION_TOKEN"):
        await NoAgent().notion_search("test")


async def test_notion_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {
                "id": "abc123",
                "object": "page",
                "url": "https://notion.so/abc123",
                "last_edited_time": "2026-06-28T12:00:00Z",
                "properties": {
                    "Name": {
                        "type": "title",
                        "title": [{"plain_text": "My Page"}],
                    }
                },
            }
        ]
    }
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        results = await NoAgent().notion_search("my page")
    assert len(results) == 1
    assert results[0]["title"] == "My Page"
    assert results[0]["id"] == "abc123"


async def test_notion_extract_text() -> None:
    agent = NoAgent()
    rich_text = [{"plain_text": "Hello "}, {"plain_text": "world"}]
    assert agent._extract_text(rich_text) == "Hello world"


async def test_notion_block_to_text() -> None:
    agent = NoAgent()
    block = {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Section"}]}}
    assert agent._block_to_text(block) == "## Section"

    bullet = {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "item"}]}}
    assert agent._block_to_text(bullet).endswith("item")
    assert "item" in agent._block_to_text(bullet)


# -- LinearMixin ---------------------------------------------------------------

from aios import LinearMixin

class LinAgent(Agent, LinearMixin):
    name = "linear_test"
    model = "claude-sonnet-4-6"
    async def run(self) -> None: ...


async def test_linear_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    with pytest.raises(OSError, match="LINEAR_API_KEY"):
        await LinAgent().linear_list_teams()


async def test_linear_list_teams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "data": {
            "teams": {
                "nodes": [
                    {"id": "t1", "key": "ENG", "name": "Engineering", "description": "Core team"},
                    {"id": "t2", "key": "MKT", "name": "Marketing", "description": ""},
                ]
            }
        }
    }
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        teams = await LinAgent().linear_list_teams()
    assert len(teams) == 2
    assert teams[0]["key"] == "ENG"


async def test_linear_gql_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"errors": [{"message": "Unauthorized"}]}
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        with pytest.raises(RuntimeError, match="Unauthorized"):
            await LinAgent().linear_list_teams()
