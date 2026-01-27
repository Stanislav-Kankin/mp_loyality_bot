from __future__ import annotations

import pathlib
from typing import Iterable

import asyncpg


async def ensure_migrations_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def iter_migration_files(migrations_dir: pathlib.Path) -> Iterable[pathlib.Path]:
    if not migrations_dir.exists():
        return []
    files = sorted([p for p in migrations_dir.iterdir() if p.is_file() and p.suffix == ".sql"])
    return files


async def apply_migrations(conn: asyncpg.Connection, migrations_dir: pathlib.Path) -> None:
    await ensure_migrations_table(conn)

    rows = await conn.fetch("SELECT version FROM schema_migrations;")
    applied = {r["version"] for r in rows}

    for path in iter_migration_files(migrations_dir):
        version = path.name
        if version in applied:
            continue

        sql = path.read_text(encoding="utf-8")
        # execute as single script (idempotent via IF NOT EXISTS)
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations(version) VALUES ($1);", version)
