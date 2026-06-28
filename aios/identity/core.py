from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiosqlite


@dataclass
class AgentIdentity:
    id: str
    name: str
    version: str
    model: str
    created_at: str
    config: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.id[:8]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "model": self.model,
            "created_at": self.created_at,
            "config": self.config,
            "tags": self.tags,
        }


async def load_identity(
    name: str,
    model: str,
    version: str,
    config: dict,
    db_path: Path,
) -> AgentIdentity:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_identity (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                tags TEXT NOT NULL DEFAULT '[]'
            )
        """)
        await db.commit()

        row = await (
            await db.execute(
                "SELECT id, name, version, model, created_at, config, tags FROM agent_identity WHERE name = ?",
                (name,),
            )
        ).fetchone()

        if row:
            identity = AgentIdentity(
                id=row[0],
                name=row[1],
                version=row[2],
                model=row[3],
                created_at=row[4],
                config=json.loads(row[5]),
                tags=json.loads(row[6]),
            )
            # Update model/version if changed
            if identity.model != model or identity.version != version:
                await db.execute(
                    "UPDATE agent_identity SET model = ?, version = ? WHERE id = ?",
                    (model, version, identity.id),
                )
                await db.commit()
                identity.model = model
                identity.version = version
            return identity

        identity = AgentIdentity(
            id=str(uuid.uuid4()),
            name=name,
            version=version,
            model=model,
            created_at=datetime.utcnow().isoformat(),
            config=config,
            tags=[],
        )
        await db.execute(
            "INSERT INTO agent_identity (id, name, version, model, created_at, config, tags) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                identity.id,
                identity.name,
                identity.version,
                identity.model,
                identity.created_at,
                json.dumps(identity.config),
                json.dumps(identity.tags),
            ),
        )
        await db.commit()
        return identity
