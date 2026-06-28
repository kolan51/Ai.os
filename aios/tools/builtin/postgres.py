from __future__ import annotations

import os
from typing import Any

from ..registry import tool


class PostgresMixin:
    """
    Adds PostgreSQL query tools to an agent.

    Requires POSTGRES_URL in environment (standard connection string).
    Uses asyncpg for async queries — install with: pip install asyncpg

    Usage::

        from aios import Agent
        from aios.tools.builtin import PostgresMixin

        class DataAgent(Agent, PostgresMixin):
            name = "analyst"
            model = "claude-sonnet-4-6"

            async def run(self):
                rows = await self.pg_query("SELECT count(*) FROM orders WHERE status=$1", ["pending"])
                schema = await self.pg_table_schema("users")
    """

    @property
    def _pg_url(self) -> str:
        url = os.environ.get("POSTGRES_URL", "")
        if not url:
            raise OSError(
                "POSTGRES_URL not set. "
                "Set it to a standard PostgreSQL connection string: "
                "postgresql://user:password@host:5432/database"
            )
        return url

    def _require_asyncpg(self) -> Any:
        try:
            import asyncpg
            return asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgresMixin. "
                "Install it with: pip install asyncpg"
            ) from exc

    def _serialize_row(self, row: Any) -> dict[str, Any]:
        """Convert an asyncpg Record to a plain dict, serializing non-JSON types."""
        result: dict[str, Any] = {}
        for key in row.keys():
            val = row[key]
            if hasattr(val, "isoformat"):
                result[key] = val.isoformat()
            elif hasattr(val, "__class__") and val.__class__.__name__ in ("Decimal", "UUID"):
                result[key] = str(val)
            else:
                result[key] = val
        return result

    @tool
    async def pg_query(self, sql: str, params: list | None = None) -> list[dict]:
        """
        Execute a SELECT query and return rows as dicts.
        sql: SQL query. Use $1, $2, ... placeholders for parameters.
        params: Optional list of parameter values matching the $N placeholders.
        """
        url = self._pg_url  # validate env first
        asyncpg = self._require_asyncpg()
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(sql, *(params or []))
            return [self._serialize_row(r) for r in rows]
        finally:
            await conn.close()

    @tool
    async def pg_execute(self, sql: str, params: list | None = None) -> str:
        """
        Execute an INSERT, UPDATE, DELETE, or DDL statement.
        sql: SQL statement. Use $1, $2, ... placeholders for parameters.
        params: Optional list of parameter values.
        Returns a status string (e.g. 'INSERT 0 3', 'UPDATE 5').
        """
        url = self._pg_url
        asyncpg = self._require_asyncpg()
        conn = await asyncpg.connect(url)
        try:
            status = await conn.execute(sql, *(params or []))
            return str(status)
        finally:
            await conn.close()

    @tool
    async def pg_list_tables(self, schema: str = "public") -> list[dict]:
        """
        List all tables in a PostgreSQL schema.
        schema: Schema name (default: 'public').
        """
        rows = await self.pg_query(
            """
            SELECT table_name, table_type,
                   pg_size_pretty(pg_total_relation_size(quote_ident(table_name)::regclass)) AS size
            FROM information_schema.tables
            WHERE table_schema = $1
            ORDER BY table_name
            """,
            [schema],
        )
        return rows

    @tool
    async def pg_table_schema(self, table: str, schema: str = "public") -> list[dict]:
        """
        Get column definitions for a table.
        table: Table name.
        schema: Schema name (default: 'public').
        """
        rows = await self.pg_query(
            """
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
            """,
            [schema, table],
        )
        return rows

    @tool
    async def pg_count(self, table: str, where: str = "", params: list | None = None) -> int:
        """
        Count rows in a table, with an optional WHERE clause.
        table: Table name (not user-provided SQL — validated as identifier).
        where: Optional WHERE clause without the 'WHERE' keyword (e.g. 'status = $1').
        params: Parameters for the WHERE clause.
        """
        # Validate table is a safe identifier (letters, digits, underscores only)
        if not all(c.isalnum() or c in ("_", ".") for c in table):
            raise ValueError(f"Invalid table name: {table!r}")
        sql = f"SELECT COUNT(*) AS n FROM {table}"
        if where:
            sql += f" WHERE {where}"
        rows = await self.pg_query(sql, params)
        return int(rows[0]["n"]) if rows else 0
