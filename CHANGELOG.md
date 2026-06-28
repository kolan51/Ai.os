# Changelog

All notable changes to Ai.os are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added — Agent-to-agent message bus
- **`await self.publish(topic, payload)`** — publish any JSON-serialisable value to a named topic; survives process restarts (stored in `~/.aios/bus.db`)
- **`await self.subscribe(topic, since=cursor)`** — poll for messages newer than a cursor; returns `(messages, new_cursor)` so agents can pick up exactly where they left off across runs
- **`await self.wait_for_message(topic, timeout=30)`** — long-poll: blocks until a message arrives or timeout expires
- **`aios bus publish <topic> <payload>`** — publish from CLI (JSON or plain text)
- **`aios bus list`** — show all active topics with message count and age
- **`aios bus read <topic> [--follow]`** — print recent messages; `--follow` watches for new ones
- **`aios bus drain <topic>`** — delete all messages on a topic
- Lazy TTL cleanup on every publish (default 24 h; `ttl=0` for no expiry)
- `aios/bus/store.py` — `MessageBus` class with `publish / poll / wait / topics / latest / drain`; shared `~/.aios/bus.db` used by all agents on the same machine

### Added — Streaming LLM tokens
- **`stream_tokens = True`** class attribute on any agent — enables streaming mode for `think()`: tokens are written to the log file in real time as they arrive from the LLM
- Web UI log viewer renders `[stream]` lines in a distinct blue italic style; they are auto-hidden by the "Hide noise" toggle (individual tokens are verbose — the full response appears in the subsequent `INFO` line)
- `Agent._think_streaming()` — internal helper that accumulates the full response from `ModelRouter.stream()` while writing each chunk to the logger
- Zero infrastructure cost — uses the existing SSE log tail that the web UI already has open

### Added — `aios eval` improvements
- **`--junit <path>`** — write JUnit XML results file; parsed by GitHub Actions, GitLab CI, Jenkins, and any JUnit-compatible CI system
- **`.github/workflows/eval.yml`** — reusable GitHub Action that discovers all `*.eval.yaml` files in the repo, runs `aios eval` on each, and publishes a pull request check with pass/fail/skip counts

### Added — Eval framework (`aios eval`)
- **`aios eval <agent.py>`** — golden-file regression testing with a mocked LLM; no API key needed
- **YAML suite format** — define cases with `mock_response`, optional `memory` seed, `expected` (substring or `exact: true`), and `skip` flag
- **`--update` / `-u`** — capture first run outputs as the golden baseline (writes back to YAML)
- **`--fail-fast` / `-x`** — stop on first failure, like pytest
- **Auto-creates starter suite** if `<agent>.eval.yaml` doesn't exist yet — one-command bootstrap
- Captures output from `run()` return value or from `memory["result"]` / `memory["output"]` / `memory["answer"]`
- Full pass/fail/skip summary with duration per case
- `aios/eval/runner.py` — EvalResult dataclass, mock LLM builder, suite runner with optional PyYAML support

### Added — MCP Server (`aios mcp`)
- **`aios mcp <agent.py> [--port 3000]`** — expose any Ai.os agent as an MCP tool server (HTTP/SSE transport); add to Claude Desktop or Cursor with a single JSON config line
- **`aios mcp <agent.py> --stdio`** — stdio transport for direct subprocess invocation by MCP hosts; zero extra dependencies
- Agents with a typed `run(query: str)` signature automatically expose their parameters as MCP input schema fields; agents with no params get a generic `prompt` input
- Auto-derives tool `description` from the agent class's `description` attribute; overridable with `--description`
- `aios doctor` now checks for `uvicorn` (HTTP transport) and `websockets` (remote logs)
- `pyproject.toml` gains `[mcp]` and `[secrets]` optional extras

### Added — Agent versioning
- **`aios snapshot <name> [--tag TAG]`** — save a named memory snapshot (SQLite `agent_snapshots` table); default tag is a UTC timestamp
- **`aios snapshots <name>`** — list all snapshots with age and size
- **`aios rollback <name> <tag> [--yes]`** — restore long-term memory from a snapshot; prompts for confirmation unless `--yes`

