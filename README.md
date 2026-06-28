# Ai.os

[![CI](https://github.com/aios-runtime/aios/actions/workflows/ci.yml/badge.svg)](https://github.com/aios-runtime/aios/actions)
[![PyPI](https://img.shields.io/pypi/v/aios-runtime)](https://pypi.org/project/aios-runtime/)
[![Python](https://img.shields.io/pypi/pyversions/aios-runtime)](https://pypi.org/project/aios-runtime/)
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

| Problem | Without Ai.os | With Ai.os |
|---|---|---|
| Agent crashes | Loses all context | Resumes from last tool call |
| Cross-run memory | DIY SQLite / Redis | `await self.memory.save("key", value)` |
| Swap models | Rewrite your code | Change one line: `model = "gpt-4o"` |
| Inspect running agents | Print statements | Web UI + `aios logs -f` |
| Recurring tasks | Cron job + state file | `@schedule("every 6h")` |
| Agent calls agent | Manual subprocess | `await self.call_agent(ResearchAgent, ...)` |

---

## Install

```bash
pip install aios-runtime
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

# Timeline (append-only event log)
await self.memory.log_event("analysis_complete", {"rows": 1200})
events = await self.memory.timeline(limit=100)
```

### Crash recovery

Before each `@tool` call, the result is cached in SQLite. If the agent crashes and restarts, `run()` re-executes from the top — but every completed tool call returns its cached result instantly. The agent fast-forwards to the first uncompleted call and continues.

No manual checkpoints. No configuration. It just works.

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

| Command | Description |
|---|---|
| `aios init [name]` | Scaffold a new agent project |
| `aios run agent.py` | Run in foreground |
| `aios run agent.py -d` | Run in background |
| `aios run agent.py -w` | Run, restart on file change |
| `aios list` | List all agents |
| `aios status <name>` | Show status + run history |
| `aios logs <name>` | Show recent logs |
| `aios logs <name> -f` | Stream logs live |
| `aios stop <name>` | Stop agent |
| `aios restart <name>` | Restart (resumes from checkpoint) |
| `aios memory <name>` | Inspect long-term memory |
| `aios memory <name> -k key` | Show one memory key in full |
| `aios ui` | Open web dashboard |
| `aios version` | Show version |

---

## Environment variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

```bash
ANTHROPIC_API_KEY=...    # claude-* models
OPENAI_API_KEY=...       # gpt-* models
GOOGLE_API_KEY=...       # gemini-* models
AIOS_LOG_LEVEL=INFO      # DEBUG | INFO | WARNING | ERROR
```

Ai.os auto-loads `.env` on `Agent.launch()`. Local Ollama needs no key.

---

## Examples

| File | What it shows |
|---|---|
| [`examples/researcher.py`](examples/researcher.py) | Web research, persistent knowledge base, crash recovery |
| [`examples/monitor.py`](examples/monitor.py) | URL monitoring, uptime history, scheduling |
| [`examples/coder.py`](examples/coder.py) | Write code, run tests, iterate until passing |

---

## vs. alternatives

| | Ai.os | LangGraph | CrewAI | Temporal |
|---|---|---|---|---|
| Crash recovery | ✅ automatic | ❌ manual | ❌ | ✅ but complex |
| Persistent memory | ✅ built-in | ❌ DIY | ❌ DIY | ❌ DIY |
| Zero-config start | ✅ | ❌ | ✅ | ❌ |
| Web UI | ✅ | ❌ | ❌ | ✅ |
| Multi-model | ✅ | ✅ | ✅ | ❌ |
| Scheduling | ✅ `@schedule` | ❌ | ❌ | ✅ |
| Agent-to-agent | ✅ | ✅ | ✅ | ❌ |
| Local LLMs | ✅ Ollama | ✅ | ✅ | ❌ |
| Learning curve | Low | High | Low | High |

---

## Roadmap

- [x] Agent persistence + crash recovery
- [x] Short-term + long-term memory
- [x] `@tool` decorator with auto schema
- [x] Multi-model routing
- [x] CLI (run/list/logs/stop/restart/memory)
- [x] Web UI dashboard
- [x] `@schedule` decorator
- [x] Agent-to-agent calls
- [x] `aios init` scaffolding
- [ ] Built-in tool library (GitHub, Slack, Postgres, S3, web search)
- [ ] TypeScript SDK
- [ ] Self-hosted cloud deployment
- [ ] Visual workflow builder

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome.

## License

MIT — see [LICENSE](LICENSE).
