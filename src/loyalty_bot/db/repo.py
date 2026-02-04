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


async def has_seller_credit_tx_by_tg_charge_id(
    pool: asyncpg.Pool,
    *,
    seller_id: int,
    tg_payment_charge_id: str | None,
) -> bool:
    """Return True if a seller_credit_transactions row exists for this Telegram charge id.

    Used for idempotency: Telegram can re-deliver successful_payment updates.
    """
    if not tg_payment_charge_id:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1
            FROM seller_credit_transactions
            WHERE seller_id=$1 AND tg_payment_charge_id=$2
            LIMIT 1;
            """,
            seller_id,
            tg_payment_charge_id,
        )
        return row is not None


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


async def get_shop_customer_status(pool: asyncpg.Pool, *, shop_id: int, customer_id: int) -> str | None:
    """Return shop subscription status for a customer.

    Returns one of:
      - 'subscribed'
      - 'unsubscribed'
      - None (no record)
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status
            FROM shop_customers
            WHERE shop_id=$1 AND customer_id=$2;
            """,
            shop_id,
            customer_id,
        )
        return str(row["status"]) if row else None


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


async def get_customer_subscribed_shops(pool: asyncpg.Pool, *, customer_id: int) -> list[dict[str, object]]:
    """List shops where customer has active subscription.

    Returns list of dicts: {shop_id:int, name:str}
    Ordered by subscribed_at DESC.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sc.shop_id, s.name
            FROM shop_customers sc
            JOIN shops s ON s.id = sc.shop_id
            WHERE sc.customer_id = $1
              AND sc.status = 'subscribed'
            ORDER BY sc.subscribed_at DESC NULLS LAST, sc.shop_id DESC;
            """,
            customer_id,
        )
        return [{"shop_id": int(r["shop_id"]), "name": str(r["name"])} for r in rows]


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




async def update_campaign_draft(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    campaign_id: int,
    text: str | None = None,
    button_title: str | None = None,
    url: str | None = None,
    photo_file_id: str | None = None,
) -> None:
    """Update draft campaign fields.

    Only campaigns with status='draft' can be edited in MVP.
    """

    fields: list[str] = []
    args: list[object] = []
    idx = 1

    if text is not None:
        fields.append(f"text=${idx}")
        args.append(text)
        idx += 1
    if button_title is not None:
        fields.append(f"button_title=${idx}")
        args.append(button_title)
        idx += 1
    if url is not None:
        fields.append(f"url=${idx}")
        args.append(url)
        idx += 1
    # photo_file_id may be set to None explicitly
    if photo_file_id is not None:
        fields.append(f"photo_file_id=${idx}")
        args.append(photo_file_id)
        idx += 1

    if not fields:
        return

    args.extend([seller_tg_user_id, campaign_id])

    async with pool.acquire() as conn:
        # Ensure seller owns campaign AND it is editable.
        row = await conn.fetchrow(
            f"""
            UPDATE campaigns c
            SET {', '.join(fields)}
            FROM shops sh
            JOIN sellers s ON s.id = sh.seller_id
            WHERE c.shop_id = sh.id
              AND s.tg_user_id=${idx}
              AND c.id=${idx + 1}
              AND c.status='draft'
            RETURNING c.id;
            """,
            *args,
        )
        if row is None:
            raise ValueError('campaign_not_editable')
async def list_seller_campaigns(pool: asyncpg.Pool, *, seller_tg_user_id: int, limit: int = 10) -> list[dict]:
    items, _has_next = await list_seller_campaigns_page(
        pool,
        seller_tg_user_id=seller_tg_user_id,
        limit=limit,
        offset=0,
    )
    return items


async def list_seller_campaigns_page(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict], bool]:
    """Return a page of campaigns for seller.

    Returns (items, has_next).
    """
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50
    if offset < 0:
        offset = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.status, c.created_at, c.shop_id, sh.name AS shop_name
            FROM campaigns c
            JOIN shops sh ON sh.id = c.shop_id
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1
            ORDER BY c.created_at DESC, c.id DESC
            OFFSET $2
            LIMIT $3;
            """,
            seller_tg_user_id,
            offset,
            limit + 1,
        )

        has_next = len(rows) > limit
        rows = rows[:limit]

        return (
            [
                {
                    "id": int(r["id"]),
                    "status": str(r["status"]),
                    "created_at": r["created_at"],
                    "shop_id": int(r["shop_id"]),
                    "shop_name": str(r["shop_name"]),
                }
                for r in rows
            ],
            has_next,
        )


async def list_shop_campaigns(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    shop_id: int,
    limit: int = 10,
) -> list[dict]:
    items, _has_next = await list_shop_campaigns_page(
        pool,
        seller_tg_user_id=seller_tg_user_id,
        shop_id=shop_id,
        limit=limit,
        offset=0,
    )
    return items


