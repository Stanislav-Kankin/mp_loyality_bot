from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.types import PreCheckoutQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.db.repo import get_campaign_for_seller, mark_campaign_paid

router = Router()


def _parse_payload(payload: str) -> int | None:
    # payload format: "campaign:<id>"
    if not payload:
        return None
    if not payload.startswith("campaign:"):
        return None
    raw = payload.split(":", 1)[1]
    if not raw.isdigit():
        return None
    return int(raw)


@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery, pool: asyncpg.Pool) -> None:
    tg_id = pre.from_user.id
    campaign_id = _parse_payload(pre.invoice_payload)
    if campaign_id is None:
        await pre.answer(ok=False, error_message="Некорректный платеж. Попробуйте снова.")
        return

    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await pre.answer(ok=False, error_message="Кампания не найдена.")
        return

    # Validate amount & currency
    if pre.total_amount != int(camp["price_minor"]) or pre.currency != str(camp["currency"]):
        await pre.answer(ok=False, error_message="Сумма/валюта не совпадают. Пересоздайте оплату.")
        return

    # For MVP: allow payment only for draft status
    if str(camp["status"]) not in ("draft", "unpaid"):
        await pre.answer(ok=False, error_message="Эта кампания уже оплачена или недоступна.")
        return

    await pre.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else 0
    sp = message.successful_payment
    campaign_id = _parse_payload(sp.invoice_payload)
    if campaign_id is None:
        await message.answer("Оплата получена, но не удалось определить кампанию. Напишите администратору.")
        return

    # Double-check ownership
    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await message.answer("Оплата получена, но кампания не найдена. Напишите администратору.")
        return

    await mark_campaign_paid(
        pool,
        campaign_id=campaign_id,
        tg_payment_charge_id=sp.telegram_payment_charge_id,
        provider_payment_charge_id=sp.provider_payment_charge_id,
    )

    await message.answer(f"Оплата получена ✅\nКампания #{campaign_id} теперь оплачена.")
