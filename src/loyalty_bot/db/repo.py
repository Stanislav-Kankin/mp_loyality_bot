from __future__ import annotations

import asyncpg


# ------------------------
# Sellers
# ------------------------

_DEFAULT_FREE_CREDITS_ON_SIGNUP = 3


async def ensure_seller(pool: asyncpg.Pool, tg_user_id: int) -> int:
    """Ensure seller exists.

    Also ensures a seller_credits row exists; if it's created for the first time,
    grants a small free balance (MVP: 3 campaigns).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO sellers(tg_user_id)
                VALUES ($1)
                ON CONFLICT (tg_user_id) DO UPDATE SET tg_user_id = EXCLUDED.tg_user_id
                RETURNING id;
                """,
                tg_user_id,
            )
            seller_id = int(row["id"])

            # Create balance row once; if created now, grant free credits.
            ins = await conn.fetchrow(
                """
                INSERT INTO seller_credits(seller_id, balance)
                VALUES ($1, $2)
                ON CONFLICT (seller_id) DO NOTHING
                RETURNING seller_id;
                """,
                seller_id,
                _DEFAULT_FREE_CREDITS_ON_SIGNUP,
            )
            if ins is not None:
                await conn.execute(
                    """
                    INSERT INTO seller_credit_transactions(seller_id, delta, reason, balance_after)
                    VALUES ($1, $2, 'free_signup', $3);
                    """,
                    seller_id,
                    _DEFAULT_FREE_CREDITS_ON_SIGNUP,
                    _DEFAULT_FREE_CREDITS_ON_SIGNUP,
                )

            return seller_id


async def get_seller_credits(pool: asyncpg.Pool, *, seller_tg_user_id: int) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT sc.balance
            FROM sellers s
            JOIN seller_credits sc ON sc.seller_id = s.id
            WHERE s.tg_user_id=$1;
            """,
            seller_tg_user_id,
        )
        if row is None:
            return 0
        return int(row["balance"] or 0)


async def add_seller_credits(
    pool: asyncpg.Pool,
    *,
    seller_id: int,
    delta: int,
    reason: str,
    invoice_payload: str | None = None,
    tg_payment_charge_id: str | None = None,
    provider_payment_charge_id: str | None = None,
    campaign_id: int | None = None,
) -> int:
    """Adjust seller credits and write a ledger transaction.

    Returns the new balance.
    """
    if delta == 0:
        # no-op
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT balance FROM seller_credits WHERE seller_id=$1;", seller_id)
            return int(row["balance"] or 0) if row else 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE seller_credits
                SET balance = balance + $2,
                    updated_at = now()
                WHERE seller_id = $1
                RETURNING balance;
                """,
                seller_id,
                delta,
            )
            if row is None:
                raise ValueError("seller_credits_missing")

            new_balance = int(row["balance"] or 0)
            await conn.execute(
                """
                INSERT INTO seller_credit_transactions(
                    seller_id, delta, reason, created_at,
                    campaign_id, tg_payment_charge_id, provider_payment_charge_id,
                    invoice_payload, balance_after
                )
                VALUES ($1, $2, $3, now(), $4, $5, $6, $7, $8);
                """,
                seller_id,
                delta,
                reason,
                campaign_id,
                tg_payment_charge_id,
                provider_payment_charge_id,
                invoice_payload,
                new_balance,
            )
            return new_balance


# ------------------------
# Customers
# ------------------------


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


async def update_shop_welcome(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    shop_id: int,
    welcome_text: str,
    welcome_photo_file_id: str | None,
    welcome_button_text: str | None,
    welcome_url: str | None,
) -> None:
    async with pool.acquire() as conn:
        owned = await conn.fetchrow(
            """
            SELECT 1
            FROM shops sh
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1 AND sh.id=$2;
            """,
            seller_tg_user_id,
            shop_id,
        )
        if owned is None:
            raise ValueError("shop_not_owned")

        await conn.execute(
            """
            UPDATE shops
            SET welcome_text=$2,
                welcome_photo_file_id=$3,
                welcome_button_text=$4,
                welcome_url=$5
            WHERE id=$1;
            """,
            shop_id,
            welcome_text,
            welcome_photo_file_id,
            welcome_button_text,
            welcome_url,
        )


