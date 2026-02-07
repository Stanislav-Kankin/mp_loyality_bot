from __future__ import annotations

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    """Create minimal schema for SuperAdmin MVP.

    Only contains instance registry and heartbeats. No PII.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instances (
                instance_id TEXT PRIMARY KEY,
                instance_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS heartbeats (
                instance_id TEXT NOT NULL REFERENCES instances(instance_id) ON DELETE CASCADE,
                service TEXT NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (instance_id, service)
            );
            """
        )


async def list_instances(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT i.instance_id, i.instance_name, i.mode,
                   hb_bot.last_seen_at AS bot_last_seen,
                   hb_worker.last_seen_at AS worker_last_seen
            FROM instances i
            LEFT JOIN heartbeats hb_bot
              ON hb_bot.instance_id = i.instance_id AND hb_bot.service = 'bot'
            LEFT JOIN heartbeats hb_worker
              ON hb_worker.instance_id = i.instance_id AND hb_worker.service = 'worker'
            ORDER BY i.updated_at DESC, i.created_at DESC;
            """
        )
