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


async def get_customer(pool: asyncpg.Pool, tg_user_id: int) -> dict:
    """Ensure customer exists and return minimal profile."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO customers(tg_user_id)
            VALUES ($1)
            ON CONFLICT (tg_user_id) DO UPDATE SET tg_user_id = EXCLUDED.tg_user_id
            RETURNING id, full_years, gender;
            """,
            tg_user_id,
        )
        return {
            "id": int(row["id"]),
            "full_years": row["full_years"],
            "gender": row["gender"],
        }


async def ensure_customer(pool: asyncpg.Pool, tg_user_id: int) -> int:
    customer = await get_customer(pool, tg_user_id)
    return int(customer["id"])


async def update_customer_profile(
    pool: asyncpg.Pool,
    customer_id: int,
    *,
    full_years: int | None = None,
    gender: str | None = None,
) -> None:
    fields: list[str] = []
    args: list[object] = []
    idx = 1

    if full_years is not None:
        fields.append(f"full_years=${idx}")
        args.append(full_years)
        idx += 1

    if gender is not None:
        fields.append(f"gender=${idx}")
        args.append(gender)
        idx += 1

    if fields:
        fields.append("onboarded_at=now()")

    if not fields:
        return

    args.append(customer_id)
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE customers SET {', '.join(fields)} WHERE id=${idx};", *args)


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
    """Exists check for any shop (active or disabled)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM shops WHERE id=$1;", shop_id)
        return row is not None


async def shop_is_active(pool: asyncpg.Pool, shop_id: int) -> bool:
    """True if shop exists and is_active=true."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM shops WHERE id=$1 AND is_active=true;", shop_id)
        return row is not None


async def create_shop(pool: asyncpg.Pool, seller_tg_user_id: int, name: str, category: str) -> int:
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


async def get_shop_subscription_stats(pool: asyncpg.Pool, shop_id: int) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='subscribed') AS subscribed,
                COUNT(*) FILTER (WHERE status='unsubscribed') AS unsubscribed,
                COUNT(*) AS total
            FROM shop_customers
            WHERE shop_id=$1;
            """,
            shop_id,
        )
        return {
            "subscribed": int(row["subscribed"] or 0),
            "unsubscribed": int(row["unsubscribed"] or 0),
            "total": int(row["total"] or 0),
        }


# Admin helpers (used by admin shop actions in shop card)

async def update_shop(pool: asyncpg.Pool, shop_id: int, *, name: str | None = None, category: str | None = None) -> None:
    fields = []
    args = []
    idx = 1

    if name is not None:
        fields.append(f"name=${idx}")
        args.append(name)
        idx += 1
    if category is not None:
        fields.append(f"category=${idx}")
        args.append(category)
        idx += 1

    if not fields:
        return

    args.append(shop_id)
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE shops SET {', '.join(fields)} WHERE id=${idx};", *args)


async def set_shop_active(pool: asyncpg.Pool, shop_id: int, is_active: bool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE shops SET is_active=$1 WHERE id=$2;", is_active, shop_id)

# Campaigns (seller)

async def create_campaign_draft(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    shop_id: int,
    text: str,
    button_title: str,
    url: str,
    price_minor: int,
    currency: str,
) -> int:
    # Ensure shop belongs to seller
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1
            FROM shops sh
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1 AND sh.id=$2;
            """,
            seller_tg_user_id,
            shop_id,
        )
        if row is None:
            raise ValueError("shop_not_owned")

        camp = await conn.fetchrow(
            """
            INSERT INTO campaigns(shop_id, status, text, button_title, url, price_minor, currency)
            VALUES ($1, 'draft', $2, $3, $4, $5, $6)
            RETURNING id;
            """,
            shop_id,
            text,
            button_title,
            url,
            price_minor,
            currency,
        )
        return int(camp["id"])


async def list_seller_campaigns(pool: asyncpg.Pool, *, seller_tg_user_id: int, limit: int = 10) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.status, c.created_at
            FROM campaigns c
            JOIN shops sh ON sh.id = c.shop_id
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT $2;
            """,
            seller_tg_user_id,
            limit,
        )
        return [{"id": int(r["id"]), "status": str(r["status"]), "created_at": r["created_at"]} for r in rows]


async def get_campaign_for_seller(
    pool: asyncpg.Pool, *, seller_tg_user_id: int, campaign_id: int
) -> dict | None:
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT c.id, c.status, c.created_at, c.text, c.button_title, c.url, c.price_minor, c.currency
            FROM campaigns c
            JOIN shops sh ON sh.id = c.shop_id
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1 AND c.id=$2;
            """,
            seller_tg_user_id,
            campaign_id,
        )
        if r is None:
            return None
        return {
            "id": int(r["id"]),
            "status": str(r["status"]),
            "created_at": r["created_at"],
            "text": str(r["text"]),
            "button_title": str(r["button_title"]) if r["button_title"] is not None else "",
            "url": str(r["url"]) if r["url"] is not None else "",
            "price_minor": int(r["price_minor"]),
            "currency": str(r["currency"]),
        }

async def mark_campaign_paid(
    pool: asyncpg.Pool,
    *,
    campaign_id: int,
    tg_payment_charge_id: str,
    provider_payment_charge_id: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE campaigns
            SET status='paid',
                paid_at=now(),
                tg_payment_charge_id=$1,
                provider_payment_charge_id=$2
            WHERE id=$3;
            """,
            tg_payment_charge_id,
            provider_payment_charge_id,
            campaign_id,
        )
