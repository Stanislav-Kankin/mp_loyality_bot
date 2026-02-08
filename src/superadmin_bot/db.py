from __future__ import annotations

import asyncpg


# If both bot & worker are silent longer than this window, instance is considered "dead".
ALIVE_WINDOW_MINUTES = 3


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

            CREATE TABLE IF NOT EXISTS instance_metrics (
                instance_id TEXT PRIMARY KEY REFERENCES instances(instance_id) ON DELETE CASCADE,
                updated_at TIMESTAMPTZ NOT NULL,
                campaigns_total BIGINT NOT NULL DEFAULT 0,
                campaigns_today BIGINT NOT NULL DEFAULT 0,
                deliveries_sent_today BIGINT NOT NULL DEFAULT 0,
                deliveries_failed_today BIGINT NOT NULL DEFAULT 0,
                deliveries_blocked_today BIGINT NOT NULL DEFAULT 0,
                subscribers_active BIGINT NOT NULL DEFAULT 0
            );

            -- Daily snapshot table for period-based aggregates (no PII)
            CREATE TABLE IF NOT EXISTS instance_metrics_daily (
                instance_id TEXT NOT NULL REFERENCES instances(instance_id) ON DELETE CASCADE,
                metric_date DATE NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                campaigns_today BIGINT NOT NULL DEFAULT 0,
                deliveries_sent_today BIGINT NOT NULL DEFAULT 0,
                deliveries_failed_today BIGINT NOT NULL DEFAULT 0,
                deliveries_blocked_today BIGINT NOT NULL DEFAULT 0,
                subscribers_active BIGINT NOT NULL DEFAULT 0,
                PRIMARY KEY (instance_id, metric_date)
            );
            """
        )


async def get_instance_metrics_for_period(
    pool: asyncpg.Pool,
    *,
    instance_id: str,
    period: str,  # today|7d|all
) -> dict[str, object]:
    """Return metrics dict for given period.

    - today: values from `instance_metrics` (latest upsert)
    - 7d/all: sums from `instance_metrics_daily`

    Always includes keys compatible with app._fmt_metrics.
    """
    period = period if period in {"today", "7d", "all"} else "today"

    async with pool.acquire() as conn:
        base = await conn.fetchrow(
            """
            SELECT updated_at AS metrics_at,
                   campaigns_total,
                   campaigns_today,
                   deliveries_sent_today,
                   deliveries_failed_today,
                   deliveries_blocked_today,
                   subscribers_active
            FROM instance_metrics
            WHERE instance_id = $1;
            """,
            instance_id,
        )

        if not base:
            return {"metrics_at": None}

        # Today is exactly what's stored in instance_metrics
        if period == "today":
            return dict(base)

        # For period aggregates we sum daily snapshots.
        # campaigns_total should remain "all time" from instance_metrics.
        date_cond = "TRUE"
        args: list[object] = [instance_id]
        if period == "7d":
            date_cond = "metric_date >= (CURRENT_DATE - INTERVAL '6 days')"

        agg = await conn.fetchrow(
            f"""
            SELECT MAX(updated_at) AS metrics_at,
                   COALESCE(SUM(campaigns_today), 0) AS campaigns_today,
                   COALESCE(SUM(deliveries_sent_today), 0) AS deliveries_sent_today,
                   COALESCE(SUM(deliveries_failed_today), 0) AS deliveries_failed_today,
                   COALESCE(SUM(deliveries_blocked_today), 0) AS deliveries_blocked_today,
                   MAX(subscribers_active) AS subscribers_active
            FROM instance_metrics_daily
            WHERE instance_id = $1 AND {date_cond};
            """,
            *args,
        )

        out = dict(base)
        if agg:
            out.update({k: agg[k] for k in agg.keys()})
        return out


async def list_instances(
    pool: asyncpg.Pool,
    *,
    mode: str = "all",  # all|demo|brand
    status: str = "all",  # all|alive|dead
    limit: int = 12,
    offset: int = 0,
) -> tuple[list[asyncpg.Record], int]:
    """Return (rows, total_count) for instances list."""

    if mode not in {"all", "demo", "brand"}:
        mode = "all"
    if status not in {"all", "alive", "dead"}:
        status = "all"

    # We keep SQL placeholders stable to avoid mistakes with dynamic numbering.
    # $1: mode (NULL means "all")
    # $2: alive window minutes
    # $3: limit
    # $4: offset
    mode_cond = "($1::text IS NULL OR i.mode = $1::text)"
    # alive if max(bot_last_seen, worker_last_seen) is within window
    # IMPORTANT: keep $2 placeholder present for *all* statuses (asyncpg binds args by placeholder count).
    status_cond = "(TRUE OR $2::int IS NOT NULL)"
    if status == "alive":
        status_cond = "last_seen_at >= (now() - ($2::int * interval '1 minute'))"
    elif status == "dead":
        status_cond = "last_seen_at < (now() - ($2::int * interval '1 minute'))"

    mode_arg: str | None = None if mode == "all" else mode

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"""
            WITH base AS (
                SELECT i.instance_id,
                       GREATEST(
                           COALESCE(hb_bot.last_seen_at, to_timestamp(0)),
                           COALESCE(hb_worker.last_seen_at, to_timestamp(0))
                       ) AS last_seen_at
                FROM instances i
                LEFT JOIN heartbeats hb_bot
                  ON hb_bot.instance_id = i.instance_id AND hb_bot.service = 'bot'
                LEFT JOIN heartbeats hb_worker
                  ON hb_worker.instance_id = i.instance_id AND hb_worker.service = 'worker'
                WHERE {mode_cond}
            )
            SELECT count(*)
            FROM base
            WHERE {status_cond};
            """,
            mode_arg,
            ALIVE_WINDOW_MINUTES,
        )

        rows = await conn.fetch(
            f"""
            WITH base AS (
                SELECT i.instance_id, i.instance_name, i.mode,
                       hb_bot.last_seen_at AS bot_last_seen,
                       hb_worker.last_seen_at AS worker_last_seen,
                       GREATEST(
                           COALESCE(hb_bot.last_seen_at, to_timestamp(0)),
                           COALESCE(hb_worker.last_seen_at, to_timestamp(0))
                       ) AS last_seen_at,

                       m.updated_at AS metrics_at,
                       m.campaigns_total,
                       m.campaigns_today,
                       m.deliveries_sent_today,
                       m.deliveries_failed_today,
                       m.deliveries_blocked_today,
                       m.subscribers_active
                FROM instances i
                LEFT JOIN heartbeats hb_bot
                  ON hb_bot.instance_id = i.instance_id AND hb_bot.service = 'bot'
                LEFT JOIN heartbeats hb_worker
                  ON hb_worker.instance_id = i.instance_id AND hb_worker.service = 'worker'
                LEFT JOIN instance_metrics m
                  ON m.instance_id = i.instance_id
                WHERE {mode_cond}
            )
            SELECT *
            FROM base
            WHERE {status_cond}
            ORDER BY last_seen_at DESC, instance_name ASC
            LIMIT $3 OFFSET $4;
            """,
            mode_arg,
            ALIVE_WINDOW_MINUTES,
            limit,
            offset,
        )
        return rows, int(total or 0)


async def get_instance(pool: asyncpg.Pool, instance_id: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT i.instance_id, i.instance_name, i.mode,
                   hb_bot.last_seen_at AS bot_last_seen,
                   hb_worker.last_seen_at AS worker_last_seen,

                   m.updated_at AS metrics_at,
                   m.campaigns_total,
                   m.campaigns_today,
                   m.deliveries_sent_today,
                   m.deliveries_failed_today,
                   m.deliveries_blocked_today,
                   m.subscribers_active
            FROM instances i
            LEFT JOIN heartbeats hb_bot
              ON hb_bot.instance_id = i.instance_id AND hb_bot.service = 'bot'
            LEFT JOIN heartbeats hb_worker
              ON hb_worker.instance_id = i.instance_id AND hb_worker.service = 'worker'
            LEFT JOIN instance_metrics m
              ON m.instance_id = i.instance_id
            WHERE i.instance_id = $1;
            """,
            instance_id,
        )
