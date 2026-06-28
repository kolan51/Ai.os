import Database from 'better-sqlite3';
import { MemoryEntry, TimelineEntry } from './types.js';

export class MemoryStore {
  private agentId: string;
  private db: Database.Database;
  private shortTerm: Map<string, unknown> = new Map();

  constructor(agentId: string, dbPath: string) {
    this.agentId = agentId;
    this.db = new Database(dbPath);
    this._init();
  }

  private _init(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS memory_long (
        agent_id   TEXT NOT NULL,
        key        TEXT NOT NULL,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (agent_id, key)
      );

      CREATE TABLE IF NOT EXISTS memory_timeline (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id   TEXT NOT NULL,
        event      TEXT NOT NULL,
        data       TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS idx_timeline_agent
        ON memory_timeline(agent_id);
    `);
  }

  // ── Short-term (in-run, in-memory) ──────────────────────────────────────

  set(key: string, value: unknown): void {
    this.shortTerm.set(key, value);
  }

  get(key: string, defaultValue?: unknown): unknown {
    if (this.shortTerm.has(key)) {
      return this.shortTerm.get(key);
    }
    return defaultValue;
  }

  clear(): void {
    this.shortTerm.clear();
  }

  // ── Long-term (persisted in SQLite) ─────────────────────────────────────

  save(key: string, value: unknown): void {
    const now = new Date().toISOString();
    const serialized = JSON.stringify(value);
    this.db
      .prepare(
        `INSERT INTO memory_long (agent_id, key, value, updated_at)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(agent_id, key) DO UPDATE SET
           value      = excluded.value,
           updated_at = excluded.updated_at`
      )
      .run(this.agentId, key, serialized, now);
  }

  load(key: string, defaultValue?: unknown): unknown {
    const row = this.db
      .prepare(
        `SELECT value FROM memory_long WHERE agent_id = ? AND key = ?`
      )
      .get(this.agentId, key) as { value: string } | undefined;

    if (!row) return defaultValue;
    try {
      return JSON.parse(row.value);
    } catch {
      return row.value;
    }
  }

  delete(key: string): void {
    this.db
      .prepare(`DELETE FROM memory_long WHERE agent_id = ? AND key = ?`)
      .run(this.agentId, key);
  }

  keys(): string[] {
    const rows = this.db
      .prepare(
        `SELECT key FROM memory_long WHERE agent_id = ? ORDER BY updated_at DESC`
      )
      .all(this.agentId) as { key: string }[];
    return rows.map((r) => r.key);
  }

  all(): Record<string, unknown> {
    const rows = this.db
      .prepare(`SELECT key, value FROM memory_long WHERE agent_id = ?`)
      .all(this.agentId) as { key: string; value: string }[];

    const result: Record<string, unknown> = {};
    for (const row of rows) {
      try {
        result[row.key] = JSON.parse(row.value);
      } catch {
        result[row.key] = row.value;
      }
    }
    return result;
  }

  search(query: string, limit = 10): MemoryEntry[] {
    const pattern = `%${query}%`;
    const rows = this.db
      .prepare(
        `SELECT key, value, updated_at
         FROM memory_long
         WHERE agent_id = ?
           AND (key LIKE ? OR value LIKE ?)
         LIMIT ?`
      )
      .all(this.agentId, pattern, pattern, limit) as {
      key: string;
      value: string;
      updated_at: string;
    }[];

    return rows.map((r) => ({
      key: r.key,
      value: (() => {
        try {
          return JSON.parse(r.value);
        } catch {
          return r.value;
        }
      })(),
      updated_at: r.updated_at,
    }));
  }

  // ── Timeline ─────────────────────────────────────────────────────────────

  logEvent(event: string, data: Record<string, unknown> = {}): void {
    const now = new Date().toISOString();
    this.db
      .prepare(
        `INSERT INTO memory_timeline (agent_id, event, data, created_at)
         VALUES (?, ?, ?, ?)`
      )
      .run(this.agentId, event, JSON.stringify(data), now);
  }

  timeline(limit = 100): TimelineEntry[] {
    const rows = this.db
      .prepare(
        `SELECT event, data, created_at
         FROM memory_timeline
         WHERE agent_id = ?
         ORDER BY id DESC
         LIMIT ?`
      )
      .all(this.agentId, limit) as {
      event: string;
      data: string;
      created_at: string;
    }[];

    return rows.map((r) => ({
      event: r.event,
      data: (() => {
        try {
          return JSON.parse(r.data) as Record<string, unknown>;
        } catch {
          return {};
        }
      })(),
      at: r.created_at,
    }));
  }

  close(): void {
    this.db.close();
  }
}
