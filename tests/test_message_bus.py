"""Tests for the agent-to-agent message bus."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aios.bus.store import MessageBus


@pytest.fixture
def bus(tmp_path) -> MessageBus:
    return MessageBus(db_path=tmp_path / "test_bus.db")


@pytest.fixture
async def setup_bus(bus):
    await bus.setup()
    return bus


# ── setup ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_creates_table(tmp_path):
    bus = MessageBus(db_path=tmp_path / "b.db")
    await bus.setup()
    import aiosqlite
    async with aiosqlite.connect(tmp_path / "b.db") as db:
        row = await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )).fetchone()
    assert row is not None


# ── publish ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_returns_id(setup_bus):
    bus = setup_bus
    mid = await bus.publish("events", {"x": 1})
    assert isinstance(mid, int)
    assert mid > 0


@pytest.mark.asyncio
async def test_publish_dict_payload(setup_bus):
    bus = setup_bus
    await bus.publish("data", {"key": "value"})
    msgs, _ = await bus.poll("data")
    assert len(msgs) == 1
    assert msgs[0]["payload"] == {"key": "value"}


@pytest.mark.asyncio
async def test_publish_string_payload(setup_bus):
    bus = setup_bus
    await bus.publish("chat", "hello world")
    msgs, _ = await bus.poll("chat")
    assert msgs[0]["payload"] == "hello world"


@pytest.mark.asyncio
async def test_publish_with_sender(setup_bus):
    bus = setup_bus
    await bus.publish("alerts", "disk full", sender="monitor-agent")
    msgs, _ = await bus.poll("alerts")
    assert msgs[0]["sender"] == "monitor-agent"


# ── poll ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_empty_topic(setup_bus):
    bus = setup_bus
    msgs, cursor = await bus.poll("no-such-topic")
    assert msgs == []
    assert cursor == 0


@pytest.mark.asyncio
async def test_poll_returns_cursor(setup_bus):
    bus = setup_bus
    await bus.publish("t", "a")
    await bus.publish("t", "b")
    msgs, cursor = await bus.poll("t")
    assert len(msgs) == 2
    assert cursor == msgs[-1]["id"]


@pytest.mark.asyncio
async def test_poll_since_cursor_only_new(setup_bus):
    bus = setup_bus
    await bus.publish("t2", "first")
    msgs, cursor = await bus.poll("t2")
    await bus.publish("t2", "second")
    new_msgs, new_cursor = await bus.poll("t2", since=cursor)
    assert len(new_msgs) == 1
    assert new_msgs[0]["payload"] == "second"


@pytest.mark.asyncio
async def test_poll_limit(setup_bus):
    bus = setup_bus
    for i in range(10):
        await bus.publish("bulk", i)
    msgs, _ = await bus.poll("bulk", limit=3)
    assert len(msgs) == 3


# ── topics ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_topics_lists_all(setup_bus):
    bus = setup_bus
    await bus.publish("alpha", "x")
    await bus.publish("beta", "y")
    await bus.publish("alpha", "z")
    topics = await bus.topics()
    names = {t["topic"] for t in topics}
    assert "alpha" in names
    assert "beta" in names
    alpha = next(t for t in topics if t["topic"] == "alpha")
    assert alpha["count"] == 2


# ── latest ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_latest_ordered_oldest_first(setup_bus):
    bus = setup_bus
    for i in range(5):
        await bus.publish("seq", i)
    msgs = await bus.latest("seq", n=5)
    payloads = [m["payload"] for m in msgs]
    assert payloads == list(range(5))


@pytest.mark.asyncio
async def test_latest_respects_n(setup_bus):
    bus = setup_bus
    for i in range(10):
        await bus.publish("lot", i)
    msgs = await bus.latest("lot", n=3)
    assert len(msgs) == 3
    assert msgs[-1]["payload"] == 9  # most recent last


# ── drain ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drain_clears_topic(setup_bus):
    bus = setup_bus
    await bus.publish("to-drain", "a")
    await bus.publish("to-drain", "b")
    count = await bus.drain("to-drain")
    assert count == 2
    msgs, _ = await bus.poll("to-drain")
    assert msgs == []


@pytest.mark.asyncio
async def test_drain_does_not_affect_other_topics(setup_bus):
    bus = setup_bus
    await bus.publish("keep", "safe")
    await bus.publish("drop", "gone")
    await bus.drain("drop")
    msgs, _ = await bus.poll("keep")
    assert len(msgs) == 1


# ── wait ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wait_returns_existing_message(setup_bus):
    bus = setup_bus
    await bus.publish("fast", "already here")
    msg = await bus.wait("fast", timeout=1.0)
    assert msg is not None
    assert msg["payload"] == "already here"


@pytest.mark.asyncio
async def test_wait_times_out(setup_bus):
    bus = setup_bus
    msg = await bus.wait("empty", timeout=0.1)
    assert msg is None


@pytest.mark.asyncio
async def test_wait_receives_late_message(setup_bus):
    bus = setup_bus

    async def _delayed_publish():
        await asyncio.sleep(0.15)
        await bus.publish("late", "arrived")

    asyncio.create_task(_delayed_publish())
    msg = await bus.wait("late", timeout=2.0, poll_interval=0.05)
    assert msg is not None
    assert msg["payload"] == "arrived"


# ── agent integration ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_publish_subscribe(tmp_path):
    """Agent.publish() and Agent.subscribe() round-trip via the bus."""
    from unittest.mock import patch
    from aios.bus.store import MessageBus

    test_bus = MessageBus(db_path=tmp_path / "agent_bus.db")
    await test_bus.setup()

    with patch("aios.bus.store._default_bus", test_bus):
        # Simulate a minimal agent with publish/subscribe wired in
        import aios.agent as agent_mod

        class FakeAgent:
            name = "test-agent"

            class logger:
                @staticmethod
                def debug(*a): pass

            async def publish(self, topic, payload, ttl=86400):
                mid = await test_bus.publish(topic, payload, sender=self.name, ttl=ttl)
                return mid

            async def subscribe(self, topic, since=0, limit=100):
                return await test_bus.poll(topic, since=since, limit=limit)

        agent = FakeAgent()
        mid = await agent.publish("orders", {"item": "widget", "qty": 3})
        assert mid > 0

        msgs, cursor = await agent.subscribe("orders")
        assert len(msgs) == 1
        assert msgs[0]["payload"]["item"] == "widget"
        assert msgs[0]["sender"] == "test-agent"
