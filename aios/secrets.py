"""
Encrypted secrets store for Ai.os agents.

Secrets are encrypted with Fernet symmetric encryption (cryptography package).
The master key is auto-generated on first use and stored at ~/.aios/master.key.
Encrypted secret values are stored in ~/.aios/secrets.db (SQLite).

Usage::

    from aios.secrets import SecretsStore

    store = SecretsStore()
    await store.set("OPENAI_API_KEY", "sk-...")
    key = await store.get("OPENAI_API_KEY")
    await store.inject_to_env()   # load all secrets into os.environ
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Optional

# The directory for Ai.os runtime data in the user's home directory.
# All secrets live here — NOT in the project directory.
_AIOS_HOME: Path = Path.home() / ".aios"

_MASTER_KEY_FILE = "master.key"
_SECRETS_DB_FILE = "secrets.db"


def _require_cryptography() -> None:
    """Raise ImportError with a helpful hint if cryptography is not installed."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise ImportError(
            "The 'cryptography' package is required for secrets management.\n"
            "Install it with:  pip install cryptography"
        )


def _get_fernet(master_key: bytes):  # type: ignore[return]
    """Return a Fernet instance using the given master key bytes."""
    from cryptography.fernet import Fernet  # type: ignore[import-not-found]

    return Fernet(master_key)


def _load_or_create_master_key(aios_dir: Path) -> bytes:
    """
    Return the Fernet master key.  Creates one on first call and persists it
    to <aios_dir>/master.key with permissions 0o600 (Unix) or locked-down ACL.
    """
    key_path = aios_dir / _MASTER_KEY_FILE
    aios_dir.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        return key_path.read_bytes().strip()

    # First run — generate a new key
    from cryptography.fernet import Fernet  # type: ignore[import-not-found]

    key: bytes = Fernet.generate_key()
    key_path.write_bytes(key)

    # chmod 600 on Unix/macOS so only the owner can read it
    if sys.platform != "win32":
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    return key


class SecretsStore:
    """
    Encrypted key-value store backed by SQLite.

    Parameters
    ----------
    aios_dir:
        Override the directory used for master.key and secrets.db.
        Defaults to ``~/.aios``.  Useful in tests (monkeypatch home dir).
    """

    def __init__(self, aios_dir: Optional[Path] = None) -> None:
        _require_cryptography()
        self._aios_dir: Path = aios_dir or _AIOS_HOME
        self._db_path: Path = self._aios_dir / _SECRETS_DB_FILE
        self._master_key: bytes = _load_or_create_master_key(self._aios_dir)

    # ── internal helpers ───────────────────────────────────────────────────────

    def _encrypt(self, plaintext: str) -> bytes:
        return _get_fernet(self._master_key).encrypt(plaintext.encode())

    def _decrypt(self, ciphertext: bytes) -> str:
        return _get_fernet(self._master_key).decrypt(ciphertext).decode()

    async def _setup(self) -> None:
        """Ensure the secrets table exists."""
        import aiosqlite

        self._aios_dir.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS secrets (
                    name  TEXT PRIMARY KEY NOT NULL,
                    value BLOB NOT NULL
                )
                """
            )
            await db.commit()

    # ── public API ─────────────────────────────────────────────────────────────

    async def set(self, name: str, value: str) -> None:
        """Encrypt *value* and store it under *name*."""
        await self._setup()
        import aiosqlite

        ciphertext = self._encrypt(value)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO secrets (name, value) VALUES (?, ?)"
                " ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, ciphertext),
            )
            await db.commit()

    async def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Return the decrypted value for *name*, or *default* if not found."""
        await self._setup()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            row = await (
                await db.execute("SELECT value FROM secrets WHERE name = ?", (name,))
            ).fetchone()

        if row is None:
            return default
        return self._decrypt(bytes(row[0]))

    async def delete(self, name: str) -> None:
        """Remove the secret with *name*. No-op if it does not exist."""
        await self._setup()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM secrets WHERE name = ?", (name,))
            await db.commit()

    async def list(self) -> list[str]:
        """Return a list of stored secret names (never the values)."""
        await self._setup()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            rows = await (
                await db.execute("SELECT name FROM secrets ORDER BY name")
            ).fetchall()
        return [r[0] for r in rows]

    async def inject_to_env(self) -> None:
        """
        Load every secret into ``os.environ``.

        Follows the same convention as python-dotenv: existing environment
        variables are **not** overwritten.  Only missing variables are injected.
        """
        names = await self.list()
        for name in names:
            if name not in os.environ:
                value = await self.get(name)
                if value is not None:
                    os.environ[name] = value

    async def import_env_file(self, env_path: Path) -> int:
        """
        Read a ``.env`` file and store every variable as an encrypted secret.

        Lines that are blank or start with ``#`` are skipped.  Returns the
        number of secrets imported.
        """
        count = 0
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip surrounding quotes (single or double)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if key:
                await self.set(key, val)
                count += 1
        return count
