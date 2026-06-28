"""Tests for aios.secrets — encrypted key-value store."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Skip the entire module if cryptography is not installed
pytest.importorskip("cryptography")

from aios.secrets import SecretsStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> SecretsStore:
    """Return a SecretsStore backed by a temporary directory."""
    return SecretsStore(aios_dir=tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_round_trip(store: SecretsStore) -> None:
    """set() then get() should return the original plaintext."""
    await store.set("MY_KEY", "super-secret-value")
    result = await store.get("MY_KEY")
    assert result == "super-secret-value"


@pytest.mark.asyncio
async def test_get_missing_key_returns_default(store: SecretsStore) -> None:
    """get() with a key that was never set should return the default."""
    result = await store.get("DOES_NOT_EXIST")
    assert result is None

    result_custom = await store.get("DOES_NOT_EXIST", default="fallback")
    assert result_custom == "fallback"


@pytest.mark.asyncio
async def test_delete_removes_key(store: SecretsStore) -> None:
    """delete() should make the key unrecoverable."""
    await store.set("TO_DELETE", "bye")
    await store.delete("TO_DELETE")
    assert await store.get("TO_DELETE") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_key_is_noop(store: SecretsStore) -> None:
    """delete() on a missing key should not raise."""
    await store.delete("GHOST_KEY")  # should not raise


@pytest.mark.asyncio
async def test_list_returns_names(store: SecretsStore) -> None:
    """list() should return a sorted list of stored names without values."""
    await store.set("ZEBRA", "z")
    await store.set("ALPHA", "a")
    names = await store.list()
    assert "ZEBRA" in names
    assert "ALPHA" in names
    # Values must NOT appear
    assert "z" not in names
    assert "a" not in names


@pytest.mark.asyncio
async def test_inject_to_env_populates_environ(store: SecretsStore, monkeypatch) -> None:
    """inject_to_env() should set missing vars in os.environ."""
    monkeypatch.delenv("INJECT_TEST_KEY", raising=False)
    await store.set("INJECT_TEST_KEY", "injected-value")
    await store.inject_to_env()
    assert os.environ.get("INJECT_TEST_KEY") == "injected-value"


@pytest.mark.asyncio
async def test_inject_to_env_does_not_overwrite(store: SecretsStore, monkeypatch) -> None:
    """inject_to_env() must NOT overwrite existing environment variables."""
    monkeypatch.setenv("EXISTING_VAR", "original")
    await store.set("EXISTING_VAR", "from-secrets")
    await store.inject_to_env()
    # The original value must be preserved
    assert os.environ["EXISTING_VAR"] == "original"


@pytest.mark.asyncio
async def test_import_env_file(store: SecretsStore, tmp_path: Path) -> None:
    """import_env_file() should parse a .env file and store all variables."""
    env_content = (
        "# comment line\n"
        "\n"
        "KEY_ONE=value_one\n"
        'KEY_TWO="quoted value"\n'
        "KEY_THREE='single quoted'\n"
    )
    env_file = tmp_path / ".env"
    env_file.write_text(env_content, encoding="utf-8")

    count = await store.import_env_file(env_file)
    assert count == 3
    assert await store.get("KEY_ONE") == "value_one"
    assert await store.get("KEY_TWO") == "quoted value"
    assert await store.get("KEY_THREE") == "single quoted"


@pytest.mark.asyncio
async def test_encryption_is_applied(store: SecretsStore, tmp_path: Path) -> None:
    """The raw bytes stored in the DB should NOT match the plaintext."""
    import aiosqlite

    plaintext = "plaintext-secret-12345"
    await store.set("ENCRYPTED_CHECK", plaintext)

    db_path = tmp_path / "secrets.db"
    async with aiosqlite.connect(db_path) as db:
        row = await (
            await db.execute("SELECT value FROM secrets WHERE name = 'ENCRYPTED_CHECK'")
        ).fetchone()

    assert row is not None
    raw_bytes = bytes(row[0])
    # The raw stored bytes must not contain the plaintext
    assert plaintext.encode() not in raw_bytes
    # It should look like a Fernet token (starts with 'gAAA')
    assert raw_bytes.startswith(b"gAAA")
