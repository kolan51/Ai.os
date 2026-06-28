"""Tests for `aios workspace` team workspace commands."""
from __future__ import annotations

import json
import aiosqlite
import asyncio
import pytest
from pathlib import Path
from typer.testing import CliRunner
from aios.cli.main import app

runner = CliRunner()


@pytest.fixture()
def ws_env(tmp_path, monkeypatch):
    """Set up a workspace environment with a source agent DB."""
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    # Workspace dir
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir()

    # Agent DB
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "myagent.db"

    async def _setup():
        async with aiosqlite.connect(db_path) as db:
            await db.execute("CREATE TABLE memory_long (agent_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY(agent_id, key))")
            await db.execute("CREATE TABLE memory_timeline (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, event_type TEXT, data TEXT, created_at TEXT)")
            await db.execute("INSERT INTO memory_long VALUES ('myagent','fact','\"hello\"',datetime('now'))")
            await db.execute("INSERT INTO memory_timeline (agent_id,event_type,data,created_at) VALUES ('myagent','run','{}',datetime('now'))")
            await db.commit()

    asyncio.run(_setup())

    # Patch home dir for workspace config
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")

    return tmp_path, ws_dir, db_path


def test_workspace_init(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    result = runner.invoke(app, ["workspace", "init", "myteam", "--dir", str(ws_dir)])
    assert result.exit_code == 0, result.output
    assert "myteam" in result.output
    cfg = json.loads((tmp_path / "workspace.json").read_text())
    assert cfg["name"] == "myteam"
    assert cfg["dir"] == str(ws_dir)


def test_workspace_push(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    # Init first
    runner.invoke(app, ["workspace", "init", "myteam", "--dir", str(ws_dir)])
    result = runner.invoke(app, ["workspace", "push", "myagent"])
    assert result.exit_code == 0, result.output
    pushed = ws_dir / "agents" / "myagent.json"
    assert pushed.exists()
    data = json.loads(pushed.read_text())
    assert data["memory"]["fact"] == "hello"
    assert len(data["timeline"]) >= 1


def test_workspace_push_no_timeline(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    runner.invoke(app, ["workspace", "init", "myteam", "--dir", str(ws_dir)])
    runner.invoke(app, ["workspace", "push", "myagent", "--no-timeline"])
    data = json.loads((ws_dir / "agents" / "myagent.json").read_text())
    assert data["timeline"] == []


def test_workspace_pull_merge(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    runner.invoke(app, ["workspace", "init", "myteam", "--dir", str(ws_dir)])
    runner.invoke(app, ["workspace", "push", "myagent"])

    # Pull into a different agent name
    result = runner.invoke(app, ["workspace", "pull", "myagent"])
    assert result.exit_code == 0, result.output
    assert "Pulled" in result.output


def test_workspace_list(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    runner.invoke(app, ["workspace", "init", "myteam", "--dir", str(ws_dir)])
    runner.invoke(app, ["workspace", "push", "myagent"])
    result = runner.invoke(app, ["workspace", "list"])
    assert result.exit_code == 0, result.output
    assert "myagent" in result.output


def test_workspace_push_no_config(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    result = runner.invoke(app, ["workspace", "push", "myagent"])
    assert result.exit_code != 0
    assert "No workspace" in result.output


def test_workspace_pull_missing_agent(ws_env, monkeypatch):
    tmp_path, ws_dir, _ = ws_env
    from aios.cli import main as m
    monkeypatch.setattr(m, "_workspace_config_path", lambda: tmp_path / "workspace.json")
    runner.invoke(app, ["workspace", "init", "myteam", "--dir", str(ws_dir)])
    result = runner.invoke(app, ["workspace", "pull", "ghost"])
    assert result.exit_code != 0
    assert "No workspace snapshot" in result.output
