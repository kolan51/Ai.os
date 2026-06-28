from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite


class CheckpointEngine:
    """
    Crash-recovery engine for agents.

    Before each tool call: cache (run_id, tool_name, args_hash) → result.
    On restart: agent.run() re-executes from the top, but every tool call
    that was already completed returns its cached result instantly — no re-execution.
    The agent fast-forwards to the first un-cached call and continues from there.

    This means agents genuinely survive crashes with zero user-visible behaviour change.
    """

    def __init__(self, agent_id: str, db_path: Path) -> None:
        self._agent_id = agent_id
        self._db_path = db_path
        self._run_id: str = ""

    async def setup(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_hash TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(agent_id, run_id, tool_name, args_hash)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    error TEXT,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    llm_calls INTEGER DEFAULT 0
                )
            """)
            # Migration: add columns to existing DBs that predate this schema
            for col, typ in [
                ("prompt_tokens", "INTEGER DEFAULT 0"),
                ("completion_tokens", "INTEGER DEFAULT 0"),
                ("total_tokens", "INTEGER DEFAULT 0"),
                ("llm_calls", "INTEGER DEFAULT 0"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE agent_runs ADD COLUMN {col} {typ}")
                except Exception:
                    pass  # column already exists
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cp_lookup ON checkpoints(agent_id, run_id, tool_name, args_hash)")
            await db.commit()

    async def start_run(self) -> str:
        """Begin a new run. Returns the run_id."""
        # Check for an interrupted run to resume
        async with aiosqlite.connect(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT id FROM agent_runs WHERE agent_id = ? AND status = 'running' ORDER BY started_at DESC LIMIT 1",
                    (self._agent_id,),
                )
            ).fetchone()

            if row:
                self._run_id = row[0]
            else:
                self._run_id = str(uuid.uuid4())
                await db.execute(
                    "INSERT INTO agent_runs (id, agent_id, status, started_at) VALUES (?, ?, 'running', ?)",
                    (self._run_id, self._agent_id, datetime.utcnow().isoformat()),
                )
                await db.commit()

        return self._run_id

    async def end_run(self, error: str | None = None) -> None:
        status = "failed" if error else "completed"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE agent_runs SET status = ?, ended_at = ?, error = ? WHERE id = ?",
                (status, datetime.utcnow().isoformat(), error, self._run_id),
            )
            await db.commit()

    async def record_llm_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Accumulate token usage for the current run."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE agent_runs
                   SET prompt_tokens     = prompt_tokens     + ?,
                       completion_tokens = completion_tokens + ?,
                       total_tokens      = total_tokens      + ?,
                       llm_calls         = llm_calls         + 1
                   WHERE id = ?""",
                (prompt_tokens, completion_tokens, prompt_tokens + completion_tokens, self._run_id),
            )
            await db.commit()

    async def get_cached(self, tool_name: str, args: dict) -> tuple[bool, Any]:
        """Return (hit, result). If hit=True, skip tool execution."""
        args_hash = _hash_args(args)
        async with aiosqlite.connect(self._db_path) as db:
            row = await (
                await db.execute(
                    "SELECT result FROM checkpoints WHERE agent_id = ? AND run_id = ? AND tool_name = ? AND args_hash = ?",
                    (self._agent_id, self._run_id, tool_name, args_hash),
                )
            ).fetchone()
            if row:
                return True, json.loads(row[0])
            return False, None

    async def save_result(self, tool_name: str, args: dict, result: Any) -> None:
        args_hash = _hash_args(args)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO checkpoints (agent_id, run_id, tool_name, args_hash, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._agent_id,
                    self._run_id,
                    tool_name,
                    args_hash,
                    json.dumps(result, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def run_history(self, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT id, status, started_at, ended_at, error FROM agent_runs WHERE agent_id = ? ORDER BY started_at DESC LIMIT ?",
                    (self._agent_id, limit),
                )
            ).fetchall()
            return [{"id": r[0], "status": r[1], "started_at": r[2], "ended_at": r[3], "error": r[4]} for r in rows]

    @property
    def run_id(self) -> str:
        return self._run_id


def _hash_args(args: dict) -> str:
    serialized = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]
