"""
Monitor Agent — checks a URL on a schedule and alerts if it goes down.
Demonstrates: scheduling, persistent state, cross-run memory.
"""

import asyncio
from datetime import datetime

import httpx

from aios import Agent, tool


class MonitorAgent(Agent):
    name = "monitor"
    model = "claude-haiku-4-5-20251001"  # Fast + cheap for simple checks
    version = "1.0.0"
    description = "Monitors URLs and tracks uptime history."
    config = {
        "targets": [
            "https://httpbin.org/status/200",
            "https://httpbin.org/status/200",
        ],
        "interval_seconds": 60,
    }

    @tool
    async def check_url(self, url: str) -> dict:
        """
        HTTP GET a URL and return status info.
        url: The URL to check.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                start = asyncio.get_event_loop().time()
                resp = await client.get(url)
                elapsed = asyncio.get_event_loop().time() - start
                return {
                    "url": url,
                    "status": resp.status_code,
                    "ok": resp.status_code < 400,
                    "latency_ms": round(elapsed * 1000),
                    "checked_at": datetime.utcnow().isoformat(),
                }
        except Exception as exc:
            return {"url": url, "status": 0, "ok": False, "error": str(exc), "checked_at": datetime.utcnow().isoformat()}

    async def run(self) -> None:
        targets = self.config.get("targets", [])
        interval = self.config.get("interval_seconds", 60)

        print(f"[{self.name}] monitoring {len(targets)} target(s) every {interval}s")

        while True:
            results = []
            for url in targets:
                result = await self.check_url(url)
                results.append(result)
                status = "✓" if result["ok"] else "✗"
                latency = result.get("latency_ms", "—")
                print(f"  {status} {url}  [{result['status']}]  {latency}ms")

            # Persist check history
            history = await self.memory.load("check_history", [])
            history.append({"ts": datetime.utcnow().isoformat(), "results": results})
            history = history[-500:]  # Keep last 500 checks
            await self.memory.save("check_history", history)

            # Alert on failure
            failures = [r for r in results if not r["ok"]]
            if failures:
                await self.memory.log_event("alert", {"failures": failures})
                print(f"\n  ⚠ {len(failures)} target(s) down!\n")

            await asyncio.sleep(interval)


if __name__ == "__main__":
    MonitorAgent.launch()
