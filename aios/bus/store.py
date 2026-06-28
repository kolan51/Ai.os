"""Agent-to-agent message bus backed by a shared SQLite database.

Agents publish messages to named topics; other agents poll or await messages.
No queue server required — just a shared ~/.aios/bus.db file.

Schema
------
messages(id, topic, sender, payload, created_at, ttl_seconds)

TTL: messages older than ttl_seconds are pruned on each publish (lazy GC).
Default TTL is 24 hours; set to 0 for no expiry.

Usage
-----
# Publisher
await bus.publish("alerts", {"level": "high", "msg": "disk full"}, sender="monitor")

# Consumer — poll since a cursor
msgs, cursor = await bus.poll("alerts", since=last_cursor)
for m in msgs:
    print(m["payload"])

# Consumer — wait for next message (long-poll, up to timeout seconds)
msg = await bus.wait("alerts", timeout=30, since=last_cursor)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_TTL = 86_400   # 24 hours


class MessageBus:
    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".aios" / "bus.db"
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)

    # ── Setup ─────────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        import aiosqlite
        async with aiosqlite.connect(self._db) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic       TEXT    NOT NULL,
                    sender      TEXT    NOT NULL DEFAULT '',
                    payload     TEXT    NOT NULL DEFAULT '{}',
                    created_at  TEXT    NOT NULL,
                    ttl_seconds INTEGER NOT NULL DEFAULT 86400
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bus_topic ON messages(topic, id)")
            await db.commit()

    # ── Publish ───────────────────────────────────────────────────────────────

    async def publish(
        self,
        topic: str,
        payload: Any,
        sender: str = "",
        ttl: int = _DEFAULT_TTL,
    ) -> int:
        """Publish a message to a topic. Returns the new message ID."""
        import aiosqlite
        now = datetime.now(timezone.utc).isoformat()
        data = json.dumps(payload, ensure_ascii=False)
        async with aiosqlite.connect(self._db) as db:
            cur = await db.execute(
                "INSERT INTO messages (topic, sender, payload, created_at, ttl_seconds) "
                "VALUES (?, ?, ?, ?, ?)",
                (topic, sender, data, now, ttl),
            )
            msg_id = cur.lastrowid
            # Lazy TTL cleanup
            await db.execute(
                "DELETE FROM messages WHERE ttl_seconds > 0 AND "
                "CAST((julianday('now') - julianday(created_at)) * 86400 AS INTEGER) > ttl_seconds"
            )
            await db.commit()
        return msg_id  # type: ignore[return-value]

    # ── Poll ──────────────────────────────────────────────────────────────────

    async def poll(
        self,
        topic: str,
        since: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        """Return messages on topic with id > since. Returns (messages, new_cursor)."""
        import aiosqlite
        async with aiosqlite.connect(self._db) as db:
            rows = await (await db.execute(
                "SELECT id, topic, sender, payload, created_at FROM messages "
                "WHERE topic = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (topic, since, limit),
            )).fetchall()

        msgs = [
            {
                "id": r[0],
                "topic": r[1],
                "sender": r[2],
                "payload": _safe_json(r[3]),
                "created_at": r[4],
            }
            for r in rows
        ]
        cursor = rows[-1][0] if rows else since
        return msgs, cursor

    # ── Wait (long-poll) ─────────────────────────────────────────────────────

    async def wait(
        self,
        topic: str,
        timeout: float = 30.0,
        since: int = 0,
        poll_interval: float = 0.5,
    ) -> dict | None:
        """Block until a new message arrives on topic, or timeout. Returns the message or None."""
        deadline = asyncio.get_event_loop().time() + timeout
        cursor = since
        while asyncio.get_event_loop().time() < deadline:
            msgs, cursor = await self.poll(topic, since=cursor, limit=1)
            if msgs:
                return msgs[0]
            remaining = deadline - asyncio.get_event_loop().time()
            await asyncio.sleep(min(poll_interval, remaining))
        return None

    # ── List topics ───────────────────────────────────────────────────────────

    async def topics(self) -> list[dict]:
        """Return all active topics with message count and latest timestamp."""
        import aiosqlite
        async with aiosqlite.connect(self._db) as db:
            rows = await (await db.execute(
                "SELECT topic, COUNT(*) as cnt, MAX(created_at) as last "
                "FROM messages GROUP BY topic ORDER BY last DESC"
            )).fetchall()
        return [{"topic": r[0], "count": r[1], "last": r[2]} for r in rows]

    # ── Drain ────────────────────────────────────────────────────────────────

    async def drain(self, topic: str) -> int:
        """Delete all messages on a topic. Returns the count deleted."""
        import aiosqlite
        async with aiosqlite.connect(self._db) as db:
            cur = await db.execute("DELETE FROM messages WHERE topic = ?", (topic,))
            await db.commit()
            return cur.rowcount  # type: ignore[return-value]

    # ── Latest ───────────────────────────────────────────────────────────────

    async def latest(self, topic: str, n: int = 20) -> list[dict]:
        """Return the n most recent messages on topic (newest last)."""
        import aiosqlite
        async with aiosqlite.connect(self._db) as db:
            rows = await (await db.execute(
                "SELECT id, topic, sender, payload, created_at FROM messages "
                "WHERE topic = ? ORDER BY id DESC LIMIT ?",
                (topic, n),
            )).fetchall()
        return [
            {"id": r[0], "topic": r[1], "sender": r[2], "payload": _safe_json(r[3]), "created_at": r[4]}
            for r in reversed(rows)
        ]


def _safe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ── Module-level singleton ────────────────────────────────────────────────────

_default_bus: MessageBus | None = None


def get_bus(db_path: Path | None = None) -> MessageBus:
    global _default_bus
    if _default_bus is None or db_path is not None:
        _default_bus = MessageBus(db_path=db_path)
    return _default_bus