async def get_shop_welcome(pool: asyncpg.Pool, *, shop_id: int) -> dict | None:
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT welcome_text, welcome_photo_file_id, welcome_button_text, welcome_url
            FROM shops
            WHERE id=$1;
            """,
            shop_id,
        )
        if r is None:
            return None
        return {
            "welcome_text": str(r["welcome_text"] or ""),
            "welcome_photo_file_id": str(r["welcome_photo_file_id"] or "") or None,
            "welcome_button_text": str(r["welcome_button_text"] or "") or None,
            "welcome_url": str(r["welcome_url"] or "") or None,
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
    photo_file_id: str | None,
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
            INSERT INTO campaigns(shop_id, status, text, button_title, url, photo_file_id, price_minor, currency)
            VALUES ($1, 'draft', $2, $3, $4, $5, $6, $7)
            RETURNING id;
            """,
            shop_id,
            text,
            button_title,
            url,
            photo_file_id,
            price_minor,
            currency,
        )
        return int(camp["id"])


async def list_seller_campaigns(pool: asyncpg.Pool, *, seller_tg_user_id: int, limit: int = 10) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.status, c.created_at, c.shop_id, sh.name AS shop_name
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
        return [
            {
                "id": int(r["id"]),
                "status": str(r["status"]),
                "created_at": r["created_at"],
                "shop_id": int(r["shop_id"]),
                "shop_name": str(r["shop_name"]),
            }
            for r in rows
        ]


async def list_shop_campaigns(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    shop_id: int,
    limit: int = 10,
) -> list[dict]:
    """Return last campaigns for a specific shop owned by seller."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.status, c.created_at, c.shop_id, sh.name AS shop_name
            FROM campaigns c
            JOIN shops sh ON sh.id = c.shop_id
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1 AND sh.id=$2
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT $3;
            """,
            seller_tg_user_id,
            shop_id,
            limit,
        )
        return [
            {
                "id": int(r["id"]),
                "status": str(r["status"]),
                "created_at": r["created_at"],
                "shop_id": int(r["shop_id"]),
                "shop_name": str(r["shop_name"]),
            }
            for r in rows
        ]


