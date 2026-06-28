import pytest
from pathlib import Path
from aios.memory.store import MemoryStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def store(db_path: Path) -> MemoryStore:
    s = MemoryStore(agent_id="test-agent-001", db_path=db_path)
    await s.setup()
    return s


async def test_short_term_set_get(store: MemoryStore) -> None:
    store.set("key", "value")
    assert store.get("key") == "value"


async def test_short_term_default(store: MemoryStore) -> None:
    assert store.get("missing", "default") == "default"


async def test_short_term_clear(store: MemoryStore) -> None:
    store.set("a", 1)
    store.clear()
    assert store.get("a") is None


async def test_long_term_save_load(store: MemoryStore) -> None:
    await store.save("report", {"rows": 42, "status": "ok"})
    result = await store.load("report")
    assert result == {"rows": 42, "status": "ok"}


async def test_long_term_overwrite(store: MemoryStore) -> None:
    await store.save("counter", 1)
    await store.save("counter", 2)
    result = await store.load("counter")
    assert result == 2


async def test_long_term_default(store: MemoryStore) -> None:
    result = await store.load("nonexistent", "fallback")
    assert result == "fallback"


async def test_long_term_delete(store: MemoryStore) -> None:
    await store.save("temp", "data")
    await store.delete("temp")
    result = await store.load("temp")
    assert result is None


async def test_keys(store: MemoryStore) -> None:
    await store.save("a", 1)
    await store.save("b", 2)
    keys = await store.keys()
    assert set(keys) == {"a", "b"}


async def test_all(store: MemoryStore) -> None:
    await store.save("x", 10)
    await store.save("y", 20)
    data = await store.all()
    assert data["x"] == 10
    assert data["y"] == 20


async def test_timeline(store: MemoryStore) -> None:
    await store.log_event("started", {"run": "abc"})
    await store.log_event("completed", {"rows": 5})
    timeline = await store.timeline()
    assert len(timeline) == 2
    assert timeline[0]["event"] == "completed"  # DESC order
