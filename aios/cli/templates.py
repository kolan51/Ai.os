"""File templates for `aios init`."""

# ── Basic template ─────────────────────────────────────────────────────────────

AGENT_TEMPLATE = '''\
from aios import Agent, tool


class {class_name}(Agent):
    name = "{agent_name}"
    model = "{model}"
    description = "What this agent does."
    system_prompt = "You are a helpful AI agent."

    @tool
    async def example_tool(self, input: str) -> str:
        """
        An example tool — replace with your own.
        input: The input to process.
        """
        return f"processed: {{input}}"

    async def run(self) -> None:
        # Long-term memory persists across restarts
        previous = await self.memory.load("last_result")
        if previous:
            print(f"[{{self.name}}] previous result: {{previous}}")

        result = await self.think_with_tools(
            "Process this example task using the available tools."
        )

        await self.memory.save("last_result", result)
        print(f"[{{self.name}}] done: {{result}}")


if __name__ == "__main__":
    {class_name}.launch()
'''

# ── Scheduled template ────────────────────────────────────────────────────────

SCHEDULED_TEMPLATE = '''\
from aios import Agent, schedule, tool


class {class_name}(Agent):
    name = "{agent_name}"
    model = "{model}"
    description = "Runs on a schedule and remembers results across runs."
    system_prompt = "You are a concise, precise assistant."

    @schedule("every 1h")          # also: "every 30m", "every 24h"
    async def run(self) -> None:
        print(f"[{{self.name}}] starting scheduled run")

        result = await self.think("What is the current state of X? Be brief.")

        # Timeline is an append-only event log
        await self.memory.log_event("run_complete", {{"result": result}})
        await self.memory.save("latest", result)

        print(f"[{{self.name}}] done — result saved to memory")


if __name__ == "__main__":
    {class_name}.launch()
'''

# ── Research template ─────────────────────────────────────────────────────────

RESEARCH_TEMPLATE = '''\
from aios import Agent, WebSearchMixin, FilesystemMixin, tool


TOPICS = [
    "topic one",
    "topic two",
]


class {class_name}(Agent, WebSearchMixin, FilesystemMixin):
    name = "{agent_name}"
    model = "{model}"
    description = "Web researcher — builds a persistent knowledge base."
    system_prompt = (
        "You are a precise research assistant. Synthesize information clearly, "
        "cite sources when available. Save findings concisely."
    )

    @tool
    async def save_finding(self, topic: str, summary: str, sources: list) -> str:
        """
        Persist a research finding to long-term memory.
        topic: Short identifier for this topic.
        summary: Concise summary of what was found.
        sources: List of source URLs or references.
        """
        await self.memory.save(f"finding:{{topic}}", {{"summary": summary, "sources": sources}})
        return f"Saved: {{topic}}"

    async def run(self) -> None:
        for topic in TOPICS:
            if await self.memory.load(f"finding:{{topic}}"):
                print(f"[{{self.name}}] already know: {{topic}}")
                continue

            print(f"[{{self.name}}] researching: {{topic}}")
            await self.think_with_tools(
                f"Research this topic and save your findings:\\n\\nTOPIC: {{topic}}\\n\\n"
                "1. Search the web\\n2. Fetch 1-2 key URLs\\n"
                "3. Synthesize\\n4. Call save_finding",
                max_iterations=8,
            )

        # Write a report
        all_mem = await self.memory.all()
        findings = {{k.replace("finding:", ""): v for k, v in all_mem.items() if k.startswith("finding:")}}
        if findings:
            report = await self.think(
                "Write a concise summary of these research findings:\\n\\n"
                + "\\n\\n".join(f"{{t}}:\\n{{d['summary']}}" for t, d in findings.items())
            )
            await self.write_file("report.md", f"# Research Report\\n\\n{{report}}\\n")
            print(f"[{{self.name}}] report written to report.md")


if __name__ == "__main__":
    {class_name}.launch()
'''

# ── Notifier template ─────────────────────────────────────────────────────────

