"""Tests for alert webhook and Prometheus metrics endpoint."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aios.web.app import create_app
from aios.runtime.process import ProcessManager as PM


# ── Alert webhook ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_webhook_fires_on_crash(monkeypatch):
    """_fire_alert_webhook posts the right payload when AIOS_ALERT_WEBHOOK is set."""
    from aios.agent import _fire_alert_webhook

    monkeypatch.setenv("AIOS_ALERT_WEBHOOK", "https://hooks.example.com/aios")
    posted = {}

    class FakeResp:
        status_code = 200

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json=None):
            posted["url"] = url
            posted["body"] = json
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await _fire_alert_webhook("myagent", "run-abc-123", "Traceback...\nValueError: boom")

    assert posted["url"] == "https://hooks.example.com/aios"
    assert posted["body"]["agent"] == "myagent"
    assert posted["body"]["status"] == "crashed"
    assert "ValueError: boom" in posted["body"]["error_summary"]
    assert "run-abc-123" in posted["body"]["run_id"]


@pytest.mark.asyncio
async def test_alert_webhook_silent_when_not_configured(monkeypatch):
    """No HTTP call is made when AIOS_ALERT_WEBHOOK is empty."""
    from aios.agent import _fire_alert_webhook

    monkeypatch.delenv("AIOS_ALERT_WEBHOOK", raising=False)

    with patch("httpx.AsyncClient") as mock_cls:
        await _fire_alert_webhook("myagent", "run-xyz", "Some error")
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_alert_webhook_swallows_network_error(monkeypatch):
    """A failed webhook POST is caught and logged — does not propagate."""
    from aios.agent import _fire_alert_webhook

    monkeypatch.setenv("AIOS_ALERT_WEBHOOK", "https://hooks.example.com/aios")

    class BrokenClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): raise ConnectionError("network down")

    with patch("httpx.AsyncClient", return_value=BrokenClient()):
        # Must not raise
        await _fire_alert_webhook("myagent", "run-xyz", "err")


# ── Prometheus metrics ─────────────────────────────────────────────────────────


@pytest.fixture()
def metrics_client(tmp_path, monkeypatch):
    """TestClient with a fake agent DB."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "metagent.db"

    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE agent_runs (
            id TEXT PRIMARY KEY, agent_id TEXT, status TEXT,
            started_at TEXT, ended_at TEXT, error TEXT
        );
        CREATE TABLE memory_long (
            agent_id TEXT, key TEXT, value TEXT, updated_at TEXT,
            PRIMARY KEY (agent_id, key)
        );
        CREATE TABLE checkpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, run_id TEXT, tool_name TEXT,
            args_hash TEXT, result TEXT, created_at TEXT,
            UNIQUE(agent_id, run_id, tool_name, args_hash)
        );
        INSERT INTO agent_runs VALUES ('r1','metagent','completed','2026-01-01',NULL,NULL);
        INSERT INTO agent_runs VALUES ('r2','metagent','failed','2026-01-02',NULL,'oops');
        INSERT INTO memory_long VALUES ('metagent','k1','v1','2026-01-01');
        INSERT INTO memory_long VALUES ('metagent','k2','v2','2026-01-01');
        INSERT INTO checkpoints VALUES (NULL,'metagent','r2','my_tool','abc123','{}','2026-01-02');
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(PM, "AIOS_DIR", tmp_path)
    monkeypatch.setattr(
        PM, "list_agents",
        staticmethod(lambda: [{"name": "metagent", "running": True, "pid": 1234}]),
    )

    app = create_app()
    return TestClient(app)


def test_metrics_endpoint_returns_prometheus_text(metrics_client):
    resp = metrics_client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert 'aios_agent_running{agent="metagent"} 1' in body
    assert 'aios_agent_runs_total{agent="metagent"} 2' in body
    assert 'aios_agent_runs_completed_total{agent="metagent"} 1' in body
    assert 'aios_agent_runs_failed_total{agent="metagent"} 1' in body
    assert 'aios_agent_memory_keys{agent="metagent"} 2' in body
    assert 'aios_agent_checkpoints_total{agent="metagent"} 1' in body


def test_metrics_includes_help_lines(metrics_client):
    body = metrics_client.get("/metrics").text
    assert "# HELP aios_agent_running" in body
    assert "# TYPE aios_agent_running gauge" in body
    assert "# HELP aios_agent_runs_total" in body
