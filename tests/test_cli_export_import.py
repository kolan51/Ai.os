"""Tests for `aios export` and `aios import` CLI commands."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aios.cli.main import app
from aios.runtime.process import ProcessManager as PM


runner = CliRunner()


@pytest.fixture()
def agent_db(tmp_path, monkeypatch):
    """Create a minimal agent SQLite DB and point AIOS_DIR at tmp_path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    db_path = data_dir / "testagent.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE memory_long (
            agent_id TEXT NOT NULL, key TEXT NOT NULL,
            value TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (agent_id, key)
        );
        CREATE TABLE memory_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL, event TEXT NOT NULL,
            data TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO memory_long VALUES ('testagent', 'answer', '"42"', '2026-01-01T00:00:00');
        INSERT INTO memory_long VALUES ('testagent', 'name', '"Alice"', '2026-01-02T00:00:00');
        INSERT INTO memory_timeline VALUES (NULL, 'testagent', 'run_complete', '{"rows":7}', '2026-01-01T01:00:00');
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(PM, "AIOS_DIR", tmp_path)
    return tmp_path, data_dir, db_path


# ── export ────────────────────────────────────────────────────────────────────


def test_export_creates_json(agent_db, tmp_path):
    _, _, _ = agent_db
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["export", "testagent", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["agent"] == "testagent"
    assert data["version"] == "1"
    assert "answer" in data["memory"]
    assert data["memory"]["answer"]["value"] == "42"
    assert len(data["timeline"]) == 1
    assert data["timeline"][0]["event"] == "run_complete"


def test_export_default_filename(agent_db, tmp_path):
    """Without --output the default filename is <agent>-memory.json."""
    out = tmp_path / "testagent-memory.json"
    result = runner.invoke(app, ["export", "testagent", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["agent"] == "testagent"


def test_export_no_timeline(agent_db, tmp_path):
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["export", "testagent", "--output", str(out), "--no-timeline"])
    assert result.exit_code == 0
    data = json.loads(out.read_text())
    assert data["timeline"] == []


def test_export_missing_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "AIOS_DIR", tmp_path)
    result = runner.invoke(app, ["export", "doesnotexist"])
    assert result.exit_code != 0
    assert "No data found" in result.output


# ── import ────────────────────────────────────────────────────────────────────


def _make_export_file(path: Path, memory: dict | None = None) -> Path:
    memory = memory or {"x": {"value": 99, "updated_at": "2026-01-01T00:00:00"}}
    data = {"version": "1", "agent": "testagent", "memory": memory, "timeline": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_import_merge(agent_db, tmp_path):
    _, data_dir, db_path = agent_db
    src = _make_export_file(tmp_path / "import.json", {"newkey": {"value": "hello", "updated_at": "2026-01-01T00:00:00"}})
    result = runner.invoke(app, ["import", "testagent", str(src)])
    assert result.exit_code == 0, result.output

    con = sqlite3.connect(db_path)
    rows = dict(con.execute("SELECT key, value FROM memory_long WHERE agent_id = 'testagent'").fetchall())
    con.close()
    # old keys preserved
    assert "answer" in rows
    assert "newkey" in rows
    assert json.loads(rows["newkey"]) == "hello"


def test_import_replace(agent_db, tmp_path):
    _, data_dir, db_path = agent_db
    src = _make_export_file(tmp_path / "import.json", {"only": {"value": "alone", "updated_at": "2026-01-01T00:00:00"}})
    result = runner.invoke(app, ["import", "testagent", str(src), "--replace"])
    assert result.exit_code == 0, result.output

    con = sqlite3.connect(db_path)
    rows = dict(con.execute("SELECT key, value FROM memory_long WHERE agent_id = 'testagent'").fetchall())
    con.close()
    assert list(rows.keys()) == ["only"]


def test_import_creates_db_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "AIOS_DIR", tmp_path)
    (tmp_path / "data").mkdir()
    src = _make_export_file(tmp_path / "import.json", {"k": {"value": "v", "updated_at": "2026-01-01T00:00:00"}})
    result = runner.invoke(app, ["import", "brandnew", str(src)])
    assert result.exit_code == 0, result.output
    db = tmp_path / "data" / "brandnew.db"
    assert db.exists()


def test_import_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "AIOS_DIR", tmp_path)
    result = runner.invoke(app, ["import", "testagent", str(tmp_path / "ghost.json")])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_import_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "AIOS_DIR", tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    result = runner.invoke(app, ["import", "testagent", str(bad)])
    assert result.exit_code != 0
    assert "Invalid JSON" in result.output
