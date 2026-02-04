from __future__ import annotations

import logging

import asyncpg
from aiogram import F, Router
from aiogram.types import PreCheckoutQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.db.repo import (
    add_seller_credits,
    ensure_seller,
    get_campaign_for_seller,
    get_seller_credits,
    has_seller_credit_tx_by_tg_charge_id,
    mark_campaign_paid,
)

router = Router()

logger = logging.getLogger(__name__)


def _parse_invoice_payload(payload: str) -> dict | None:
    """Parse Telegram invoice payload.

    Supported formats:
      - campaign:<id>
      - credits_pack:<qty>[:ctx]
    """
    if not payload:
        return None

    if payload.startswith("campaign:"):
        raw = payload.split(":", 1)[1]
        if raw.isdigit():
            return {"kind": "campaign", "campaign_id": int(raw)}
        return None

    if payload.startswith("credits_pack:"):
        parts = payload.split(":")
        # parts: [credits_pack, qty, ctx?]
        if len(parts) >= 2 and parts[1].isdigit():
            qty = int(parts[1])
            if qty in (1, 3, 10):
                ctx = parts[2] if len(parts) >= 3 and parts[2] else None
                return {"kind": "credits_pack", "qty": qty, "ctx": ctx}
        return None

    return None


@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery, pool: asyncpg.Pool) -> None:
    tg_id = pre.from_user.id
    info = _parse_invoice_payload(pre.invoice_payload)
    if info is None:
        logger.info("pre_checkout invalid payload tg_id=%s payload=%s", tg_id, pre.invoice_payload)
        await pre.answer(ok=False, error_message="Некорректный платеж. Попробуйте снова.")
        return

    logger.info(
        "pre_checkout received tg_id=%s kind=%s amount=%s currency=%s payload=%s",
        tg_id,
        info.get("kind"),
        pre.total_amount,
        pre.currency,
        pre.invoice_payload,
    )

    if info["kind"] == "credits_pack":
        qty = int(info["qty"])
        expected_minor = {
            1: settings.credits_pack_1_minor,
            3: settings.credits_pack_3_minor,
            10: settings.credits_pack_10_minor,
        }[qty]
        if pre.currency != settings.currency or pre.total_amount != int(expected_minor):
            await pre.answer(ok=False, error_message="Сумма/валюта не совпадают. Пересоздайте оплату.")
            return
        await pre.answer(ok=True)
        return

    # --- campaign payment (legacy, if used) ---
    campaign_id = int(info["campaign_id"])

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
    info = _parse_invoice_payload(sp.invoice_payload)
    if info is None:
        logger.info("successful_payment invalid payload tg_id=%s payload=%s", tg_id, sp.invoice_payload)
        await message.answer("Оплата получена, но не удалось определить назначение платежа. Напишите администратору.")
        return

    logger.info(
        "successful_payment received tg_id=%s kind=%s currency=%s total=%s tg_charge=%s provider_charge=%s payload=%s",
        tg_id,
        info.get("kind"),
        sp.currency,
        sp.total_amount,
        sp.telegram_payment_charge_id,
        sp.provider_payment_charge_id,
        sp.invoice_payload,
    )

    if info["kind"] == "credits_pack":
        seller_id = await ensure_seller(pool, tg_id)

        # Idempotency: Telegram can re-deliver successful_payment update.
        already = await has_seller_credit_tx_by_tg_charge_id(
            pool,
            seller_id=seller_id,
            tg_payment_charge_id=sp.telegram_payment_charge_id,
        )
        if already:
            credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
            await message.answer(f"Платёж уже учтён ✅\nТекущий баланс: {credits}")
            return

        qty = int(info["qty"])
        reason = f"payment_pack_{qty}"
        new_balance = await add_seller_credits(
            pool,
            seller_id=seller_id,
            delta=qty,
            reason=reason,
            invoice_payload=sp.invoice_payload,
            tg_payment_charge_id=sp.telegram_payment_charge_id,
            provider_payment_charge_id=sp.provider_payment_charge_id,
        )

        logger.info(
            "credits_pack credited tg_id=%s seller_id=%s qty=%s new_balance=%s tg_charge=%s",
            tg_id,
            seller_id,
            qty,
            new_balance,
            sp.telegram_payment_charge_id,
        )

        await message.answer(
            f"Оплата получена ✅\nНачислено рассылок: {qty}\nБаланс: {new_balance}"
        )
        return

    # --- campaign payment (legacy, if used) ---
    campaign_id = int(info["campaign_id"])

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
