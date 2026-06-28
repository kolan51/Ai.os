import Database from 'better-sqlite3';

/**
 * CheckpointEngine — read-only stub for the Ai.os checkpoint layer.
 *
 * The Python runtime stores tool-call results in a `checkpoints` table so
 * that crashed agents can resume without re-running expensive operations.
 * This TypeScript implementation provides read access to those checkpoints
 * and exposes the same schema so checkpoints written by Python agents can
 * be read here (and vice-versa once write support is added).
 *
 * Schema (matches Python aios/runtime/checkpoint.py):
 *
 *   CREATE TABLE IF NOT EXISTS checkpoints (
 *     id         INTEGER PRIMARY KEY AUTOINCREMENT,
 *     agent_id   TEXT NOT NULL,
 *     run_id     TEXT NOT NULL,
 *     tool_name  TEXT NOT NULL,
 *     input_hash TEXT NOT NULL,
 *     result     TEXT NOT NULL,   -- JSON
 *     created_at TEXT NOT NULL,
 *     UNIQUE(agent_id, run_id, tool_name, input_hash)
 *   );
 */

export interface CheckpointRecord {
  id: number;
  agent_id: string;
  run_id: string;
  tool_name: string;
  input_hash: string;
  result: unknown;
  created_at: string;
}

export class CheckpointEngine {
  private db: Database.Database;
  private agentId: string;

  constructor(agentId: string, dbPath: string) {
    this.agentId = agentId;
    this.db = new Database(dbPath);
    this._ensureTable();
  }

  private _ensureTable(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS checkpoints (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id   TEXT NOT NULL,
        run_id     TEXT NOT NULL,
        tool_name  TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        result     TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(agent_id, run_id, tool_name, input_hash)
      )
    `);
  }

  /**
   * Look up a cached tool result by run_id, tool_name, and input_hash.
   * Returns undefined if not found.
   */
  get(
    runId: string,
    toolName: string,
    inputHash: string
  ): unknown | undefined {
    const row = this.db
      .prepare(
        `SELECT result FROM checkpoints
         WHERE agent_id = ? AND run_id = ? AND tool_name = ? AND input_hash = ?`
      )
      .get(this.agentId, runId, toolName, inputHash) as
      | { result: string }
      | undefined;

    if (!row) return undefined;
    try {
      return JSON.parse(row.result);
    } catch {
      return row.result;
    }
  }

  /**
   * List all checkpoints for a given run.
   */
  listForRun(runId: string): CheckpointRecord[] {
    const rows = this.db
      .prepare(
        `SELECT id, agent_id, run_id, tool_name, input_hash, result, created_at
         FROM checkpoints
         WHERE agent_id = ? AND run_id = ?
         ORDER BY id ASC`
      )
      .all(this.agentId, runId) as Array<{
      id: number;
      agent_id: string;
      run_id: string;
      tool_name: string;
      input_hash: string;
      result: string;
      created_at: string;
    }>;

    return rows.map((r) => ({
      ...r,
      result: (() => {
        try {
          return JSON.parse(r.result);
        } catch {
          return r.result;
        }
      })(),
    }));
  }

  /**
   * Save a tool result (write path — available even in this "stub" version).
   */
  save(
    runId: string,
    toolName: string,
    inputHash: string,
    result: unknown
  ): void {
    const now = new Date().toISOString();
    this.db
      .prepare(
        `INSERT INTO checkpoints
           (agent_id, run_id, tool_name, input_hash, result, created_at)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT(agent_id, run_id, tool_name, input_hash) DO NOTHING`
      )
      .run(this.agentId, runId, toolName, inputHash, JSON.stringify(result), now);
  }

  close(): void {
    this.db.close();
  }
}
