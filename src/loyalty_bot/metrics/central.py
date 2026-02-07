from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg

from loyalty_bot.config import settings

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_metrics_enabled() -> bool:
    """Metrics push is optional. If not configured, must not affect the bot."""
    return bool((settings.central_database_dsn or "").strip() and (settings.instance_id or "").strip())


async def create_central_pool() -> asyncpg.Pool | None:
    dsn = (settings.central_database_dsn or "").strip()
    if not dsn:
        return None
    try:
        return await asyncpg.create_pool(dsn)
    except Exception:
        logger.exception("failed to create central metrics pool")
        return None


async def push_heartbeat(pool: asyncpg.Pool, *, service: str) -> None:
    """Upsert instance + heartbeat in central DB.

    Central schema is created by SuperAdmin bot:
      - instances(instance_id PK, instance_name, mode, created_at, updated_at)
      - heartbeats(instance_id, service) PK, last_seen_at
    """
    instance_id = (settings.instance_id or "").strip()
    if not instance_id:
        return

    instance_name = (settings.instance_name or "").strip() or instance_id
    mode = (settings.bot_mode or "").strip().lower() or "unknown"
    now = _utc_now()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO instances(instance_id, instance_name, mode, created_at, updated_at)
            VALUES ($1, $2, $3, now(), now())
            ON CONFLICT (instance_id)
            DO UPDATE SET instance_name = EXCLUDED.instance_name,
                          mode = EXCLUDED.mode,
                          updated_at = now();
            """,
            instance_id,
            instance_name,
            mode,
        )
        await conn.execute(
            """
            INSERT INTO heartbeats(instance_id, service, last_seen_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (instance_id, service)
            DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at;
            """,
            instance_id,
            str(service),
            now,
        )
