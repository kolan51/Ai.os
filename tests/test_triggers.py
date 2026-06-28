"""Tests for @trigger decorator and WebhookServer."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aios import Agent, trigger
from aios.triggers import WebhookServer, _TRIGGER_MARKER


# ── Decorator ─────────────────────────────────────────────────────────────────


class WebhookAgent(Agent):
    name = "webhook_test"
    model = "claude-sonnet-4-6"

    received: list = []

    @trigger("webhook", path="/hook", port=9999)
    async def run(self, payload: dict) -> None:
        WebhookAgent.received.append(payload)

    async def run_plain(self) -> None: ...  # for isinstance checks


def test_trigger_marker_set():
    assert getattr(WebhookAgent.run, _TRIGGER_MARKER, False) is True


def test_trigger_kind():
    assert getattr(WebhookAgent.run, "__aios_trigger_kind__") == "webhook"


def test_trigger_opts():
    opts = getattr(WebhookAgent.run, "__aios_trigger_opts__")
    assert opts["path"] == "/hook"
    assert opts["port"] == 9999


# ── WebhookServer ─────────────────────────────────────────────────────────────


def _make_agent(run_fn=None):
    agent = MagicMock()
    agent.name = "test"
    agent.memory.log_event = AsyncMock()
    if run_fn:
        agent.run = run_fn
    else:
        agent.run = AsyncMock()
    return agent


def _http_request(method: str, path: str, body: str = "", headers: dict | None = None) -> bytes:
    headers = headers or {}
    header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
    if header_lines:
        header_lines += "\r\n"
    return (
        f"{method} {path} HTTP/1.1\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{header_lines}"
        f"\r\n"
        f"{body}"
    ).encode()


class _FakeStream:
    def __init__(self, data: bytes):
        self._data = data
        self.written = b""

    async def read(self, n): return self._data
    def write(self, data): self.written += data
    async def drain(self): pass
    def close(self): pass


@pytest.mark.asyncio
async def test_webhook_dispatches_payload():
    dispatched = []

    async def fake_run(payload):
        dispatched.append(payload)

    agent = _make_agent(fake_run)
    server = WebhookServer(agent, path="/hook", port=9999)

    payload = {"event": "push", "ref": "refs/heads/main"}
    raw = _http_request("POST", "/hook", json.dumps(payload))
    stream = _FakeStream(raw)
    await server._handle(stream, stream)

    # Give the task time to run
    await asyncio.sleep(0.05)
    assert len(dispatched) == 1
    assert dispatched[0]["event"] == "push"


@pytest.mark.asyncio
async def test_webhook_wrong_path_returns_404():
    agent = _make_agent()
    server = WebhookServer(agent, path="/hook", port=9999)
    raw = _http_request("POST", "/other")
    stream = _FakeStream(raw)
    await server._handle(stream, stream)
    assert b"404" in stream.written


@pytest.mark.asyncio
async def test_webhook_wrong_method_returns_405():
    agent = _make_agent()
    server = WebhookServer(agent, path="/hook", port=9999)
    raw = _http_request("GET", "/hook")
    stream = _FakeStream(raw)
    await server._handle(stream, stream)
    assert b"405" in stream.written


@pytest.mark.asyncio
async def test_webhook_signature_verification_passes():
    secret = "mysecret"
    agent = _make_agent()
    server = WebhookServer(agent, path="/hook", port=9999, secret=secret)

    body = json.dumps({"x": 1})
    sig = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    raw = _http_request("POST", "/hook", body, {"X-Hub-Signature-256": sig})
    stream = _FakeStream(raw)
    await server._handle(stream, stream)
    assert b"200" in stream.written


@pytest.mark.asyncio
async def test_webhook_signature_verification_fails():
    agent = _make_agent()
    server = WebhookServer(agent, path="/hook", port=9999, secret="mysecret")
    raw = _http_request("POST", "/hook", '{"x":1}', {"X-Hub-Signature-256": "sha256=bad"})
    stream = _FakeStream(raw)
    await server._handle(stream, stream)
    assert b"401" in stream.written


def test_webhook_secret_env_resolution(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "abc123")
    server = WebhookServer(_make_agent(), secret="env:MY_SECRET")
    assert server._secret == "abc123"


def test_webhook_secret_literal():
    server = WebhookServer(_make_agent(), secret="literalvalue")
    assert server._secret == "literalvalue"
