"""Tests for `aios cp`, `aios timeline`, and `aios runs` CLI commands."""
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
def agent_db(tmp_path, monkeypatch):
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "src.db"

    async def _setup():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS memory_long "
                "(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS memory_timeline "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, data TEXT, created_at TEXT)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS agent_runs "
                "(id TEXT PRIMARY KEY, status TEXT, started_at TEXT, ended_at TEXT, "
                "total_tokens INTEGER, llm_calls INTEGER, error TEXT)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS tool_checkpoints (id TEXT PRIMARY KEY)"
            )
            await db.execute(
                "INSERT INTO memory_long VALUES (?, ?, datetime('now'))",
                ("fact", json.dumps("42")),
            )
            await db.execute(
                "INSERT INTO memory_timeline (event_type, data, created_at) VALUES (?, ?, datetime('now'))",
                ("run_complete", json.dumps({"result": "ok"})),
            )
            await db.execute(
                "INSERT INTO agent_runs VALUES (?, ?, datetime('now'), datetime('now'), 1000, 5, NULL)",
                ("run-aaa-bbb", "completed"),
            )
            await db.execute(
                "INSERT INTO agent_runs VALUES (?, ?, datetime('now'), datetime('now'), 500, 2, 'ValueError')",
                ("run-xxx-yyy", "failed"),
            )
            await db.commit()

    asyncio.run(_setup())
    return tmp_path, db_path


# ── aios cp ───────────────────────────────────────────────────────────────────

def test_cp_clones_memory(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["cp", "src", "dst"])
    assert result.exit_code == 0, result.output
    assert "dst" in result.output

    dst_db = tmp_path / "data" / "dst.db"
    assert dst_db.exists()

    async def _check():
        async with aiosqlite.connect(dst_db) as db:
            row = await (await db.execute("SELECT value FROM memory_long WHERE key='fact'")).fetchone()
            return row

    row = asyncio.run(_check())
    assert row is not None and json.loads(row[0]) == "42"


def test_cp_clears_run_history(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    runner.invoke(app, ["cp", "src", "dst2"])

    async def _check():
        async with aiosqlite.connect(tmp_path / "data" / "dst2.db") as db:
            rows = await (await db.execute("SELECT id FROM agent_runs")).fetchall()
            return rows

    rows = asyncio.run(_check())
    assert rows == []


def test_cp_no_memory_flag(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    runner.invoke(app, ["cp", "src", "dst3", "--no-memory"])

    async def _check():
        async with aiosqlite.connect(tmp_path / "data" / "dst3.db") as db:
            rows = await (await db.execute("SELECT key FROM memory_long")).fetchall()
            return rows

    rows = asyncio.run(_check())
    assert rows == []


def test_cp_missing_source(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["cp", "ghost", "dst4"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_cp_dest_exists(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    runner.invoke(app, ["cp", "src", "dst5"])
    result = runner.invoke(app, ["cp", "src", "dst5"])
    assert result.exit_code != 0
    assert "already exists" in result.output.lower()


# ── aios timeline ─────────────────────────────────────────────────────────────

def test_timeline_shows_events(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["timeline", "src"])
    assert result.exit_code == 0
    assert "run_complete" in result.output


def test_timeline_type_filter(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["timeline", "src", "--type", "run_complete"])
    assert result.exit_code == 0
    assert "run_complete" in result.output

    result2 = runner.invoke(app, ["timeline", "src", "--type", "nonexistent"])
    assert result2.exit_code == 0
    assert "No events" in result2.output


def test_timeline_missing_agent(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["timeline", "ghost"])
    assert result.exit_code != 0


# ── aios runs ─────────────────────────────────────────────────────────────────

def test_runs_shows_history(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["runs", "src"])
    assert result.exit_code == 0
    assert "done" in result.output or "failed" in result.output


def test_runs_failed_filter(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["runs", "src", "--failed"])
    assert result.exit_code == 0
    assert "failed" in result.output
    assert "done" not in result.output


def test_runs_missing_agent(agent_db, monkeypatch):
    tmp_path, _ = agent_db
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    result = runner.invoke(app, ["runs", "ghost"])
    assert result.exit_code != 0
