"""Tests for @tool(cache_ttl=N) in-memory result caching."""
from __future__ import annotations

import asyncio
import pytest
from aios import tool


class _DummyAgent:
    """Minimal stand-in — no real Agent needed for unit tests."""

    def __init__(self):
        self.call_count = 0

    @tool(cache_ttl=10.0)
    async def fetch(self, url: str) -> str:
        """Fetch a URL. url: The endpoint."""
        self.call_count += 1
        return f"response-for-{url}"

    @tool(cache_ttl=0.001)  # extremely short TTL for expiry test
    async def short_ttl(self, key: str) -> str:
        """Short-TTL tool. key: cache key."""
        self.call_count += 1
        return f"value-{self.call_count}"

    @tool
    async def no_cache(self, x: str) -> str:
        """No cache. x: input."""
        self.call_count += 1
        return f"result-{self.call_count}"


@pytest.fixture()
def agent():
    return _DummyAgent()


def test_cache_ttl_returns_cached_result(agent):
    r1 = asyncio.run(agent.fetch("http://example.com"))
    r2 = asyncio.run(agent.fetch("http://example.com"))
    assert r1 == r2
    assert agent.call_count == 1  # only called once


def test_cache_ttl_different_args_not_cached(agent):
    asyncio.run(agent.fetch("http://a.com"))
    asyncio.run(agent.fetch("http://b.com"))
    assert agent.call_count == 2


def test_cache_ttl_expired(agent):
    import time
    asyncio.run(agent.short_ttl("k"))
    time.sleep(0.05)  # well past 1ms TTL
    asyncio.run(agent.short_ttl("k"))
    assert agent.call_count == 2


def test_cache_ttl_not_applied_without_option(agent):
    asyncio.run(agent.no_cache("x"))
    asyncio.run(agent.no_cache("x"))
    assert agent.call_count == 2


def test_cache_ttl_schema_preserved(agent):
    schema = getattr(type(agent).fetch, "__aios_schema__", None)
    assert schema is not None
    assert "url" in schema["properties"]


def test_cache_ttl_attribute_set():
    assert getattr(_DummyAgent.fetch, "__aios_cache_ttl__", None) == 10.0
    assert getattr(_DummyAgent.no_cache, "__aios_cache_ttl__", 0) == 0.0
