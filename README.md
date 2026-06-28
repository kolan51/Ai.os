# Ai.os

[![CI](https://github.com/kolan51/Ai.os/actions/workflows/ci.yml/badge.svg)](https://github.com/kolan51/Ai.os/actions)
[![PyPI version](https://img.shields.io/pypi/v/aios-runtime.svg?cacheSeconds=60)](https://pypi.org/project/aios-runtime/)
[![Python versions](https://img.shields.io/pypi/pyversions/aios-runtime.svg?cacheSeconds=60)](https://pypi.org/project/aios-runtime/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Persistent agent runtime. Deploy agents that remember, survive crashes, and run forever.**

```python
from aios import Agent, tool

class ResearchAgent(Agent):
    name = "researcher"
    model = "claude-sonnet-4-6"

    @tool
    async def search_web(self, query: str) -> str:
        """Search the web. query: What to search for."""
        ...

    async def run(self):
        results = await self.search_web("latest AI papers")
        await self.memory.save("results", results)  # persists across restarts

if __name__ == "__main__":
    ResearchAgent.launch()
```

```bash
aios run researcher.py --detach   # run in background
aios logs researcher -f           # stream live logs
aios memory researcher            # inspect what it remembers
aios restart researcher           # restart — picks up where it left off
aios ui                           # open web dashboard
```

---

## Why Ai.os

Every agent framework makes you solve the same problems from scratch:

| Problem                | Without Ai.os         | With Ai.os                                  |
| ---------------------- | --------------------- | ------------------------------------------- |
| Agent crashes          | Loses all context     | Resumes from last tool call                 |
| Cross-run memory       | DIY SQLite / Redis    | `await self.memory.save("key", value)`      |
| Swap models            | Rewrite your code     | Change one line: `model = "gpt-4o"`         |
| Inspect running agents | Print statements      | Web UI + `aios logs -f`                     |
| Recurring tasks        | Cron job + state file | `@schedule("every 6h")`                     |
| Agent calls agent      | Manual subprocess     | `await self.call_agent(ResearchAgent, ...)` |

---

## Install

```bash
pip install aios-runtime
```

With Postgres support:

```bash
pip install "aios-runtime[postgres]"
```

Requires Python 3.10+. No Docker, no Redis, no external services.

---

## Quickstart

```bash
aios init myagent          # scaffold a new agent project
cd myagent
echo "ANTHROPIC_API_KEY=your-key" >> .env
aios run myagent.py        # run it
```

---

## Core API

### Agent base class

```python
from aios import Agent, tool, schedule

class MyAgent(Agent):
    name = "myagent"             # persistent identity key
    model = "claude-sonnet-4-6"  # or "gpt-4o", "ollama/llama3"
    version = "1.0.0"
    description = "What this agent does"
    system_prompt = "You are..."
    temperature = 0.7
    config = {"key": "value"}    # arbitrary config, persisted

    @tool
    async def my_tool(self, input: str) -> str:
        """Tool description shown to the LLM. input: What it means."""
        return f"result: {input}"

    async def run(self) -> None:
        ...

if __name__ == "__main__":
    MyAgent.launch()
```

### Memory

```python
# Short-term (cleared each run)
self.memory.set("draft", "...")
value = self.memory.get("draft", default=None)
self.memory.clear()

# Long-term (persisted forever across runs)
await self.memory.save("report", {"rows": 42})
result = await self.memory.load("report", default=None)
await self.memory.delete("report")
keys = await self.memory.keys()
all_data = await self.memory.all()

# Search (case-insensitive substring match on keys and values)
results = await self.memory.search("climate", limit=5)
# → [{"key": "finding:climate", "value": {...}, "updated_at": "..."}]

# Timeline (append-only event log)
await self.memory.log_event("analysis_complete", {"rows": 1200})
events = await self.memory.timeline(limit=100)
```

### Crash recovery

Before each `@tool` call, the result is cached in SQLite. If the agent crashes and restarts, `run()` re-executes from the top — but every completed tool call returns its cached result instantly. The agent fast-forwards to the first uncompleted call and continues.

No manual checkpoints. No configuration. It just works.

### Webhook triggers

Make an agent respond to external HTTP events instead of running once:

```python
from aios import Agent, trigger

class GitHubReviewAgent(Agent):
    name = "gh_reviewer"
    model = "claude-sonnet-4-6"

    @trigger("webhook", path="/github", port=8080, secret="env:GITHUB_WEBHOOK_SECRET")
    async def run(self, payload: dict) -> None:
        pr = payload.get("pull_request", {})
        if not pr:
            return
        review = await self.think(f"Review this PR: {pr['title']}")
        await self.memory.save(f"review_{pr['number']}", review)

if __name__ == "__main__":
    GitHubReviewAgent.launch()
```

Point any webhook source at `http://your-host:8080/github`. Works with GitHub, Stripe, Slack slash commands, or any HTTP sender. HMAC-SHA256 signature verification built in.

### Tool retries

```python
@tool(retries=3, backoff=2.0)
async def fetch_data(self, url: str) -> str:
    """Fetch from an external API. url: endpoint to call."""
    # Automatically retried up to 3 times on exception.
    # Wait: 2s → 4s → 8s (exponential backoff).
    return await self.http_get(url)
```

### Tool result caching

```python
@tool(cache_ttl=60)
async def get_price(self, symbol: str) -> str:
    """Get current price. symbol: ticker to look up."""
    # Same args → same result returned instantly for 60 seconds.
    # Saves API calls and speeds up repeated LLM loops.
    return await self.http_get(f"https://api.example.com/price/{symbol}")
```

Combine with retries: `@tool(retries=3, backoff=2.0, cache_ttl=300)`

### Scheduling

```python
class DailyAgent(Agent):
    @schedule("every 24h")       # or "every 30m", "every 1d"
    async def run(self):
        await self.do_daily_work()
```

Next-run time persists in memory — correct schedule survives restarts.

### LLM calls

```python
# Single-shot, no tools
text = await self.think("Summarize this: ...")

# Agentic loop — LLM selects and calls tools until done
answer = await self.think_with_tools(
    "Research X, save findings, return a summary",
    max_iterations=10,
)
```

### Agent-to-agent

```python
# Blocking — call another agent, get text back
summary = await self.call_agent(ResearchAgent, "summarize the findings on X")

# Fire-and-forget — spawn in background
await self.spawn_agent(MonitorAgent)
```

### Lifecycle hooks

```python
async def on_start(self) -> None:
    """Called once before run(). Setup work goes here."""

async def on_stop(self) -> None:
    """Called after run() completes or crashes. Cleanup goes here."""
```

### Logging

Every agent has a `self.logger` (a standard `logging.Logger`) pre-configured with the agent's name:

```python
async def run(self) -> None:
    self.logger.info("Starting run")
    self.logger.debug("Fetching %s", url)
    self.logger.warning("Rate limit hit, backing off")
```

Logs appear in `aios logs <name>` and the web UI log viewer.

---

## Multi-model

Change one line — no other code changes:

```python
model = "claude-sonnet-4-6"      # Anthropic
model = "claude-opus-4-8"        # Anthropic (most capable)
model = "gpt-4o"                 # OpenAI
model = "gpt-4o-mini"            # OpenAI (fast + cheap)
model = "gemini/gemini-pro"      # Google
model = "mistral/mistral-large"  # Mistral
model = "ollama/llama3"          # Local — no API key needed
model = "ollama/mistral"         # Local Mistral
```

Powered by [litellm](https://github.com/BerriAI/litellm) — any OpenAI-compatible endpoint works.

---

## CLI reference

| Command                          | Description                             |
| -------------------------------- | --------------------------------------- |
| `aios init [name]`               | Scaffold a new agent project (basic template) |
| `aios init [name] -t scheduled`  | Scaffold with a scheduled-run template  |
| `aios init [name] -t research`   | Scaffold with a web research template   |
| `aios init [name] -t notifier`   | Scaffold with a Slack notifier template |
| `aios init [name] -t webhook`    | Scaffold a webhook-triggered agent      |
| `aios init --list-templates`     | List all available templates            |
| `aios run agent.py`         | Run in foreground                 |
| `aios run agent.py -d`      | Run in background                 |
| `aios run agent.py -w`      | Run, restart on file change       |
| `aios list`                 | List all agents                   |
| `aios status <name>`        | Show status + run history         |
| `aios runs <name>`          | Full run history (duration, tokens, errors) |
| `aios runs <name> --failed` | Show only failed runs             |
| `aios cp <src> <dst>`       | Clone agent (copy memory + timeline) |
| `aios timeline <name>`      | Show event timeline               |
| `aios timeline <name> -t run_complete` | Filter by event type  |
| `aios logs <name>`          | Show recent logs                  |
| `aios logs <name> -f`       | Stream logs live                  |
| `aios stop <name>`          | Stop agent                        |
| `aios restart <name>`       | Restart (resumes from checkpoint) |
| `aios memory <name>`        | Inspect long-term memory          |
| `aios memory <name> -k key` | Show one memory key in full       |
| `aios memory <name> -k key --set '{"x":1}'` | Write a memory key |
| `aios memory <name> -k key --delete` | Delete a memory key       |
| `aios export <name>`        | Export memory to JSON             |
| `aios export <name> -o f.json` | Export to specific file         |
| `aios import <name> f.json` | Import (merge) memory from JSON   |
| `aios import <name> f.json --replace` | Import, wiping existing memory |
| `aios ui`                   | Open web dashboard                |
| `aios stats`                | Aggregate stats — runs, success rate, tokens, avg duration |
| `aios publish <agent.py>`   | Scaffold a pip-installable package from an agent |
| `aios publish <agent.py> --push` | Build + upload to PyPI            |
| `aios deploy [file]`        | Generate Docker/Fly.io/systemd deploy bundle |
| `aios deploy -p fly`        | Fly.io config + Dockerfile        |
| `aios deploy -p systemd`    | Linux systemd service unit        |
| `aios test agent.py`        | Dry-run agent (mock LLM calls)    |
| `aios test agent.py -w`     | Auto-rerun on file change         |
| `aios secrets set NAME VAL` | Encrypt and store a secret        |
| `aios secrets get NAME`     | Decrypt and print a secret        |
| `aios secrets list`         | List stored secret names          |
| `aios secrets delete NAME`  | Remove a secret                   |
| `aios secrets import .env`  | Bulk-import a .env file into the encrypted store |
| `aios version`              | Show version                      |

---

## Environment variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

```bash
ANTHROPIC_API_KEY=...        # claude-* models
OPENAI_API_KEY=...           # gpt-* models
GOOGLE_API_KEY=...           # gemini-* models
AIOS_LOG_LEVEL=INFO          # DEBUG | INFO | WARNING | ERROR
AIOS_ALERT_WEBHOOK=https://… # POST here when an agent crashes (Slack/Discord/custom)
```

Ai.os auto-loads `.env` on `Agent.launch()`. Local Ollama needs no key.

### Encrypted secrets (production)

For production deployments where you don't want plain-text `.env` files, use the built-in encrypted secrets store:

```bash
pip install cryptography          # one-time install

aios secrets set OPENAI_API_KEY sk-...
aios secrets set SLACK_BOT_TOKEN xoxb-...
aios secrets import .env          # or bulk-import an existing .env file
aios secrets list                 # see stored names (never values)
```

Secrets are encrypted with [Fernet](https://cryptography.io/en/latest/fernet/) symmetric encryption.
The master key lives at `~/.aios/master.key` (chmod 600 on Unix) and encrypted values at `~/.aios/secrets.db`.

Every agent automatically calls `SecretsStore.inject_to_env()` at startup — secrets are merged into
`os.environ` (existing variables are never overwritten).  Agents require zero code changes; just stop
shipping the `.env` file and store secrets once with `aios secrets set`.

Scrape Prometheus metrics from the web dashboard:

```
http://localhost:8000/metrics
```

---

## Built-in tool mixins

Mix in capabilities by inheriting alongside `Agent`:

```python
from aios import Agent, WebSearchMixin, FilesystemMixin, SlackMixin, PostgresMixin

class AnalystAgent(Agent, WebSearchMixin, FilesystemMixin, SlackMixin, PostgresMixin):
    name = "analyst"
    model = "claude-sonnet-4-6"

    async def run(self):
        rows = await self.pg_query("SELECT * FROM orders WHERE status = $1", ["pending"])
        summary = await self.think(f"Summarize these {len(rows)} orders: {rows[:5]}")
        await self.slack_send_message("#data-team", summary)
        await self.write_file("report.md", summary)
```

| Mixin | Tools | Requires |
| --- | --- | --- |
| `WebSearchMixin` | `web_search`, `fetch_url` | nothing (uses DuckDuckGo) |
| `FilesystemMixin` | `read_file`, `write_file`, `list_directory`, `delete_file`, `file_exists` | nothing |
| `ShellMixin` | `run_command`, `run_python` | nothing |
| `HttpMixin` | `http_get`, `http_post`, `http_put`, `http_delete` | nothing |
| `GitHubMixin` | `github_get_repo`, `github_list_issues`, `github_create_issue`, `github_list_prs`, `github_search_code`, `github_get_file` | `GITHUB_TOKEN` |
| `SlackMixin` | `slack_send_message`, `slack_send_blocks`, `slack_get_messages`, `slack_list_channels`, `slack_reply_to_thread`, `slack_add_reaction` | `SLACK_BOT_TOKEN` |
| `PostgresMixin` | `pg_query`, `pg_execute`, `pg_list_tables`, `pg_table_schema`, `pg_count` | `POSTGRES_URL` + `pip install asyncpg` |
| `EmailMixin` | `send_email`, `send_html_email` | `EMAIL_SMTP_HOST`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD` |
| `DiscordMixin` | `discord_send`, `discord_send_embed`, `discord_send_message`, `discord_get_messages`, `discord_create_thread` | `DISCORD_WEBHOOK_URL` (webhook) or `DISCORD_BOT_TOKEN` (full API) |
| `NotionMixin` | `notion_search`, `notion_get_page`, `notion_get_page_content`, `notion_append_block`, `notion_create_page`, `notion_query_database` | `NOTION_TOKEN` |
| `LinearMixin` | `linear_get_issues`, `linear_get_issue`, `linear_create_issue`, `linear_update_issue`, `linear_add_comment`, `linear_list_teams` | `LINEAR_API_KEY` |

---

## Examples

| File                                               | What it shows                                           |
| -------------------------------------------------- | ------------------------------------------------------- |
| [`examples/researcher.py`](examples/researcher.py) | Web research, persistent knowledge base, crash recovery |
| [`examples/monitor.py`](examples/monitor.py)       | URL monitoring, uptime history, scheduling              |
| [`examples/coder.py`](examples/coder.py)           | Write code, run tests, iterate until passing            |
| [`examples/notifier.py`](examples/notifier.py)     | Multi-channel alerts (Slack + Discord + Email), dedup across runs |
| [`examples/triage.py`](examples/triage.py)         | Linear issue triage on a schedule — classify, dedup, post Slack summary |
| [`examples/github_reviewer.py`](examples/github_reviewer.py) | GitHub PR reviewer via `@trigger("webhook")` — HMAC-verified |

---

## TypeScript / Node.js SDK

Same API, same persistence layer:

```typescript
import { Agent, tool, schedule } from '@aios/sdk';

class ResearchAgent extends Agent {
  static name = 'researcher';
  static model = 'claude-sonnet-4-6';

  @tool({ description: 'Save a finding' })
  async saveFinding(topic: string, summary: string): Promise<string> {
    this.memory.save(`finding:${topic}`, { summary });
    return `Saved: ${topic}`;
  }

  async run(): Promise<void> {
    const result = await this.thinkWithTools('Research AI agents and save findings.');
    this.memory.save('last_run', result);
  }
}

ResearchAgent.launch();
```

```bash
cd sdk/typescript
npm install
npx ts-node examples/researcher.ts
```

A Python agent and a TypeScript agent with the same `name` share one SQLite database — memory persists across both runtimes.

---

## Team Workspaces

Share agent memory across machines without a server:

```bash
# On machine A (set up once)
aios workspace init myteam --dir /mnt/shared/aios   # or ~/Dropbox/aios

# Push agent state to the shared dir
aios workspace push researcher

# On machine B
aios workspace init myteam --dir /mnt/shared/aios
aios workspace pull researcher                       # merge
aios workspace pull researcher --replace             # overwrite
aios workspace list                                  # see what's available
```

Works with any shared filesystem — NFS, Dropbox, Google Drive, S3 mounted via s3fs, etc.

---

## Encrypted Secrets (Production)

Stop putting API keys in plain-text `.env` files:

```bash
aios secrets set ANTHROPIC_API_KEY sk-ant-...
aios secrets set SLACK_BOT_TOKEN xoxb-...
aios secrets list                              # shows names only, never values
aios secrets import .env                       # bulk import from existing .env
aios secrets get ANTHROPIC_API_KEY             # decrypt + print
```

Secrets are Fernet-encrypted in `~/.aios/secrets.db`. Agents automatically receive them at startup — no code changes needed.

```bash
pip install "aios-runtime[secrets]"   # or: pip install cryptography
```

---

## Remote Log Streaming

Watch a cloud-hosted agent's logs from your laptop:

```bash
# Stream logs from a remote Ai.os web UI
aios logs myagent --remote ws://my-server:8000

# Or connect directly with any WebSocket client
wscat -c ws://my-server:8000/ws/agents/myagent/logs
```

---

## vs. alternatives

|                   | Ai.os          | LangGraph | CrewAI | Temporal       |
| ----------------- | -------------- | --------- | ------ | -------------- |
| Crash recovery    | ✅ automatic   | ❌ manual | ❌     | ✅ but complex |
| Persistent memory | ✅ built-in    | ❌ DIY    | ❌ DIY | ❌ DIY         |
| Zero-config start | ✅             | ❌        | ✅     | ❌             |
| Web UI            | ✅             | ❌        | ❌     | ✅             |
| Multi-model       | ✅             | ✅        | ✅     | ❌             |
| Scheduling        | ✅ `@schedule` | ❌        | ❌     | ✅             |
| Agent-to-agent    | ✅             | ✅        | ✅     | ❌             |
| Local LLMs        | ✅ Ollama      | ✅        | ✅     | ❌             |
| Learning curve    | Low            | High      | Low    | High           |

---

## Roadmap

**v0.1 — Foundation** ✅
- [x] Agent persistence + crash recovery
- [x] Short-term + long-term memory
- [x] `@tool` decorator with auto schema
- [x] Multi-model routing (Claude, GPT-4o, Gemini, Ollama, any OpenAI-compatible)
- [x] CLI (run / list / logs / stop / restart / memory / doctor)
- [x] Web UI dashboard with live log streaming
- [x] `@schedule` decorator with persistent next-run
- [x] Agent-to-agent calls (`call_agent` / `spawn_agent`)
- [x] `aios init` project scaffolding
- [x] Built-in tool library (Web search, Filesystem, Shell, HTTP, GitHub)

**v0.2 — SDKs & Ecosystem** ✅
- [x] Slack, Discord, Email tool mixins
- [x] Postgres, Notion, Linear tool mixins
- [x] `aios export` / `aios import` — memory backup and migration
- [x] Web UI — dark/light theme, relative timestamps, ANSI stripping, noise filter, Trace tab
- [x] TypeScript / Node.js SDK — same API, same SQLite persistence layer (`sdk/typescript/`)
- [x] `aios publish` — share agents as pip-installable packages

**v0.3 — Observability** ✅
- [x] Timeline tab in web UI
- [x] Tool call Trace tab (see exactly which tools were cached for crash recovery)
- [x] Prometheus metrics export — `/metrics` endpoint, scrape with Grafana / Datadog
- [x] Alert webhooks on agent failure — `AIOS_ALERT_WEBHOOK=<url>`

**v0.4 — Cloud** ✅
- [x] `aios deploy` — generate production deploy bundle (Docker Compose, Fly.io, systemd)
- [x] `@tool(retries=N, backoff=s)` — automatic exponential-backoff retry on tool failure
- [x] `aios publish` — scaffold and upload agents as pip-installable packages
- [x] `@trigger("webhook")` — event-driven agents; respond to GitHub, Stripe, Slack, etc.
- [x] Team workspaces — `aios workspace push/pull` over any shared directory
- [x] Secrets management — Fernet-encrypted secrets store (`aios secrets`)
- [x] Remote log streaming — WebSocket endpoint + `aios logs --remote`

**v0.5 — Visual Builder** ✅
- [x] Drag-and-drop workflow editor in web UI (Workflow tab — nodes, edges, canvas)
- [x] Export workflow to Python agent class (`Export .py` button)
- [x] CSV / JSON / PDF exports from web UI (Runs, Memory, Timeline, full report)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome.

## License

MIT — see [LICENSE](LICENSE).
