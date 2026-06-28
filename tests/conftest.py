"""Shared fixtures for the Ai.os test suite."""
import pytest
from pathlib import Path


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def agent_id() -> str:
    return "test-agent-00000000-0000-0000-0000-000000000001"
