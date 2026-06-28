"""
Notifier Agent — monitors a data source and sends alerts via Slack, Discord, and email.

Demonstrates:
- SlackMixin, DiscordMixin, EmailMixin used together
- @schedule decorator for recurring checks
- Persistent alert history (no duplicate alerts across restarts)
- think() for generating human-friendly summaries

Setup:
    Copy .env.example to .env and fill in at least one notification channel:
        SLACK_BOT_TOKEN=xoxb-...        (+ channel name below)
        DISCORD_WEBHOOK_URL=https://...
        EMAIL_ADDRESS + EMAIL_PASSWORD + EMAIL_SMTP_HOST

Usage:
    aios run examples/notifier.py
    aios run examples/notifier.py --detach
    aios logs notifier -f
"""

from aios import Agent, DiscordMixin, EmailMixin, SlackMixin, schedule, tool


# ── Configure these ─────────────────────────────────────────────────────────
SLACK_CHANNEL = "#alerts"
ALERT_EMAIL_TO = "team@example.com"
# ────────────────────────────────────────────────────────────────────────────


class NotifierAgent(Agent, SlackMixin, DiscordMixin, EmailMixin):
    name = "notifier"
    model = "claude-haiku-4-5-20251001"  # fast + cheap for frequent checks
    version = "1.0.0"
    description = "Monitors services and sends multi-channel alerts."
    system_prompt = (
        "You are a concise ops assistant. When summarizing alerts, "
        "be direct: what broke, severity, recommended action. Under 100 words."
    )
    config = {
        # URLs to check — replace with your own endpoints
        "watch_urls": [
            "https://httpbin.org/status/200",
            "https://httpbin.org/status/200",
        ],
        "alert_threshold_ms": 2000,  # alert if response time exceeds this
    }

    @tool
    async def check_endpoint(self, url: str) -> dict:
        """
        HTTP GET a URL and return status info.
        url: The URL to check.
        """
        import asyncio
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                t0 = asyncio.get_event_loop().time()
                resp = await client.get(url)
                elapsed_ms = round((asyncio.get_event_loop().time() - t0) * 1000)
                return {
                    "url": url,
                    "status": resp.status_code,
                    "ok": resp.status_code < 400,
                    "latency_ms": elapsed_ms,
                }
        except Exception as exc:
            return {"url": url, "status": 0, "ok": False, "error": str(exc), "latency_ms": -1}

    @tool
    async def send_alert(self, message: str, severity: str = "warning") -> str:
        """
        Send an alert to all configured channels.
        message: Alert message text.
        severity: 'info', 'warning', or 'critical'.
        """
        import os

        color_map = {"info": 0x30D483, "warning": 0xF0A843, "critical": 0xE05C5C}
        color = color_map.get(severity, 0xF0A843)
        sent = []

        if os.environ.get("SLACK_BOT_TOKEN"):
            try:
                await self.slack_send_message(SLACK_CHANNEL, f"*[{severity.upper()}]* {message}")
                sent.append("Slack")
            except Exception as e:
                print(f"  [notifier] Slack failed: {e}")

        if os.environ.get("DISCORD_WEBHOOK_URL"):
            try:
                await self.discord_send_embed(
                    title=f"[{severity.upper()}] Alert",
                    description=message,
                    color=color,
                )
                sent.append("Discord")
            except Exception as e:
                print(f"  [notifier] Discord failed: {e}")

        if os.environ.get("EMAIL_ADDRESS") and os.environ.get("EMAIL_PASSWORD"):
            try:
                await self.send_email(
                    to=ALERT_EMAIL_TO,
                    subject=f"[{severity.upper()}] Ai.os Alert",
                    body=message,
                )
                sent.append("Email")
            except Exception as e:
                print(f"  [notifier] Email failed: {e}")

        if not sent:
            print(f"  [notifier] No notification channels configured — alert not sent")
            return "No channels configured"

        return f"Alert sent via: {', '.join(sent)}"

    @schedule("every 5m")
    async def run(self) -> None:
        urls = self.config.get("watch_urls", [])
        threshold_ms = self.config.get("alert_threshold_ms", 2000)

        print(f"\n[{self.name}] checking {len(urls)} endpoint(s)")

        results = []
        for url in urls:
            r = await self.check_endpoint(url)
            results.append(r)
            icon = "✓" if r["ok"] else "✗"
            print(f"  {icon} {url}  [{r['status']}]  {r.get('latency_ms', '?')}ms")

        # Filter problems
        problems = [
            r for r in results
            if not r["ok"] or r.get("latency_ms", 0) > threshold_ms
        ]

        if not problems:
            print(f"  All endpoints healthy")
            return

        # Check if we've already alerted for this set of failures
        last_alert_key = "last_alert_urls"
        last_alerted = set(await self.memory.load(last_alert_key) or [])
        problem_urls = {r["url"] for r in problems}
        new_problems = problem_urls - last_alerted

        if not new_problems:
            print(f"  {len(problems)} problem(s) — already alerted, skipping")
            return

        # Generate a human-friendly summary via LLM
        summary = await self.think(
            f"Summarize these endpoint problems for an ops alert:\n"
            + "\n".join(
                f"- {r['url']}: status={r['status']}, latency={r.get('latency_ms')}ms, error={r.get('error', 'none')}"
                for r in problems
            )
        )

        severity = "critical" if any(r["status"] == 0 for r in problems) else "warning"
        await self.send_alert(summary, severity=severity)

        # Remember which URLs we've alerted for
        await self.memory.save(last_alert_key, list(problem_urls))
        await self.memory.log_event("alert_sent", {"urls": list(problem_urls), "severity": severity})

        print(f"  ⚠ Alert sent for {len(new_problems)} new problem(s)")


if __name__ == "__main__":
    NotifierAgent.launch()
