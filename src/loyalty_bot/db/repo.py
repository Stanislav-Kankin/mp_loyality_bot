from __future__ import annotations

import asyncpg


async def ensure_seller(pool: asyncpg.Pool, tg_user_id: int) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sellers(tg_user_id)
            VALUES ($1)
            ON CONFLICT (tg_user_id) DO UPDATE SET tg_user_id = EXCLUDED.tg_user_id
            RETURNING id;
            """,
            tg_user_id,
        )
        return int(row["id"])


async def ensure_customer(pool: asyncpg.Pool, tg_user_id: int) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO customers(tg_user_id)
            VALUES ($1)
            ON CONFLICT (tg_user_id) DO UPDATE SET tg_user_id = EXCLUDED.tg_user_id
            RETURNING id;
            """,
            tg_user_id,
        )
        return int(row["id"])


async def subscribe_customer_to_shop(pool: asyncpg.Pool, shop_id: int, customer_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO shop_customers(shop_id, customer_id, status, subscribed_at)
            VALUES ($1, $2, 'subscribed', now())
            ON CONFLICT (shop_id, customer_id)
            DO UPDATE SET status = 'subscribed', subscribed_at = now(), unsubscribed_at = NULL;
            """,
            shop_id,
            customer_id,
        )


async def shop_exists(pool: asyncpg.Pool, shop_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM shops WHERE id=$1;", shop_id)
        return row is not None