### Added — Cost tracking
- **`aios stats --cost`** — new `Est. cost` column with per-agent USD estimate based on Claude Sonnet 4.6 pricing ($3/M input · $15/M output); total cost shown in summary header
- Graceful fallback: if per-direction token counts aren't stored, uses a 70/30 input/output split on `total_tokens`

### Fixed — Web UI
- Export dropdown replaces the broken flat export bar (wrong border, bad margins)
- Export functions now correctly reference `selected` (was `selectedAgent` — exports were silently broken)
- Workflow builder tab removed (half-baked canvas cluttered the UI; backend API endpoints preserved)
- Export menu closes on outside-click

### Added — Encrypted secrets — `aios.secrets.SecretsStore`: Fernet-encrypted key-value store backed by `~/.aios/secrets.db`; master key auto-generated at `~/.aios/master.key` (chmod 600 on Unix)
- **`aios secrets set NAME VALUE`** — encrypt and store a secret
- **`aios secrets get NAME`** — decrypt and print a secret
- **`aios secrets list`** — list stored secret names (never the values)
- **`aios secrets delete NAME`** — remove a secret
- **`aios secrets import .env`** — bulk-import variables from a `.env` file into the encrypted store
- **Automatic secrets injection** — `Agent._bootstrap()` calls `SecretsStore.inject_to_env()` at startup, so agents receive secrets without plain-text `.env` files in production; silently skipped when `cryptography` is not installed
- **`aios doctor`** — now checks for the `cryptography` package and reports secrets DB status (path + secret count)

- `aios export <name>` — dump an agent's long-term memory and timeline to a portable JSON file
- `aios export --no-timeline` — exclude timeline events from export
- `aios import <name> <file>` — restore memory from an export; defaults to merge mode
- `aios import --replace` — wipe existing memory before importing
- **Trace tab** in web dashboard — shows every cached tool call from the latest run (tool name, args hash, timestamp) with crash-recovery explanation
- `/api/agents/{name}/checkpoints` REST endpoint (latest run; optional `?run_id=` param)
- `examples/triage.py` — Linear issue triage agent with 15-minute schedule, seen-issue dedup, Slack summary
- Tests for export/import in `tests/test_cli_export_import.py`

### Added — Stats & Token Tracking
- **`aios stats`** — rich aggregate dashboard: all agents in one table with total runs, success rate, average duration, total tokens consumed, memory key count, and last-run time
- **Token & LLM call tracking** — every `think()` / `think_with_tools()` call records `prompt_tokens`, `completion_tokens`, `total_tokens`, and `llm_calls` to the `agent_runs` SQLite table
- **`max_tokens_per_run`** class variable — set a hard token budget per run (e.g. `max_tokens_per_run = 50_000`); agent raises cleanly when exceeded so cost can't spiral
- **History tab in web UI** now shows Tokens and LLM calls columns per run, plus a total-tokens footer
- **Prometheus `/metrics`** now includes per-agent token data from the DB

### Added — Triggers & Publishing
- **`@trigger("webhook")`** — event-driven agents that respond to HTTP webhooks (GitHub, Stripe, Slack, etc.); starts a pure-asyncio HTTP server, no extra deps
  - `path`, `port` options to configure endpoint
  - `secret="env:VAR"` for HMAC-SHA256 signature verification (GitHub-compatible)
  - Payload auto-parsed from JSON body; passed as first arg to `run(payload)`
  - `examples/github_reviewer.py` — full PR review agent triggered by GitHub webhooks
- **`aios publish <agent.py>`** — scaffold a distributable pip package (`pyproject.toml`, `__init__.py`, `__main__.py`, `README.md`)
  - `pip install aios-myagent` + `python -m aios_myagent` to run from anywhere
  - `--push` flag: build with `build` + upload with `twine` in one command