async def get_campaign_for_seller(pool: asyncpg.Pool, *, seller_tg_user_id: int, campaign_id: int) -> dict | None:
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            """
            SELECT c.id, c.shop_id, sh.name AS shop_name,
                   c.status, c.created_at, c.text, c.button_title, c.url, c.photo_file_id, c.price_minor, c.currency
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
            "shop_id": int(r["shop_id"]),
            "shop_name": str(r["shop_name"]),
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


async def mark_campaign_paid_test(pool: asyncpg.Pool, *, campaign_id: int) -> None:
    # For development only (PAYMENTS_TEST_MODE).
    await mark_campaign_paid(
        pool,
        campaign_id=campaign_id,
        tg_payment_charge_id="test_mode",
        provider_payment_charge_id="test_mode",
    )


# ------------------------
# Campaign sending (worker)
# ------------------------


async def start_campaign_sending(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    campaign_id: int,
) -> int:
    """Start campaign sending.

    - Verifies campaign belongs to seller and is in "paid" status.
    - Consumes 1 seller credit atomically.
    - Enqueues deliveries for all subscribed customers of the campaign shop.

    Returns: total recipients count.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            camp = await conn.fetchrow(
                """
                SELECT c.id, c.shop_id, c.status, s.id AS seller_id
                FROM campaigns c
                JOIN shops sh ON sh.id = c.shop_id
                JOIN sellers s ON s.id = sh.seller_id
                WHERE s.tg_user_id=$1 AND c.id=$2
                FOR UPDATE;
                """,
                seller_tg_user_id,
                campaign_id,
            )
            if camp is None:
                raise ValueError("campaign_not_found")
            if str(camp["status"]) != "paid":
                raise ValueError("campaign_not_paid")

            seller_id = int(camp["seller_id"])

            # Consume 1 credit.
            bal = await conn.fetchrow(
                """
                UPDATE seller_credits
                SET balance = balance - 1,
                    updated_at = now()
                WHERE seller_id=$1 AND balance > 0
                RETURNING balance;
                """,
                seller_id,
            )
            if bal is None:
                raise ValueError("no_credits")
            new_balance = int(bal["balance"])

            await conn.execute(
                """
                INSERT INTO seller_credit_transactions(
                    seller_id, delta, reason, created_at,
                    campaign_id, balance_after
                )
                VALUES ($1, -1, 'campaign_send', now(), $2, $3);
                """,
                seller_id,
                campaign_id,
                new_balance,
            )

            # Enqueue deliveries (idempotent).
            await conn.execute(
                """
                INSERT INTO campaign_deliveries(campaign_id, customer_id, status, next_attempt_at)
                SELECT $1, sc.customer_id, 'pending', now()
                FROM shop_customers sc
                WHERE sc.shop_id=$2 AND sc.status='subscribed'
                ON CONFLICT (campaign_id, customer_id) DO NOTHING;
                """,
                campaign_id,
                int(camp["shop_id"]),
            )

            total = await conn.fetchval(
                "SELECT COUNT(*) FROM campaign_deliveries WHERE campaign_id=$1;",
                campaign_id,
            )
            total_i = int(total or 0)

            await conn.execute(
                """
                UPDATE campaigns
                SET status='sending',
                    total_recipients=$2,
                    sent_count=0,
                    failed_count=0,
                    blocked_count=0
                WHERE id=$1;
                """,
                campaign_id,
                total_i,
            )

            return total_i


async def lease_due_deliveries(
    pool: asyncpg.Pool,
    *,
    batch_size: int,
    lease_seconds: int = 300,
) -> list[dict]:
    """Claim a batch of due deliveries using SKIP LOCKED and set a short lease.

    We do not keep row locks during network IO.
    Instead, we move next_attempt_at into the future (lease) inside a transaction.
    """
    if batch_size <= 0:
        return []

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT d.id AS delivery_id,
                       d.campaign_id,
                       d.customer_id,
                       d.attempt_count,
                       cu.tg_user_id AS tg_user_id,
                       c.text,
                       c.button_title,
                       c.url,
                       c.photo_file_id
                FROM campaign_deliveries d
                JOIN campaigns c ON c.id = d.campaign_id
                JOIN customers cu ON cu.id = d.customer_id
                WHERE d.status='pending'
                  AND d.next_attempt_at <= now()
                  AND c.status='sending'
                ORDER BY d.next_attempt_at ASC, d.id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT $1;
                """,
                batch_size,
            )

            if not rows:
                return []

            ids = [int(r["delivery_id"]) for r in rows]
            await conn.execute(
                """
                UPDATE campaign_deliveries
                SET attempt_count = attempt_count + 1,
                    next_attempt_at = now() + ($2::int * interval '1 second')
                WHERE id = ANY($1::bigint[]);
                """,
                ids,
                int(lease_seconds),
            )

            return [
                {
                    "delivery_id": int(r["delivery_id"]),
                    "campaign_id": int(r["campaign_id"]),
                    "customer_id": int(r["customer_id"]),
                    "attempt": int(r["attempt_count"] or 0) + 1,
                    "tg_user_id": int(r["tg_user_id"]),
                    "text": str(r["text"]),
                    "button_title": str(r["button_title"] or ""),
                    "url": str(r["url"] or ""),
                    "photo_file_id": str(r["photo_file_id"] or "") or None,
                }
                for r in rows
            ]


async def mark_delivery_sent(
    pool: asyncpg.Pool,
    *,
    delivery_id: int,
    campaign_id: int,
    tg_message_id: int,
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE campaign_deliveries
                SET status='sent', sent_at=now(), tg_message_id=$2, last_error=NULL
                WHERE id=$1;
                """,
                delivery_id,
                tg_message_id,
            )
            await conn.execute(
                "UPDATE campaigns SET sent_count = sent_count + 1 WHERE id=$1;",
                campaign_id,
            )