async def list_shop_campaigns_page(
    pool: asyncpg.Pool,
    *,
    seller_tg_user_id: int,
    shop_id: int,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict], bool]:
    """Return a page of campaigns for a specific shop owned by seller.

    Returns (items, has_next).
    """
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50
    if offset < 0:
        offset = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.status, c.created_at, c.shop_id, sh.name AS shop_name
            FROM campaigns c
            JOIN shops sh ON sh.id = c.shop_id
            JOIN sellers s ON s.id = sh.seller_id
            WHERE s.tg_user_id=$1 AND sh.id=$2
            ORDER BY c.created_at DESC, c.id DESC
            OFFSET $3
            LIMIT $4;
            """,
            seller_tg_user_id,
            shop_id,
            offset,
            limit + 1,
        )

        has_next = len(rows) > limit
        rows = rows[:limit]

        return (
            [
                {
                    "id": int(r["id"]),
                    "status": str(r["status"]),
                    "created_at": r["created_at"],
                    "shop_id": int(r["shop_id"]),
                    "shop_name": str(r["shop_name"]),
                }
                for r in rows
            ],
            has_next,
        )


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
            "photo_file_id": str(r["photo_file_id"] or "") or None,
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

    - Verifies campaign belongs to seller.
    - Rejects campaigns already in progress / finished.
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

            status = str(camp["status"] or "")
            if status in {"sending", "completed", "sent"}:
                raise ValueError("campaign_already_started")
            if status in {"canceled", "cancelled"}:
                raise ValueError("campaign_invalid_status")

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


# ------------------------
# Seller access (DB allowlist) + Admin analytics
# ------------------------


async def is_seller_allowed(pool: asyncpg.Pool, tg_user_id: int) -> bool:
    """Return True if tg_user_id is allowed to use seller panel via DB allowlist."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1
            FROM seller_access
            WHERE tg_user_id=$1 AND is_active=TRUE
            LIMIT 1;
            """,
            tg_user_id,
        )
        return row is not None


async def upsert_seller_access(
    pool: asyncpg.Pool,
    *,
    tg_user_id: int,
    is_active: bool = True,
    note: str | None = None,
    added_by_tg_user_id: int | None = None,
) -> None:
    """Insert or update a seller access entry."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO seller_access(tg_user_id, is_active, note, added_by_tg_user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (tg_user_id)
            DO UPDATE SET
                is_active = EXCLUDED.is_active,
                note = EXCLUDED.note,
                added_by_tg_user_id = COALESCE(EXCLUDED.added_by_tg_user_id, seller_access.added_by_tg_user_id),
                updated_at = now();
            """,
            tg_user_id,
            is_active,
            note,
            added_by_tg_user_id,
        )


async def set_seller_access_active(pool: asyncpg.Pool, *, tg_user_id: int, is_active: bool) -> None:
    """Enable/disable seller access. Creates seller_access row if missing."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO seller_access(tg_user_id, is_active)
            VALUES ($1, $2)
            ON CONFLICT (tg_user_id) DO UPDATE
            SET is_active = EXCLUDED.is_active,
                updated_at = now();
            """,
            tg_user_id,
            is_active,
        )


async def get_admin_overview(pool: asyncpg.Pool) -> dict:
    """Return basic platform stats for admin panel."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM sellers) AS sellers_total,
              (SELECT COUNT(*) FROM seller_access WHERE is_active=TRUE) AS sellers_allowed,
              (SELECT COUNT(*) FROM shops WHERE is_active=TRUE) AS shops_active,
              (SELECT COUNT(*) FROM campaigns) AS campaigns_total,
              (SELECT COUNT(*) FROM campaigns WHERE created_at >= now() - interval '7 days') AS campaigns_7d,
              (SELECT COALESCE(SUM(balance), 0) FROM seller_credits) AS credits_total
            ;
            """
        )
        return {
            "sellers_total": int(row["sellers_total"] or 0),
            "sellers_allowed": int(row["sellers_allowed"] or 0),
            "shops_active": int(row["shops_active"] or 0),
            "campaigns_total": int(row["campaigns_total"] or 0),
            "campaigns_7d": int(row["campaigns_7d"] or 0),
            "credits_total": int(row["credits_total"] or 0),
        }


async def list_admin_sellers_page(
    pool: asyncpg.Pool,
    *,
    offset: int,
    limit: int,
) -> tuple[list[dict], bool]:
    """List all sellers with basic metrics (paged). Returns (items, has_next)."""
    page_size = max(1, min(int(limit), 50))
    off = max(0, int(offset))

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH base AS (
              SELECT s.id AS seller_id, s.tg_user_id, s.created_at
              FROM sellers s
              ORDER BY s.created_at DESC
              OFFSET $1
              LIMIT $2
            )
            SELECT
              b.tg_user_id,
              COALESCE(sa.is_active, FALSE) AS is_active,
              b.created_at,
              COALESCE(sc.balance, 0) AS credits,
              COALESCE(sh.cnt, 0) AS shops_count,
              COALESCE(cp.cnt, 0) AS campaigns_count,
              COALESCE(sp.spent, 0) AS spent_total,
              cp.last_campaign_at
            FROM base b
            LEFT JOIN seller_access sa ON sa.tg_user_id = b.tg_user_id
            LEFT JOIN seller_credits sc ON sc.seller_id = b.seller_id
            LEFT JOIN (
              SELECT seller_id, COUNT(*) AS cnt
              FROM shops
              GROUP BY seller_id
            ) sh ON sh.seller_id = b.seller_id
            LEFT JOIN (
              SELECT sh2.seller_id, COUNT(c.*) AS cnt, MAX(c.created_at) AS last_campaign_at
              FROM shops sh2
              LEFT JOIN campaigns c ON c.shop_id = sh2.id
              GROUP BY sh2.seller_id
            ) cp ON cp.seller_id = b.seller_id
            LEFT JOIN (
              SELECT t.seller_id, COALESCE(SUM(CASE WHEN t.delta < 0 THEN -t.delta ELSE 0 END), 0) AS spent
              FROM seller_credit_transactions t
              GROUP BY t.seller_id
            ) sp ON sp.seller_id = b.seller_id
            ORDER BY b.created_at DESC;
            """,
            off,
            page_size + 1,
        )

    has_next = len(rows) > page_size
    rows = rows[:page_size]

    items: list[dict] = []
    for r in rows:
        items.append(
            {
                "tg_user_id": int(r["tg_user_id"]),
                "is_active": bool(r["is_active"]),
                "created_at": r["created_at"],
                "credits": int(r["credits"] or 0),
                "shops_count": int(r["shops_count"] or 0),
                "campaigns_count": int(r["campaigns_count"] or 0),
                "spent_total": int(r["spent_total"] or 0),
                "last_campaign_at": r["last_campaign_at"],
            }
        )

    return items, has_next