### Added — Deployment & Resilience
- **`aios deploy`** — generate a production-ready deployment bundle:
  - `--platform docker` (default): `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `deploy.sh` with persistent volume for memory/checkpoints
  - `--platform fly`: `fly.toml` + `Dockerfile` for one-command Fly.io deploys
  - `--platform systemd`: systemd `.service` unit + `install-service.sh` for Linux VPS hosting
- **`@tool(retries=N, backoff=s)`** — automatic exponential-backoff retry on tool exceptions; schema is correctly inferred from the original function signature before wrapping

### Added — Observability
- **Alert webhooks** — set `AIOS_ALERT_WEBHOOK=<url>` to receive a POST when any agent crashes; payload includes agent name, run ID, error summary, and full traceback
- **Prometheus `/metrics` endpoint** — scrape `http://localhost:8000/metrics` to get `aios_agent_running`, `aios_agent_runs_total`, `aios_agent_runs_completed_total`, `aios_agent_runs_failed_total`, `aios_agent_memory_keys`, `aios_agent_checkpoints_total` per agent
- Works with any Prometheus-compatible scraper (Grafana, Datadog agent, VictoriaMetrics, etc.)

### Added — Memory API
- **`await self.memory.search(query, limit=10)`** — case-insensitive substring search across all long-term memory keys and values. Returns `[{"key", "value", "updated_at"}, ...]` ordered by most-recently-updated. Useful when agents need to find relevant context without knowing the exact key name.

### Added — Tool decorator
- **`@tool(cache_ttl=N)`** — cache tool results in memory for N seconds. Identical args → instant replay within the TTL; cache is per-instance and not persisted. Compose freely with `retries` and `backoff`: `@tool(retries=3, cache_ttl=60)`.

### Added — Agent API
- **`self.logger`** — every agent now has a pre-wired `logging.Logger` at `aios.<name>`, available after `_bootstrap()`. Use `self.logger.info(...)` / `.warning(...)` / `.debug(...)` directly in `run()` and tools — no import needed. Logs appear in `aios logs <name>` and the web UI.
- **`aios test --watch` / `-w`** — auto-reruns the dry-run whenever the agent file changes on disk; great for TDD. Ctrl-C to stop.
- **`aios cp <source> <dest>`** — clone an agent: copies long-term memory and timeline to a new name with a fresh UUID and empty run history. `--no-memory` and `--no-timeline` flags let you control what transfers.
- **`aios timeline <name>`** — show the append-only event log from the CLI (previously web-UI only). Supports `--type` to filter by event type and `--limit` for row count.
- **`aios runs <name>`** — dedicated run history command showing run ID, status, start time, duration, total tokens, and LLM call count in a table. `--failed` filters to failed runs only and prints the last error message; `--limit N` controls row count.
- **`aios memory <name> --set` / `--delete`** — write or remove memory keys directly from the CLI without writing Python. Values may be JSON or plain strings. Useful for seeding agents with data or resetting state during debugging.

### Changed — Web UI (major polish pass)
- **Dark/light theme toggle** — 🌙/☀️ button in header, persisted to `localStorage`
- **ANSI escape code stripping** — terminal color codes no longer appear in the log viewer (stripped server-side in both `/logs` and `/logs/stream`)
- **Relative timestamps everywhere** — all timestamps now show "2m ago", "just now", "3h ago" etc. with the absolute time as a tooltip/sub-line; History table, Memory table, Trace table, and Timeline all updated
- **LiteLLM noise filter** — "Hide noise" toggle (on by default) hides verbose LiteLLM framework lines so you can focus on your agent's output; click to reveal them dimmed
- **Tab count badges** — pill counters inside each tab label
- **Log search highlight fix** — noise-hidden lines excluded from filter results correctly
- **Light theme CSS variables** — full light palette for all surfaces, borders, text, badges, and the log box
- **Agent sidebar count pill** — styled rounded badge instead of plain text
- **Improved empty states** — all tabs include a code hint for how to populate them

