import pytest
from pathlib import Path
from aios.runtime.checkpoint import CheckpointEngine


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cp.db"


@pytest.fixture
async def engine(db_path: Path) -> CheckpointEngine:
    e = CheckpointEngine(agent_id="agent-test-001", db_path=db_path)
    await e.setup()
    return e


async def test_start_run_creates_id(engine: CheckpointEngine) -> None:
    run_id = await engine.start_run()
    assert len(run_id) == 36  # UUID format


async def test_cache_miss(engine: CheckpointEngine) -> None:
    await engine.start_run()
    hit, result = await engine.get_cached("my_tool", {"query": "hello"})
    assert hit is False
    assert result is None


async def test_cache_hit_after_save(engine: CheckpointEngine) -> None:
    await engine.start_run()
    await engine.save_result("my_tool", {"query": "hello"}, {"answer": 42})
    hit, result = await engine.get_cached("my_tool", {"query": "hello"})
    assert hit is True
    assert result == {"answer": 42}


async def test_cache_miss_different_args(engine: CheckpointEngine) -> None:
    await engine.start_run()
    await engine.save_result("my_tool", {"query": "hello"}, "result_a")
    hit, _ = await engine.get_cached("my_tool", {"query": "world"})
    assert hit is False


async def test_resume_uses_existing_run(engine: CheckpointEngine) -> None:
    run_id_1 = await engine.start_run()
    await engine.save_result("tool", {"x": 1}, "result")
    # Don't end the run — simulate crash

    # Next start_run should resume the same run
    run_id_2 = await engine.start_run()
    assert run_id_1 == run_id_2

    hit, result = await engine.get_cached("tool", {"x": 1})
    assert hit is True
    assert result == "result"


async def test_completed_run_starts_new(engine: CheckpointEngine) -> None:
    run_id_1 = await engine.start_run()
    await engine.end_run()

    run_id_2 = await engine.start_run()
    assert run_id_1 != run_id_2


async def test_end_run_marks_completed(engine: CheckpointEngine) -> None:
    await engine.start_run()
    await engine.end_run()
    history = await engine.run_history()
    assert history[0]["status"] == "completed"


async def test_end_run_with_error(engine: CheckpointEngine) -> None:
    await engine.start_run()
    await engine.end_run(error="Traceback: something broke")
    history = await engine.run_history()
    assert history[0]["status"] == "failed"
    assert history[0]["error"] is not None


async def test_run_history_order(engine: CheckpointEngine) -> None:
    for _ in range(3):
        await engine.start_run()
        await engine.end_run()

    history = await engine.run_history()
    assert len(history) == 3
    # Most recent first
    assert history[0]["started_at"] >= history[1]["started_at"]
