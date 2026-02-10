from __future__ import annotations

import datetime
import logging
from uuid import UUID

import asyncpg
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from payment_hub_bot.config import hub_settings
from payment_hub_bot.db.repo import get_payment_order_for_buyer, mark_order_paid


router = Router()
logger = logging.getLogger(__name__)


def _parse_order_id(args: str) -> str | None:
    raw = (args or "").strip()
    if not raw:
        return None
    if not raw.startswith("pay_"):
        return None
    candidate = raw.removeprefix("pay_").strip()
    try:
        UUID(candidate)
    except Exception:
        return None
    return candidate


def _is_expired(created_at: datetime.datetime) -> bool:
    ttl = int(getattr(hub_settings, "order_ttl_seconds", 86_400))
    if ttl <= 0:
        return False
    return (datetime.datetime.now(datetime.UTC) - created_at) > datetime.timedelta(seconds=ttl)


@router.message(CommandStart())
async def start(message: Message, command: CommandObject | None, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None:
        return

    args = (command.args if command else "") or ""
    order_id = _parse_order_id(args)
    if order_id is None:
        await message.answer(
            "Это платёжный бот.\n\n"
            "Перейдите сюда по кнопке оплаты из вашего клиентского бота."
        )
        return

    order = await get_payment_order_for_buyer(pool, order_id=order_id, buyer_tg_id=tg_id)
    if order is None:
        await message.answer("Заказ не найден или не принадлежит вам.")
        return

    status = (order.get("status") or "").strip()
    created_at = order.get("created_at")
    if isinstance(created_at, datetime.datetime) and _is_expired(created_at):
        await message.answer("Ссылка на оплату истекла. Вернитесь в клиентский бот и создайте новый заказ.")
        return

    if status in {"paid", "fulfilled"}:
        await message.answer(
            "Оплата уже получена ✅\n\n"
            "Вернитесь в клиентский бот и нажмите «✅ Проверить оплату / начислить»."
        )
        return

    if status != "pending":
        await message.answer("Этот заказ сейчас нельзя оплатить. Статус: %s" % status)
        return

    pack_code = (order.get("pack_code") or "").strip()
    amount_minor = int(order.get("amount_minor") or 0)
    currency = (order.get("currency") or hub_settings.currency).strip()

    expected = hub_settings.pack_amount_minor(pack_code)
    if expected is None:
        await message.answer("Ошибка: неизвестный пакет для оплаты.")
        logger.warning("start: unknown pack_code order_id=%s pack_code=%s", order_id, pack_code)
        return

    if amount_minor != expected or currency != hub_settings.currency:
        await message.answer("Ошибка: сумма/валюта заказа не совпадает с прайсом.")
        logger.warning(
            "start: price mismatch order_id=%s pack_code=%s amount_minor=%s expected=%s currency=%s",
            order_id,
            pack_code,
            amount_minor,
            expected,
            currency,
        )
        return

    payload = (order.get("invoice_payload") or "").strip() or f"order:{order_id}"

    title = "Пакет рассылок"
    description = f"Оплата {pack_code.replace('_', ' ')}"
    prices = [LabeledPrice(label=title, amount=amount_minor)]

    logger.info("send_invoice order_id=%s tg_id=%s pack=%s amount_minor=%s", order_id, tg_id, pack_code, amount_minor)

    await message.bot.send_invoice(
        chat_id=tg_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=hub_settings.payment_provider_token,
        currency=hub_settings.currency,
        prices=prices,
    )


@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery, pool: asyncpg.Pool) -> None:
    tg_id = pre.from_user.id if pre.from_user else None
    payload = (pre.invoice_payload or "").strip()

    if tg_id is None or not payload.startswith("order:"):
        await pre.answer(ok=False, error_message="Неверные данные оплаты.")
        return

    order_id = payload.removeprefix("order:").strip()
    try:
        UUID(order_id)
    except Exception:
        await pre.answer(ok=False, error_message="Неверные данные оплаты.")
        return

    order = await get_payment_order_for_buyer(pool, order_id=order_id, buyer_tg_id=tg_id)
    if order is None:
        await pre.answer(ok=False, error_message="Заказ не найден.")
        return

    status = (order.get("status") or "").strip()
    if status != "pending":
        await pre.answer(ok=False, error_message="Заказ уже обработан.")
        return

    pack_code = (order.get("pack_code") or "").strip()
    expected_amount = hub_settings.pack_amount_minor(pack_code) or 0

    if pre.currency != hub_settings.currency or int(pre.total_amount) != int(expected_amount):
        await pre.answer(ok=False, error_message="Сумма/валюта не совпадает с заказом.")
        return

    await pre.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, pool: asyncpg.Pool) -> None:
    sp = message.successful_payment
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None:
        return

    payload = (sp.invoice_payload or "").strip()
    if not payload.startswith("order:"):
        logger.warning("successful_payment invalid payload tg_id=%s payload=%s", tg_id, payload)
        return

    order_id = payload.removeprefix("order:").strip()
    try:
        UUID(order_id)
    except Exception:
        logger.warning("successful_payment invalid order_id tg_id=%s payload=%s", tg_id, payload)
        return

    provider_charge = (sp.provider_payment_charge_id or "").strip()
    if not provider_charge:
        logger.warning("successful_payment missing provider_charge tg_id=%s order_id=%s", tg_id, order_id)
        return

    changed = await mark_order_paid(pool, order_id=order_id, provider_payment_charge_id=provider_charge)

    logger.info(
        "successful_payment order_id=%s tg_id=%s changed=%s total=%s currency=%s provider_charge=%s",
        order_id,
        tg_id,
        changed,
        sp.total_amount,
        sp.currency,
        provider_charge,
    )

    await message.answer(
        "Оплата прошла ✅\n\n"
        "Вернитесь в клиентский бот и нажмите «✅ Проверить оплату / начислить»."
    )
