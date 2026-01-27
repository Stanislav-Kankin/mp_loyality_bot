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


async def unsubscribe_customer_from_shop(pool: asyncpg.Pool, shop_id: int, customer_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO shop_customers(shop_id, customer_id, status, unsubscribed_at)
            VALUES ($1, $2, 'unsubscribed', now())
            ON CONFLICT (shop_id, customer_id)
            DO UPDATE SET status = 'unsubscribed', unsubscribed_at = now();
            """,
            shop_id,
            customer_id,
        )


async def shop_exists(pool: asyncpg.Pool, shop_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM shops WHERE id=$1;", shop_id)
        return row is not None


async def create_shop(pool: asyncpg.Pool, seller_tg_user_id: int, name: str, category: str) -> int:
    # Ensure seller exists and create shop under it.
    async with pool.acquire() as conn:
        async with conn.transaction():
            seller_row = await conn.fetchrow(
                """
                INSERT INTO sellers(tg_user_id)
                VALUES ($1)
                ON CONFLICT (tg_user_id) DO UPDATE SET tg_user_id = EXCLUDED.tg_user_id
                RETURNING id;
                """,
                seller_tg_user_id,
            )
            seller_id = int(seller_row["id"])

            shop_row = await conn.fetchrow(
                """
                INSERT INTO shops(seller_id, name, category)
                VALUES ($1, $2, $3)
                RETURNING id;
                """,
                seller_id,
                name,
                category,
            )
            return int(shop_row["id"])


async def list_seller_shops(pool: asyncpg.Pool, seller_tg_user_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id
            FROM sellers s
            WHERE s.tg_user_id=$1;
            """,
            seller_tg_user_id,
        )
        if not rows:
            return []

        seller_id = int(rows[0]["id"])
        shops = await conn.fetch(
            """
            SELECT id, name, category, is_active, created_at
            FROM shops
            WHERE seller_id=$1
            ORDER BY created_at DESC, id DESC;
            """,
            seller_id,
        )
        return [
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "category": str(r["category"]),
                "is_active": bool(r["is_active"]),
                "created_at": r["created_at"],
            }
            for r in shops
        ]


async def get_shop_for_seller(pool: asyncpg.Pool, seller_tg_user_id: int, shop_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT sh.id, sh.name, sh.category, sh.is_active, sh.created_at
            FROM shops sh
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1 AND sh.id=$2;
            """,
            seller_tg_user_id,
            shop_id,
        )
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "category": str(row["category"]),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
        }
