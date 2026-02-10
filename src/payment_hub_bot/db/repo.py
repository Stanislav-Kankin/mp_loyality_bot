from __future__ import annotations

import asyncpg


async def get_payment_order_for_buyer(
    pool: asyncpg.Pool,
    *,
    order_id: str,
    buyer_tg_id: int,
) -> asyncpg.Record | None:
    q = (
        "SELECT id, instance_id, buyer_tg_id, pack_code, amount_minor, currency, status, created_at, paid_at, fulfilled_at, "
        "provider_payment_charge_id, invoice_payload "
        "FROM payment_orders "
        "WHERE id = $1::uuid AND buyer_tg_id = $2"
    )
    async with pool.acquire() as conn:
        return await conn.fetchrow(q, order_id, buyer_tg_id)


async def mark_order_paid(
    pool: asyncpg.Pool,
    *,
    order_id: str,
    provider_payment_charge_id: str,
) -> bool:
    """Mark order as paid.

    Returns True if status was changed to 'paid' in this call.
    """
    q = (
        "UPDATE payment_orders "
        "SET status = 'paid', paid_at = now(), provider_payment_charge_id = $2 "
        "WHERE id = $1::uuid AND status = 'pending' "
        "RETURNING id"
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(q, order_id, provider_payment_charge_id)
    return row is not None
