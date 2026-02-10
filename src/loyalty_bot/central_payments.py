from __future__ import annotations

import datetime
import logging
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from loyalty_bot.config import settings


logger = logging.getLogger(__name__)


def new_order_id() -> str:
    return str(uuid4())


def build_invoice_payload(order_id: str) -> str:
    return f"order:{order_id}"


def build_hub_start_payload(order_id: str) -> str:
    return f"pay_{order_id}"


def build_hub_deeplink(order_id: str) -> str:
    username = (settings.hub_bot_username or "").strip().lstrip("@")
    return f"https://t.me/{username}?start={build_hub_start_payload(order_id)}"


def pack_code_from_qty(qty: int) -> str:
    return f"pack_{int(qty)}"


def pack_minor_amount_from_qty(qty: int) -> int:
    mapping = {
        1: int(settings.credits_pack_1_minor),
        3: int(settings.credits_pack_3_minor),
        10: int(settings.credits_pack_10_minor),
    }
    return mapping[int(qty)]


async def create_payment_order(
    central_pool: asyncpg.Pool,
    *,
    buyer_tg_id: int,
    qty: int,
) -> dict[str, Any]:
    """Create a pending payment order in CENTRAL DB and return it."""
    instance_id = (settings.instance_id or "").strip()
    if not instance_id:
        raise ValueError("INSTANCE_ID is not configured")

    order_id = new_order_id()
    invoice_payload = build_invoice_payload(order_id)
    pack_code = pack_code_from_qty(qty)
    amount_minor = pack_minor_amount_from_qty(qty)
    currency = (settings.currency or "RUB").strip()

    async with central_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO payment_orders(
                id, instance_id, buyer_tg_id,
                pack_code, amount_minor, currency,
                status, invoice_payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7);
            """,
            UUID(order_id),
            instance_id,
            int(buyer_tg_id),
            pack_code,
            int(amount_minor),
            currency,
            invoice_payload,
        )

        row = await conn.fetchrow(
            """
            SELECT id::text AS id, instance_id, buyer_tg_id, pack_code,
                   amount_minor, currency, status, created_at, paid_at, fulfilled_at,
                   provider_payment_charge_id, invoice_payload
            FROM payment_orders
            WHERE id = $1;
            """,
            UUID(order_id),
        )
    return dict(row) if row else {"id": order_id, "invoice_payload": invoice_payload}


async def get_payment_order(
    central_pool: asyncpg.Pool,
    *,
    order_id: str,
    buyer_tg_id: int,
) -> dict[str, Any] | None:
    try:
        oid = UUID(order_id)
    except Exception:
        return None

    instance_id = (settings.instance_id or "").strip()
    if not instance_id:
        return None

    async with central_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text AS id, instance_id, buyer_tg_id, pack_code,
                   amount_minor, currency, status, created_at, paid_at, fulfilled_at,
                   provider_payment_charge_id, invoice_payload
            FROM payment_orders
            WHERE id = $1 AND instance_id = $2 AND buyer_tg_id = $3;
            """,
            oid,
            instance_id,
            int(buyer_tg_id),
        )
    return dict(row) if row else None


async def mark_order_fulfilled(
    central_pool: asyncpg.Pool,
    *,
    order_id: str,
    buyer_tg_id: int,
) -> bool:
    """Mark order as fulfilled (idempotent)."""
    try:
        oid = UUID(order_id)
    except Exception:
        return False

    instance_id = (settings.instance_id or "").strip()
    if not instance_id:
        return False

    async with central_pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE payment_orders
            SET status = 'fulfilled', fulfilled_at = now()
            WHERE id = $1 AND instance_id = $2 AND buyer_tg_id = $3
              AND status IN ('paid', 'fulfilled');
            """,
            oid,
            instance_id,
            int(buyer_tg_id),
        )

    # asyncpg returns like "UPDATE 1"
    return str(res).startswith("UPDATE ") and not str(res).endswith(" 0")
