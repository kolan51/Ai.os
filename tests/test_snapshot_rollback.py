"""Tests for aios snapshot / snapshots / rollback commands."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from aios.cli.main import app

runner = CliRunner()


def _make_db(tmp_path: Path, agent_name: str, memory: dict[str, str]) -> Path:
    """Create a minimal agent DB with long-term memory."""
    db = tmp_path / "data" / f"{agent_name}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE memory_long "
        "(id INTEGER PRIMARY KEY, agent_id TEXT, key TEXT, value TEXT, updated_at TEXT)"
    )
    con.execute(
        "CREATE TABLE agent_snapshots "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL, "
        " tag TEXT NOT NULL, memory_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        " UNIQUE(agent_id, tag))"
    )
    for k, v in memory.items():
        con.execute(
            "INSERT INTO memory_long (agent_id, key, value, updated_at) VALUES (?,?,?,?)",
            (agent_name, k, v, "2026-01-01T00:00:00"),
        )
    con.commit()
    con.close()
    return db


def _pm_patch(tmp_path: Path, agent_name: str):
    return patch("aios.cli.main.PM.AIOS_DIR", tmp_path)


# ── snapshot ──────────────────────────────────────────────────────────────────


def test_snapshot_creates_record(tmp_path):
    name = "alpha"
    _make_db(tmp_path, name, {"city": "Ljubljana", "lang": "Python"})
    with _pm_patch(tmp_path, name):
        result = runner.invoke(app, ["snapshot", name, "--tag", "v1"])
    assert result.exit_code == 0
    assert "v1" in result.output
    assert "2 memory keys" in result.output


def test_snapshot_default_tag_is_timestamp(tmp_path):
    name = "beta"
    _make_db(tmp_path, name, {"x": "1"})
    with _pm_patch(tmp_path, name):
        result = runner.invoke(app, ["snapshot", name])
    assert result.exit_code == 0
    # timestamp format: 20xx
    assert "20" in result.output


def test_snapshot_missing_agent_errors(tmp_path):
    with _pm_patch(tmp_path, "ghost"):
        result = runner.invoke(app, ["snapshot", "ghost", "--tag", "t1"])
    assert result.exit_code != 0
    assert "No database" in result.output


def test_snapshot_overwrites_same_tag(tmp_path):
    name = "gamma"
    _make_db(tmp_path, name, {"a": "1"})
    with _pm_patch(tmp_path, name):
        runner.invoke(app, ["snapshot", name, "--tag", "same"])
        result = runner.invoke(app, ["snapshot", name, "--tag", "same"])
    assert result.exit_code == 0


# ── snapshots list ────────────────────────────────────────────────────────────


def test_snapshots_lists_tags(tmp_path):
    name = "delta"
    _make_db(tmp_path, name, {"k": "v"})
    with _pm_patch(tmp_path, name):
        runner.invoke(app, ["snapshot", name, "--tag", "v1"])
        runner.invoke(app, ["snapshot", name, "--tag", "v2"])
        result = runner.invoke(app, ["snapshots", name])
    assert "v1" in result.output
    assert "v2" in result.output


def test_snapshots_empty_message(tmp_path):
    name = "epsilon"
    _make_db(tmp_path, name, {})
    with _pm_patch(tmp_path, name):
        result = runner.invoke(app, ["snapshots", name])
    assert "No snapshots" in result.output


# ── rollback ──────────────────────────────────────────────────────────────────


def test_rollback_restores_memory(tmp_path):
    name = "zeta"
    _make_db(tmp_path, name, {"city": "Ljubljana"})
    with _pm_patch(tmp_path, name):
        runner.invoke(app, ["snapshot", name, "--tag", "v1"])
        # now corrupt memory
        db_path = tmp_path / "data" / f"{name}.db"
        con = sqlite3.connect(db_path)
        con.execute("DELETE FROM memory_long WHERE agent_id = ?", (name,))
        con.execute(
            "INSERT INTO memory_long (agent_id, key, value, updated_at) VALUES (?,?,?,?)",
            (name, "city", "Berlin", "2026-06-01"),
        )
        con.commit()
        con.close()
        # rollback to v1
        result = runner.invoke(app, ["rollback", name, "v1", "--yes"])
    assert result.exit_code == 0
    assert "1" in result.output  # "1 memory keys"
    # verify DB was restored
    con = sqlite3.connect(tmp_path / "data" / f"{name}.db")
    row = con.execute("SELECT value FROM memory_long WHERE key='city'").fetchone()
    con.close()
    assert row and row[0] == "Ljubljana"


def test_rollback_missing_snapshot(tmp_path):
    name = "eta"
    _make_db(tmp_path, name, {"x": "1"})
    with _pm_patch(tmp_path, name):
        result = runner.invoke(app, ["rollback", name, "nonexistent", "--yes"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_rollback_requires_confirmation_without_yes(tmp_path):
    name = "theta"
    _make_db(tmp_path, name, {"k": "v"})
    with _pm_patch(tmp_path, name):
        runner.invoke(app, ["snapshot", name, "--tag", "snap"])
        # send 'n' to the confirmation prompt
        result = runner.invoke(app, ["rollback", name, "snap"], input="n\n")
    # should abort (non-zero) or have "Aborted" in output
    assert result.exit_code != 0 or "Aborted" in result.output


# ── cost estimation helpers ───────────────────────────────────────────────────


def test_estimate_cost():
    from aios.cli.main import _estimate_cost
    # 1M input + 1M output
    cost = _estimate_cost(1_000_000, 1_000_000)
    assert abs(cost - 18.0) < 0.01  # $3 + $15


def test_estimate_cost_zero():
    from aios.cli.main import _estimate_cost
    assert _estimate_cost(0, 0) == 0.0


def test_fmt_cost():
    from aios.cli.main import _fmt_cost
    assert _fmt_cost(1.5) == "$1.50"
    assert "$" in _fmt_cost(0.001)
