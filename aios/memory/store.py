from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite


class MemoryStore:
    """
    Two-tier memory: short-term (cleared each run) and long-term (persists forever).
    Scoped per agent. All reads/writes go through async methods.
    """

    def __init__(self, agent_id: str, db_path: Path) -> None:
        self._agent_id = agent_id
        self._db_path = db_path
        self._short: dict[str, Any] = {}

    async def setup(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory_long (
                    agent_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (agent_id, key)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory_timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_timeline_agent ON memory_timeline(agent_id)")
            await db.commit()

    # ── Short-term (in-run, cleared on restart) ──────────────────────────────

    def set(self, key: str, value: Any) -> None:
        self._short[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._short.get(key, default)

    def clear(self) -> None:
        self._short.clear()

    # ── Long-term (persisted across runs) ────────────────────────────────────

    async def save(self, key: str, value: Any) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO memory_long (agent_id, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (self._agent_id, key, json.dumps(value), datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def load(self, key: str, default: Any = None) -> Any:
        async with aiosqlite.connect(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT value FROM memory_long WHERE agent_id = ? AND key = ?",
                    (self._agent_id, key),
                )
            ).fetchone()
            return json.loads(row[0]) if row else default

    async def delete(self, key: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM memory_long WHERE agent_id = ? AND key = ?",
                (self._agent_id, key),
            )
            await db.commit()

    async def keys(self) -> list[str]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT key FROM memory_long WHERE agent_id = ? ORDER BY updated_at DESC",
                    (self._agent_id,),
                )
            ).fetchall()
            return [r[0] for r in rows]

    async def all(self) -> dict[str, Any]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT key, value FROM memory_long WHERE agent_id = ?",
                    (self._agent_id,),
                )
            ).fetchall()
            return {r[0]: json.loads(r[1]) for r in rows}

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return memory entries whose key or serialised value contains `query` (case-insensitive).

        Returns a list of ``{"key": ..., "value": ..., "updated_at": ...}`` dicts,
        ordered by most-recently-updated first.
        """
        pattern = f"%{query}%"
        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT key, value, updated_at FROM memory_long WHERE agent_id = ? AND (key LIKE ? OR value LIKE ?) ORDER BY updated_at DESC LIMIT ?",
                    (self._agent_id, pattern, pattern, limit),
                )
            ).fetchall()
            return [{"key": r[0], "value": json.loads(r[1]), "updated_at": r[2]} for r in rows]

    # ── Timeline (append-only event log) ────────────────────────────────────

    async def log_event(self, event: str, data: dict | None = None) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO memory_timeline (agent_id, event, data, created_at) VALUES (?, ?, ?, ?)",
                (self._agent_id, event, json.dumps(data or {}), datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def timeline(self, limit: int = 100) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT event, data, created_at FROM memory_timeline WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                    (self._agent_id, limit),
                )
            ).fetchall()
            return [{"event": r[0], "data": json.loads(r[1]), "at": r[2]} for r in rows]