async def get_admin_seller_details(pool: asyncpg.Pool, *, tg_user_id: int) -> dict | None:
    """Return detailed seller metrics for admin panel. Works even if seller_access row is missing."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              s.tg_user_id,
              COALESCE(sa.is_active, FALSE) AS is_active,
              sa.note,
              COALESCE(sa.created_at, s.created_at) AS created_at,
              s.id AS seller_id,
              COALESCE(sc.balance, 0) AS credits,
              COALESCE(sh.cnt, 0) AS shops_count,
              COALESCE(cp.cnt, 0) AS campaigns_count,
              COALESCE(sp.spent, 0) AS spent_total,
              cp.last_campaign_at
            FROM sellers s
            LEFT JOIN seller_access sa ON sa.tg_user_id = s.tg_user_id
            LEFT JOIN seller_credits sc ON sc.seller_id = s.id
            LEFT JOIN (
              SELECT seller_id, COUNT(*) AS cnt
              FROM shops
              GROUP BY seller_id
            ) sh ON sh.seller_id = s.id
            LEFT JOIN (
              SELECT sh2.seller_id, COUNT(c.*) AS cnt, MAX(c.created_at) AS last_campaign_at
              FROM shops sh2
              LEFT JOIN campaigns c ON c.shop_id = sh2.id
              GROUP BY sh2.seller_id
            ) cp ON cp.seller_id = s.id
            LEFT JOIN (
              SELECT t.seller_id, COALESCE(SUM(CASE WHEN t.delta < 0 THEN -t.delta ELSE 0 END), 0) AS spent
              FROM seller_credit_transactions t
              GROUP BY t.seller_id
            ) sp ON sp.seller_id = s.id
            WHERE s.tg_user_id=$1
            LIMIT 1;
            """,
            tg_user_id,
        )
        if row is None:
            return None

        seller_id = row["seller_id"]
        return {
            "tg_user_id": int(row["tg_user_id"]),
            "is_active": bool(row["is_active"]),
            "note": row["note"],
            "created_at": row["created_at"],
            "seller_id": int(seller_id) if seller_id is not None else None,
            "credits": int(row["credits"] or 0),
            "shops_count": int(row["shops_count"] or 0),
            "campaigns_count": int(row["campaigns_count"] or 0),
            "spent_total": int(row["spent_total"] or 0),
            "last_campaign_at": row["last_campaign_at"],
        }
