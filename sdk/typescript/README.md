# @aios/sdk — TypeScript/Node.js SDK for Ai.os

> Persistent agents that remember, survive crashes, and run forever — now in TypeScript.

The `@aios/sdk` package lets Node.js developers write agents using the same SQLite persistence layer as the Python `aios-runtime`. An agent written in TypeScript and one written in Python share the same `~/.aios/data/<name>.db` file — memory saved by one is readable by the other.

---

## Install

```bash
npm install @aios/sdk
```

Set your Anthropic API key (or put it in `.env`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Quick start

```typescript
import { Agent, tool } from '@aios/sdk';

class HelloAgent extends Agent {
  static agentName = 'hello';
  static systemPrompt = 'You are a friendly assistant.';

  @tool({ description: 'Greet a person by name' })
  async greet(name: string): Promise<string> {
    return `Hello, ${name}!`;
  }

  async run(): Promise<void> {
    const reply = await this.thinkWithTools('Greet Alice and Bob.');
    console.log(reply);
  }
}

HelloAgent.launch();
```

Run it:

```bash
npx ts-node hello.ts
```

---

## API Reference

### `Agent` (abstract base class)

Extend `Agent` and implement `run()`.

#### Static properties

| Property | Type | Default | Description |
|---|---|---|---|
| `agentName` | `string` | class name (snake_case) | Name used for DB file (`~/.aios/data/<name>.db`) |
| `model` | `string` | `"claude-sonnet-4-6"` | Anthropic model ID |
| `systemPrompt` | `string` | `"You are a helpful AI agent."` | System prompt sent with every LLM call |
| `temperature` | `number` | `0.7` | Sampling temperature |
| `maxTokens` | `number` | `4096` | Max tokens per LLM response |

#### Instance properties

| Property | Type | Description |
|---|---|---|
| `memory` | `MemoryStore` | Persistent memory for this agent |
| `checkpoint` | `CheckpointEngine` | Access to tool-call checkpoints |
| `logger` | `Console` | Defaults to `console` |

#### Methods to override

```typescript
abstract run(): Promise<void>          // required — your agent logic
async onStart(): Promise<void>         // called before run()
async onStop(): Promise<void>          // called after run() (even on error)
```

#### LLM methods

```typescript
// Single-shot completion
await this.think(prompt: string): Promise<string>

// Agentic loop with @tool methods
await this.thinkWithTools(prompt: string, maxIterations?: number): Promise<string>
```

`thinkWithTools()` automatically discovers every method decorated with `@tool` on the agent instance, registers them with the Anthropic API, and runs the agentic loop until the model stops calling tools.

#### Entry point

```typescript
MyAgent.launch()
```

Instantiates the agent, calls `setup()` → `onStart()` → `run()` → `onStop()`. If `run()` is decorated with `@schedule`, the agent loops at that interval indefinitely.

---

### `MemoryStore`

Backed by SQLite. Two tiers:

#### Short-term (in-memory, cleared after each run)

```typescript
memory.set(key: string, value: any): void
memory.get(key: string, defaultValue?: any): any
memory.clear(): void
```

#### Long-term (persisted in SQLite `memory_long`)

```typescript
memory.save(key: string, value: any): void
memory.load(key: string, defaultValue?: any): any
memory.delete(key: string): void
memory.keys(): string[]                            // ordered by updated_at DESC
memory.all(): Record<string, any>
memory.search(query: string, limit?: number): MemoryEntry[]
```

`search()` does a LIKE match on both the key and the JSON-serialised value.

#### Timeline (append-only log in `memory_timeline`)

```typescript
memory.logEvent(event: string, data?: Record<string, any>): void
memory.timeline(limit?: number): TimelineEntry[]
```

`TimelineEntry` shape: `{ event: string; data: Record<string, any>; at: string }`.

---

### `@tool` decorator

```typescript
import { tool } from '@aios/sdk';

@tool({ description: 'Search the web for a query', retries: 3, backoff: 500 })
async webSearch(query: string): Promise<string> { ... }

// Short-hand: just pass a description string
@tool('Fetch a URL')
async fetch(url: string): Promise<string> { ... }
```

Options:

| Option | Type | Description |
|---|---|---|
| `description` | `string` | Passed to the LLM as the tool description |
| `retries` | `number` | Retry the method this many times on error (default: 0) |
| `backoff` | `number` | Initial backoff in ms between retries (exponential, default: 1000) |
| `cacheTtl` | `number` | Reserved — checkpoint TTL in seconds (future) |

---

### `@schedule` decorator

```typescript
import { schedule } from '@aios/sdk';

@schedule('every 1h')
async run(): Promise<void> { ... }
```

Supported units: `ms`, `s`, `m`, `h`, `d`. Examples: `"every 30m"`, `"every 1h"`, `"every 24h"`, `"every 2d"`.

When `run()` carries `@schedule`, `Agent.launch()` loops the agent at that interval (using `setTimeout`, not `setInterval`, so the next run begins only after the current one finishes).

---

### `CheckpointEngine`

Read/write access to tool-call checkpoints. The Python runtime stores results here so a crashed agent can resume without re-running expensive operations.

```typescript
engine.get(runId, toolName, inputHash): unknown | undefined
engine.save(runId, toolName, inputHash, result): void
engine.listForRun(runId): CheckpointRecord[]
```

The TypeScript SDK creates and reads the same `checkpoints` table as the Python runtime.

---

## SQLite schema

All data lives in `~/.aios/data/<agent_name>.db`. The schema is identical to the Python runtime so agents in both languages share the same database.

```sql
-- Long-term key/value store
CREATE TABLE memory_long (
  agent_id   TEXT NOT NULL,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,   -- JSON
  updated_at TEXT NOT NULL,   -- ISO 8601 UTC
  PRIMARY KEY (agent_id, key)
);

-- Append-only event timeline
CREATE TABLE memory_timeline (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id   TEXT NOT NULL,
  event      TEXT NOT NULL,
  data       TEXT NOT NULL DEFAULT '{}',  -- JSON
  created_at TEXT NOT NULL                -- ISO 8601 UTC
);
CREATE INDEX idx_timeline_agent ON memory_timeline(agent_id);

-- Tool-call checkpoint cache
CREATE TABLE checkpoints (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id   TEXT NOT NULL,
  run_id     TEXT NOT NULL,
  tool_name  TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  result     TEXT NOT NULL,   -- JSON
  created_at TEXT NOT NULL,
  UNIQUE(agent_id, run_id, tool_name, input_hash)
);
```

---

## Example: ResearchAgent

See [`examples/researcher.ts`](./examples/researcher.ts) for a full working example that:

- Uses `@tool` to save findings to long-term memory
- Reads prior context from memory at startup
- Calls `thinkWithTools()` to drive an agentic research loop

---

## Compatibility with the Python runtime

| Feature | Python `aios-runtime` | TypeScript `@aios/sdk` |
|---|---|---|
| Long-term memory | SQLite `memory_long` | Same table |
| Timeline | SQLite `memory_timeline` | Same table |
| Checkpoints | SQLite `checkpoints` | Same table |
| DB location | `~/.aios/data/<name>.db` | `~/.aios/data/<name>.db` |
| `@tool` decorator | async, with retries | async, with retries |
| `@schedule` decorator | cron-style | interval-style (`every Nh`) |
| LLM provider | Anthropic (Claude) | Anthropic (Claude) |

Both runtimes scope all rows by `agent_id` (the agent name), so a Python agent named `researcher` and a TypeScript agent named `researcher` read each other's memory entries.
