from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory.store import MemoryStore

logger = logging.getLogger("aios.scheduler")

# Sentinel applied by @schedule
_SCHEDULE_MARKER = "__aios_schedule__"


def schedule(interval: str) -> Callable:
    """
    Decorator that makes an agent's run() method repeat on an interval.

    Intervals: "every 6h", "every 30m", "every 1d", or cron: "0 */6 * * *"

    Example::

        class MonitorAgent(Agent):
            @schedule("every 5m")
            async def run(self):
                await self.check_services()
    """

    def decorator(fn: Callable) -> Callable:
        setattr(fn, _SCHEDULE_MARKER, True)
        setattr(fn, "__aios_interval__", interval)
        return fn

    return decorator


def parse_interval(interval: str) -> int:
    """Parse an interval string into seconds. Returns 0 for cron expressions."""
    interval = interval.strip().lower()

    # "every Xh", "every Xm", "every Xd", "every X hours" etc.
    match = re.match(r"every\s+(\d+)\s*(s|sec|second|m|min|minute|h|hour|d|day)s?", interval)
    if match:
        n = int(match.group(1))
        unit = match.group(2)[0]
        return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]

    # Raw "6h", "30m", "1d"
    match = re.match(r"^(\d+)(s|m|h|d)$", interval)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]

    return 0  # treat as cron or unknown


class Scheduler:
    """
    Lightweight scheduler that runs an async function repeatedly.
    Stores next-run time in the agent's long-term memory so it survives restarts.
    """

    def __init__(self, interval_seconds: int, memory_key: str = "__next_run__") -> None:
        self._interval = interval_seconds
        self._memory_key = memory_key

    async def run_loop(self, fn: Callable, memory: MemoryStore) -> None:  # type: ignore[name-defined]
        while True:
            next_run_str = await memory.load(self._memory_key)
            now = datetime.utcnow()

            if next_run_str:
                next_run = datetime.fromisoformat(next_run_str)
                wait = (next_run - now).total_seconds()
                if wait > 0:
                    logger.info("scheduler: next run in %.0fs", wait)
                    await asyncio.sleep(wait)

            # Execute
            logger.info("scheduler: running (interval=%ds)", self._interval)
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.error("scheduler: run failed: %s", exc)

            # Schedule next
            next_run = datetime.utcnow() + timedelta(seconds=self._interval)
            await memory.save(self._memory_key, next_run.isoformat())
            logger.info("scheduler: next run at %s", next_run.strftime("%H:%M:%S"))
