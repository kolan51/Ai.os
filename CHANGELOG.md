# Changelog

All notable changes to Ai.os are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Planned
- Built-in GitHub tool mixin (repos, issues, PRs, search)
- Built-in Postgres tool mixin (query, insert, schema inspection)
- Built-in Slack tool mixin (send message, read channel)
- TypeScript SDK
- `aios deploy` — self-hosted cloud deployment
- Visual workflow builder

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
