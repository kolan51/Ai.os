# Contributing to Ai.os

Thank you for contributing. This document covers everything you need to go from zero to a merged pull request.

## Setup

```bash
git clone https://github.com/aios-runtime/aios
cd aios
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env           # add your API key
```

Verify everything works:

```bash
pytest tests/        # 37 tests, all green
ruff check aios/     # no lint errors
```

## Project structure

```
aios/
  agent.py          Base Agent class — the core API
  config.py         .env loading, API key validation
  scheduling.py     @schedule decorator
  identity/         Persistent agent UUID and config
  memory/           Short-term + long-term SQLite memory
  models/           litellm model router
  runtime/          Checkpoint engine + process manager
  tools/            @tool decorator and registry
  web/              FastAPI dashboard
  cli/              Typer CLI (aios run/list/logs/…)
tests/
examples/
```

## Making changes

1. Fork the repo and create a branch: `git checkout -b feat/your-feature`
2. Write your code. Write tests for it.
3. Run `pytest tests/` — all tests must pass.
4. Run `ruff check aios/ && ruff format aios/` — no lint errors.
5. Open a pull request against `main`.

## Commit conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add @schedule decorator
fix: agent crash on empty tool response
docs: add multi-agent example
test: cover checkpoint resume semantics
refactor: simplify tool schema inference
```

Keep subject lines under 72 characters. No period at the end.

## Writing tests

Tests live in `tests/`. All tests are async-friendly via `pytest-asyncio`.

```python
async def test_my_feature(tmp_path: Path) -> None:
    store = MemoryStore(agent_id="test-001", db_path=tmp_path / "test.db")
    await store.setup()
    await store.save("key", "value")
    assert await store.load("key") == "value"
```

Rules:
- Every new feature needs at least one test.
- Every bug fix needs a regression test.
- Use `tmp_path` for all file I/O — never write to real `~/.aios` in tests.
- Mock LLM calls — never make real API calls in tests.

## What we accept

**Yes:**
- Bug fixes with regression tests
- Performance improvements with benchmarks
- New built-in tools (GitHub, Slack, Postgres, S3…)
- Documentation improvements
- New example agents

**Discuss first (open an issue):**
- New runtime targets (TypeScript SDK, edge runtime)
- Breaking changes to the Agent API
- New CLI commands
- Web UI changes

**Not now:**
- Visual workflow builder
- Marketplace
- Blockchain
- Mobile runtime

## Code style

- Python 3.10+, `from __future__ import annotations` in every file
- Type hints on all public functions
- No comments that describe *what* the code does — only *why*
- `ruff` for formatting and linting — run `ruff format aios/` before committing
- Function names are verbs, class names are nouns

## Questions

Open an issue or join the Discord. We respond within 24 hours.
