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

            -- Payment Hub: cross-instance payment orders live in CENTRAL DB.
            CREATE TABLE IF NOT EXISTS payment_orders (
                id UUID PRIMARY KEY,
                instance_id TEXT NOT NULL,
                buyer_tg_id BIGINT NOT NULL,
                pack_code TEXT NOT NULL,
                amount_minor INT NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                paid_at TIMESTAMPTZ NULL,
                fulfilled_at TIMESTAMPTZ NULL,
                provider_payment_charge_id TEXT NULL,
                invoice_payload TEXT NOT NULL
            );

            -- Protect against invalid transitions and duplicates.
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'payment_orders_status_chk'
              ) THEN
                ALTER TABLE payment_orders
                  ADD CONSTRAINT payment_orders_status_chk
                  CHECK (status IN ('pending', 'paid', 'fulfilled', 'expired', 'cancelled'));
              END IF;
            END $$;

            CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_orders_invoice_payload
              ON payment_orders (invoice_payload);

            -- provider_payment_charge_id is only known after successful_payment.
            -- UNIQUE allows multiple NULLs in Postgres, which is what we want.
            CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_orders_provider_charge
              ON payment_orders (provider_payment_charge_id);

            CREATE INDEX IF NOT EXISTS ix_payment_orders_instance_buyer
              ON payment_orders (instance_id, buyer_tg_id, created_at DESC);
            """
        )


async def list_instances(
    pool: asyncpg.Pool,
    *,
    mode: str = "all",  # all|demo|brand
    status: str = "all",  # all|alive|dead
    query: str | None = None,
    sort: str = "seen",  # seen|name
    limit: int = 12,
    offset: int = 0,
) -> tuple[list[asyncpg.Record], int]:
    """Return (rows, total_count) for instances list."""

    if mode not in {"all", "demo", "brand"}:
        mode = "all"
    if status not in {"all", "alive", "dead"}:
        status = "all"

    q = (query or "").strip()
    if not q:
        q = ""
    # keep order stable (avoid SQL injection by using placeholders)
    if sort not in {"seen", "name"}:
        sort = "seen"

    # We keep SQL placeholders stable to avoid mistakes with dynamic numbering.
    # $1: mode (NULL means "all")
    # $2: alive window minutes
    # $3: query string ('' means disabled)
    # $3: limit
    # $4: offset
    mode_cond = "($1::text IS NULL OR i.mode = $1::text)"
    query_cond = "($3::text = '' OR i.instance_id ILIKE '%' || $3::text || '%' OR i.instance_name ILIKE '%' || $3::text || '%')"
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
                  AND {query_cond}
            )
            SELECT count(*)
            FROM base
            WHERE {status_cond};
            """,
            mode_arg,
            ALIVE_WINDOW_MINUTES,
            q,
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
                  AND {query_cond}
            )
            SELECT *
            FROM base
            WHERE {status_cond}
            ORDER BY {'last_seen_at DESC, instance_name ASC' if sort == 'seen' else 'instance_name ASC, last_seen_at DESC'}
            LIMIT $4 OFFSET $5;
            """,
            mode_arg,
            ALIVE_WINDOW_MINUTES,
            q,
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