NOTIFIER_TEMPLATE = '''\
import os
from aios import Agent, SlackMixin, schedule, tool


class {class_name}(Agent, SlackMixin):
    name = "{agent_name}"
    model = "{model}"
    description = "Monitors something and posts Slack alerts."
    config = {{
        "slack_channel": "#alerts",
        "check_urls": ["https://example.com"],
    }}

    @tool
    async def check_endpoint(self, url: str) -> dict:
        """
        HTTP GET a URL and return status.
        url: URL to check.
        """
        import asyncio, httpx
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                t0 = asyncio.get_event_loop().time()
                r = await c.get(url)
                ms = round((asyncio.get_event_loop().time() - t0) * 1000)
                return {{"url": url, "status": r.status_code, "ok": r.status_code < 400, "ms": ms}}
        except Exception as exc:
            return {{"url": url, "status": 0, "ok": False, "error": str(exc)}}

    @schedule("every 5m")
    async def run(self) -> None:
        channel = self.config["slack_channel"]
        urls = self.config["check_urls"]
        results = [await self.check_endpoint(u) for u in urls]
        failures = [r for r in results if not r["ok"]]

        if not failures:
            return

        # Avoid re-alerting on the same failures
        alerted = set(await self.memory.load("alerted_urls") or [])
        new_failures = [r for r in failures if r["url"] not in alerted]
        if not new_failures:
            return

        msg = "\\n".join(f"✗ {{r['url']}} [{{r['status']}}]" for r in new_failures)
        await self.slack_send_message(channel, f"*Alert*\\n{{msg}}")
        await self.memory.save("alerted_urls", [r["url"] for r in failures])
        print(f"[{{self.name}}] alerted {{len(new_failures)}} failure(s)")


if __name__ == "__main__":
    {class_name}.launch()
'''

# ── Env / gitignore ───────────────────────────────────────────────────────────

ENV_TEMPLATE = """\
# {agent_name} — environment variables
ANTHROPIC_API_KEY=your-key-here
# OPENAI_API_KEY=
# GOOGLE_API_KEY=
"""

GITIGNORE_TEMPLATE = """\
__pycache__/
*.pyc
.env
.aios/
*.db
"""

WEBHOOK_TEMPLATE = '''\
from aios import Agent, tool, trigger


class {class_name}(Agent):
    name = "{agent_name}"
    model = "{model}"
    description = "Webhook-triggered agent — responds to HTTP events."
    system_prompt = "You are a helpful AI agent that processes webhook payloads."

    # Listens on http://0.0.0.0:8080/webhook
    # Set WEBHOOK_SECRET in .env for HMAC-SHA256 signature verification.
    @trigger("webhook", path="/webhook", port=8080, secret="env:WEBHOOK_SECRET")
    async def run(self, payload: dict) -> None:
        self.logger.info("Webhook received: %s", list(payload.keys()))

        # Extract what you care about from the payload
        event = payload.get("event", "unknown")
        data  = payload.get("data", {{}})

        # Call the LLM to process the event
        summary = await self.think(
            f"An event of type '{{event}}' arrived with this data: {{data}}. "
            f"Summarise what happened in one sentence."
        )
        self.logger.info("Summary: %s", summary)

        # Persist result to long-term memory
        await self.memory.save(f"last_{{event}}", summary)
        await self.memory.log_event("webhook_processed", {{"event": event}})

    @tool
    async def get_last_event(self, event_type: str) -> str:
        """Retrieve the last processed event of a given type. event_type: e.g. push, pull_request."""
        result = await self.memory.load(f"last_{{event_type}}")
        return result or f"No events of type '{{event_type}}' processed yet."


if __name__ == "__main__":
    {class_name}.launch()
'''

TEMPLATES: dict[str, tuple[str, str]] = {
    "basic":     (AGENT_TEMPLATE,    "Simple agent with a custom @tool and memory"),
    "scheduled": (SCHEDULED_TEMPLATE,"Agent that runs on a schedule (e.g. every 1h)"),
    "research":  (RESEARCH_TEMPLATE, "Web researcher with persistent knowledge base"),
    "notifier":  (NOTIFIER_TEMPLATE, "Slack-alerting monitor with @schedule"),
    "webhook":   (WEBHOOK_TEMPLATE,  "Webhook-triggered agent — responds to HTTP POST events"),
}
