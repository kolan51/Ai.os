"""Tests for `aios memory --set` and `aios memory --delete`."""
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
    """Create a minimal agent DB and redirect AIOS_DIR to tmp_path."""
    from aios.runtime import process as proc
    monkeypatch.setattr(proc.ProcessManager, "AIOS_DIR", tmp_path)
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "testagent.db"

    async def _setup():
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE memory_long (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
            )
            await db.execute(
                "INSERT INTO memory_long VALUES (?, ?, datetime('now'))",
                ("existing_key", json.dumps("hello")),
            )
            await db.commit()

    asyncio.run(_setup())
    return db_path


def test_memory_set_new_key(agent_db):
    result = runner.invoke(app, ["memory", "testagent", "--key", "newkey", "--set", '"world"'])
    assert result.exit_code == 0
    assert "newkey" in result.output

    async def _read():
        async with aiosqlite.connect(agent_db) as db:
            row = await (await db.execute("SELECT value FROM memory_long WHERE key='newkey'")).fetchone()
            return row

    row = asyncio.run(_read())
    assert row is not None
    assert json.loads(row[0]) == "world"


def test_memory_set_json_value(agent_db):
    result = runner.invoke(app, ["memory", "testagent", "--key", "data", "--set", '{"x": 1}'])
    assert result.exit_code == 0

    async def _read():
        async with aiosqlite.connect(agent_db) as db:
            row = await (await db.execute("SELECT value FROM memory_long WHERE key='data'")).fetchone()
            return row

    row = asyncio.run(_read())
    assert json.loads(row[0]) == {"x": 1}


def test_memory_set_overwrites_existing(agent_db):
    result = runner.invoke(app, ["memory", "testagent", "--key", "existing_key", "--set", '"updated"'])
    assert result.exit_code == 0

    async def _read():
        async with aiosqlite.connect(agent_db) as db:
            row = await (await db.execute("SELECT value FROM memory_long WHERE key='existing_key'")).fetchone()
            return row

    row = asyncio.run(_read())
    assert json.loads(row[0]) == "updated"


def test_memory_set_plain_string(agent_db):
    result = runner.invoke(app, ["memory", "testagent", "--key", "plain", "--set", "not json"])
    assert result.exit_code == 0

    async def _read():
        async with aiosqlite.connect(agent_db) as db:
            row = await (await db.execute("SELECT value FROM memory_long WHERE key='plain'")).fetchone()
            return row

    row = asyncio.run(_read())
    assert json.loads(row[0]) == "not json"


def test_memory_delete_existing(agent_db):
    result = runner.invoke(app, ["memory", "testagent", "--key", "existing_key", "--delete"])
    assert result.exit_code == 0
    assert "Deleted" in result.output

    async def _read():
        async with aiosqlite.connect(agent_db) as db:
            row = await (await db.execute("SELECT value FROM memory_long WHERE key='existing_key'")).fetchone()
            return row

    row = asyncio.run(_read())
    assert row is None


def test_memory_delete_missing_key(agent_db):
    result = runner.invoke(app, ["memory", "testagent", "--key", "ghost", "--delete"])
    assert result.exit_code == 0
    assert "not found" in result.output.lower()


def test_memory_set_no_db(tmp_path, monkeypatch):
    from aios.cli import main as m
    monkeypatch.setattr(m.PM, "AIOS_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    result = runner.invoke(app, ["memory", "nobody", "--key", "k", "--set", '"v"'])
    assert result.exit_code != 0