### Added — TypeScript / Node.js SDK (`sdk/typescript/`)
- Full TypeScript SDK mirroring the Python API: `Agent`, `MemoryStore`, `CheckpointEngine`, `@tool`, `@schedule`
- **Shared SQLite persistence** — same DB schema as the Python runtime; a Python agent and a TypeScript agent with the same `name` share one database transparently
- `think()` / `thinkWithTools()` — Anthropic SDK driven agentic loop
- `Agent.launch()` — respects `@schedule` decorator and loops automatically
- `examples/researcher.ts` — complete example agent

### Added — Team Workspaces
- **`aios workspace init <name> --dir <path>`** — configure a shared directory (network share, Dropbox, NFS, etc.) as the team workspace
- **`aios workspace push <agent>`** — serialise an agent's memory + timeline to `<dir>/agents/<name>.json`
- **`aios workspace pull <agent>`** — restore from the workspace (merge or `--replace`)
- **`aios workspace list`** — list agents in the workspace with key/event counts
- Works with any shared filesystem; no server required

### Added — Remote Log Streaming
- **WebSocket endpoint** `ws://<host>:8000/ws/agents/<name>/logs` — streams live log lines to any WS client; ANSI stripped
- **`aios logs <name> --remote ws://<host>:8000`** — watch logs from a cloud-hosted agent without SSH; requires `pip install websockets`

### Added — Web UI Exports & Visual Builder
- **Export bar** in dashboard — one-click download buttons for Runs CSV, Memory CSV, Timeline CSV, Full JSON report, and Print/PDF
- **Workflow Builder tab** — visual canvas editor: add nodes (start, llm, tool, memory_read, memory_write, end), drag to position, connect edges, inspect/edit node properties
- **`POST /api/workflow/{name}/export_python`** — convert a saved workflow graph to a runnable Python agent class
- **`GET /api/agents/{name}/export/runs.csv`** — download run history as CSV
- **`GET /api/agents/{name}/export/memory.csv`** — download memory as CSV
- **`GET /api/agents/{name}/export/timeline.csv`** — download timeline as CSV
- **`GET /api/agents/{name}/export/report.json`** — full agent snapshot (runs + memory + timeline) as JSON
- **Print/PDF** — `@media print` stylesheet hides UI chrome; use browser "Save as PDF"

---

## [0.2.0] — 2026-06-28

### Added

**Built-in tool mixins** (all new)
- `GitHubMixin` — repos, issues, PRs, code search, file reading (`GITHUB_TOKEN`)
- `SlackMixin` — send messages, rich blocks, read channels, reply to threads, react with emoji (`SLACK_BOT_TOKEN`)
- `DiscordMixin` — webhook messages and embeds (no bot setup), full bot API for channel reads and threads (`DISCORD_WEBHOOK_URL` or `DISCORD_BOT_TOKEN`)
- `PostgresMixin` — parameterized queries, execute, list tables, schema inspection, safe row counts (`POSTGRES_URL` + `asyncpg`)
- `EmailMixin` — plain text and HTML email via SMTP, supports Gmail, SendGrid, Outlook and any standard SMTP server, no extra dependencies (`EMAIL_*` env vars)

**Web UI overhaul** (`aios/web/app.py`)
- Log line colorization by level (error=red, warning=amber, success=green, debug=dim)
- Live filter: search box filters log lines client-side in real time
- Pulsing green dot animation on running agents
- Run history now shows duration column (e.g. "2m 15s")
- Auto-scroll detection — scrolling up pauses auto-scroll, returning to bottom re-enables it
- Live badge on log panel when agent is running via SSE
- Header shows total running agent count at all times
- Better empty state for sidebar and panel