async def mark_delivery_blocked(
    pool: asyncpg.Pool,
    *,
    delivery_id: int,
    campaign_id: int,
    last_error: str,
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE campaign_deliveries
                SET status='blocked', sent_at=now(), last_error=$2
                WHERE id=$1;
                """,
                delivery_id,
                last_error[:5000],
            )
            await conn.execute(
                "UPDATE campaigns SET blocked_count = blocked_count + 1 WHERE id=$1;",
                campaign_id,
            )


async def mark_delivery_failed(
    pool: asyncpg.Pool,
    *,
    delivery_id: int,
    campaign_id: int,
    last_error: str,
) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE campaign_deliveries
                SET status='failed', sent_at=now(), last_error=$2
                WHERE id=$1;
                """,
                delivery_id,
                last_error[:5000],
            )
            await conn.execute(
                "UPDATE campaigns SET failed_count = failed_count + 1 WHERE id=$1;",
                campaign_id,
            )


async def reschedule_delivery(
    pool: asyncpg.Pool,
    *,
    delivery_id: int,
    next_attempt_in_seconds: int,
    last_error: str,
) -> None:
    delay = max(1, int(next_attempt_in_seconds))
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE campaign_deliveries
            SET status='pending',
                next_attempt_at = now() + ($2::int * interval '1 second'),
                last_error=$3
            WHERE id=$1;
            """,
            delivery_id,
            delay,
            last_error[:5000],
        )


async def finalize_completed_campaigns(pool: asyncpg.Pool) -> int:
    """Mark campaigns as completed when they have no pending deliveries left."""
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            """
            WITH candidates AS (
                SELECT c.id
                FROM campaigns c
                WHERE c.status='sending'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM campaign_deliveries d
                      WHERE d.campaign_id=c.id AND d.status='pending'
                  )
            )
            UPDATE campaigns c
            SET status='completed'
            FROM candidates
            WHERE c.id=candidates.id
            RETURNING (SELECT COUNT(*) FROM candidates);
            """,
        )
        return int(row or 0)


async def record_campaign_click(
    pool: asyncpg.Pool,
    *,
    campaign_id: int,
    customer_tg_user_id: int,
) -> bool:
    """Record a unique click and increment campaign counter.

    Returns True if it was a new click.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH cust AS (
                    SELECT id FROM customers WHERE tg_user_id=$2
                ), ins AS (
                    INSERT INTO clicks(campaign_id, customer_id)
                    SELECT $1, cust.id FROM cust
                    ON CONFLICT DO NOTHING
                    RETURNING 1
                )
                SELECT EXISTS(SELECT 1 FROM ins) AS inserted;
                """,
                campaign_id,
                customer_tg_user_id,
            )
            inserted = bool(row and row["inserted"])
            if inserted:
                await conn.execute(
                    "UPDATE campaigns SET click_count = click_count + 1 WHERE id=$1;",
                    campaign_id,
                )
            return inserted


async def get_campaign_url(pool: asyncpg.Pool, *, campaign_id: int) -> str | None:
    async with pool.acquire() as conn:
        url = await conn.fetchval("SELECT url FROM campaigns WHERE id=$1;", campaign_id)
        if url is None:
            return None
        return str(url)