**CLI: `aios doctor` improvements**
- New "Tool mixin credentials" section — shows `GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, `POSTGRES_URL`, `EMAIL_ADDRESS` with warnings if unset
- Now checks for `asyncpg` as optional package with install hint
- Cleaner section headers for LLM vs mixin credentials

**Examples**
- `examples/notifier.py` — multi-channel alert agent using `SlackMixin + DiscordMixin + EmailMixin + @schedule`, deduplicates alerts across runs using long-term memory

### Fixed
- `examples/researcher.py` — corrupted UTF-8 encoding (garbled em-dashes and check marks caused by Windows UTF-16 BOM)
- `.env.example` — rewritten to fix UTF-16 BOM encoding corruption

### Changed
- `__version__` bumped to `0.2.0`
- `pyproject.toml` — added `[postgres]` and `[all]` optional dependency extras

---

## [0.1.0] — 2026-06-28

First public release.

### Added

**Core runtime**
- `Agent` base class — persistent identity, memory, tools, crash recovery in one class
- `@tool` decorator — schema inferred automatically from type hints and docstrings
- `@schedule("every Xh")` decorator — agent repeats on a cron/interval, survives restarts
- `Agent.think()` — single-shot LLM call
- `Agent.think_with_tools()` — agentic loop, LLM selects and calls tools until done
- `Agent.call_agent()` — blocking call to another agent class, returns text
- `Agent.spawn_agent()` — fire-and-forget agent in background task
- `Agent.on_start()` / `Agent.on_stop()` — lifecycle hooks
- `Agent.launch()` — entry point, auto-loads `.env`, validates API key

**Checkpoint engine** (`aios/runtime/checkpoint.py`)
- Caches tool call results by `(run_id, tool_name, args_hash)` in SQLite
- On restart, `run()` re-executes but completed tool calls replay from cache instantly
- Agent fast-forwards to the first uncompleted call and continues from there
- No manual checkpoints required

**Memory** (`aios/memory/store.py`)
- Short-term: in-process key/value, cleared each run
- Long-term: SQLite-persisted key/value, survives forever
- Timeline: append-only event log per agent

**Identity** (`aios/identity/core.py`)
- Persistent UUID per agent name — same agent, same ID across restarts
- Tracks name, model, version, config

**Model router** (`aios/models/router.py`)
- Any model in one line: Claude, GPT-4o, Gemini, Mistral, Ollama
- Powered by [litellm](https://github.com/BerriAI/litellm)
- Streaming support via `ModelRouter.stream()`

**Built-in tool mixins** (`aios/tools/builtin/`)
- `WebSearchMixin` — `web_search()` (DuckDuckGo, no key), `fetch_url()` with HTML stripping
- `FilesystemMixin` — `read_file()`, `write_file()`, `list_directory()`, `delete_file()`, `file_exists()` with cwd sandboxing
- `ShellMixin` — `run_command()` (allowlist support), `run_python()` with timeout
- `HttpMixin` — `http_get()`, `http_post()`, `http_put()`, `http_delete()`

**CLI** (`aios` command)
- `aios init [name]` — scaffold new agent project
- `aios run <file>` — run agent (foreground, `--detach`, `--watch`)
- `aios list` — list all agents with status
- `aios status <name>` — status + run history
- `aios logs <name>` — show logs (`-f` to follow, cross-platform)
- `aios stop <name>` — stop agent
- `aios restart <name>` — stop + restart (resumes from checkpoint)
- `aios memory <name>` — inspect long-term memory
- `aios ui` — open web dashboard
- `aios version` — show version

**Web UI** (`aios/web/`)
- FastAPI backend with SSE log streaming
- Live dashboard: running agents, logs, memory inspector, run history
- Auto-refreshes every 5 seconds

**Config** (`aios/config.py`)
- Auto-discovers and loads `.env` from cwd upward
- Validates API key for the configured model on startup
- Clear error messages with setup instructions when keys are missing

**Testing**
- 54 tests across 5 test files
- `pytest-asyncio` for async tests
- Covers: memory store, checkpoint engine (including crash-resume), tool registry, agent lifecycle, built-in tools, scheduling, config

**Project**
- `pyproject.toml` with hatchling build, ruff, mypy config
- GitHub Actions CI: test (3.10/3.11/3.12), lint (ruff), type check (mypy)
- `CONTRIBUTING.md` with dev setup, commit conventions, testing guide
- `.env.example` with all supported variables
- MIT license

---

[Unreleased]: https://github.com/aios-runtime/aios/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/aios-runtime/aios/releases/tag/v0.1.0
