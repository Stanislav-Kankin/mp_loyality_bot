from __future__ import annotations

import asyncpg
import datetime
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import buyer_gender_menu, buyer_subscription_menu, seller_main_menu
from loyalty_bot.db.repo import (
    ensure_seller,
    get_customer,
    get_customer_subscribed_shops,
    get_seller_credits,
    get_shop_customer_status,
    get_shop_welcome,
    is_seller_allowed,
    set_seller_trial_started,
    shop_exists,
    shop_is_active,
    subscribe_customer_to_shop,
    unsubscribe_customer_from_shop,
    update_customer_profile,
)

router = Router()
logger = logging.getLogger(__name__)


DEMO_LANDING_TEXT = (
    "DEMO для селлера\n\n"
    "— 7 дней доступа к демо-режиму\n"
    "— можно создать 1 магазин и сделать 3 тестовые рассылки\n"
    "— покупки в DEMO запрещены\n"
    "— база покупателей НЕ переносится в персонального бота\n\n"
    "Нажмите кнопку ниже, чтобы запустить демо."
)


DEMO_INFO_TEXT = (
    "INFO\n\n"
    "• DEMO длится 7 дней с момента нажатия «Получить демо».\n"
    "• В DEMO-боте покупки запрещены.\n"
    "• База покупателей из DEMO не переносится в персонального бота.\n\n"
    "Чтобы получить персонального бота: создайте бота в BotFather и свяжитесь с нами вне бота."
)


async def _send_shop_welcome(message: Message, pool: asyncpg.Pool, shop_id: int) -> None:
    welcome = await get_shop_welcome(pool, shop_id=shop_id)
    if not welcome:
        return

    text = (welcome.get("welcome_text") or "").strip()
    photo_file_id = welcome.get("welcome_photo_file_id")
    welcome_button_text = (welcome.get("welcome_button_text") or "").strip()
    welcome_url = (welcome.get("welcome_url") or "").strip() or None

    kb = None
    if welcome_url:
        b = InlineKeyboardBuilder()
        b.button(text=(welcome_button_text or "🔗 Ссылка"), url=welcome_url)
        b.adjust(1)
        kb = b.as_markup()

    if photo_file_id:
        # UX: welcome должно быть одним сообщением. Caption max is 1024.
        caption = text[:1024] if text else None
        await message.answer_photo(photo=photo_file_id, caption=caption, reply_markup=kb)
        return

    if text:
        # Text max is 4096
        await message.answer(text[:4096], reply_markup=kb)


class BuyerOnboarding(StatesGroup):
    full_years = State()
    gender = State()


def _parse_shop_payload(args: str | None) -> int | None:
    if not args:
        return None
    # MVP payload format: "shop_<id>"
    if not args.startswith("shop_"):
        return None
    raw = args.removeprefix("shop_").strip()
    if not raw.isdigit():
        return None
    return int(raw)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None:
        await message.answer("Ошибка: не удалось определить Telegram user id.")
        return

    raw_args = (command.args or "").strip() or None
    shop_id = _parse_shop_payload(raw_args)

    source = "none"
    if shop_id is not None:
        source = "buyer_shop"
    elif raw_args == "landing":
        source = "seller_landing"
    logger.info("start: tg_id=%s payload=%r source=%s", tg_id, raw_args, source)

    # Seller landing flow (from website). Trial does NOT start automatically.
    if raw_args == "landing":
        b = InlineKeyboardBuilder()
        b.button(text="🎁 Получить демо (7 дней)", callback_data="trial:start")
        b.button(text="ℹ️ INFO", callback_data="trial:info")
        b.adjust(1)
        await message.answer(DEMO_LANDING_TEXT, reply_markup=b.as_markup())
        return

    # Buyer flow (opt-in via deep-link)
    if shop_id is not None:
        if not await shop_exists(pool, shop_id):
            await message.answer("Магазин не найден. Проверьте ссылку/QR.")
            return

        if not await shop_is_active(pool, shop_id):
            await message.answer("Магазин сейчас отключён. Обратитесь к продавцу.")
            return

        customer = await get_customer(pool, tg_id)
        customer_id = int(customer["id"])

        # UX: if already subscribed, don't spam resubscribe/welcome.
        status = await get_shop_customer_status(pool, shop_id=shop_id, customer_id=customer_id)
        if status == "subscribed":
            await message.answer(
                "Вы успешно подписаны на выгоду, приятного использования.",
                reply_markup=buyer_subscription_menu(shop_id),
            )
            return

        await subscribe_customer_to_shop(pool, shop_id=shop_id, customer_id=customer_id)

        # lightweight onboarding (only if not filled yet)
        if customer.get("full_years") is None or customer.get("gender") is None:
            await state.clear()
            await state.update_data(shop_id=shop_id, customer_id=customer_id)
            await state.set_state(BuyerOnboarding.full_years)
            await message.answer("1) Сколько вам полных лет?")
            return

        await message.answer(
            "Вы подписаны на уведомления магазина ✅\n\n"
            "Если захотите — можно отписаться кнопкой ниже.",
            reply_markup=buyer_subscription_menu(shop_id),
        )
        await _send_shop_welcome(message, pool, shop_id)
        return

    # Seller flow
    # Admins are always allowed.
    # Sellers are allowed either via DB allowlist (preferred) or via legacy env SELLER_TG_IDS.
    allowed = tg_id in settings.admin_ids_set
    if not allowed:
        allowed = await is_seller_allowed(pool, tg_id) or (tg_id in settings.seller_ids_set)

    if allowed:
        await ensure_seller(pool, tg_id)
        credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
        await message.answer(
            f"Панель селлера:\n"
            f"Доступно рассылок: {credits}",
            reply_markup=seller_main_menu(is_admin=tg_id in settings.admin_ids_set),
        )
        return

    # Buyer repeat /start (no payload): if already subscribed, show quick unsubscribe.
    customer = await get_customer(pool, tg_id)
    customer_id = int(customer["id"])
    subs = await get_customer_subscribed_shops(pool, customer_id=customer_id)
    if subs:
        if len(subs) == 1:
            sid = int(subs[0]["shop_id"])
            await message.answer(
                "Вы успешно подписаны на выгоду, приятного использования.",
                reply_markup=buyer_subscription_menu(sid),
            )
            return

        b = InlineKeyboardBuilder()
        for s in subs:
            sid = int(s["shop_id"])
            name = str(s["name"])
            b.button(text=f"🚫 Отписаться от {name}", callback_data=f"buyer:unsub:{sid}")
        b.adjust(1)

        await message.answer(
            "Вы успешно подписаны на выгоду, приятного использования.\n\n"
            "Выберите магазин, чтобы отписаться:",
            reply_markup=b.as_markup(),
        )
        return

    await message.answer(
        "Это бот лояльности магазина.\n\n"
        "Чтобы подписаться — перейдите по ссылке/QR от продавца.\n"
        "Если вы продавец — попросите администратора добавить ваш TG id в админке бота."
    )


@router.callback_query(F.data == "trial:info")
async def trial_info(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.answer(DEMO_INFO_TEXT)


@router.callback_query(F.data == "trial:start")
async def trial_start(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id if cb.from_user else None
    if tg_id is None:
        await cb.answer("Ошибка: не удалось определить Telegram user id.", show_alert=True)
        return

    await ensure_seller(pool, tg_id)
    info = await set_seller_trial_started(pool, seller_tg_user_id=tg_id)
    started_at = info.get("trial_started_at")

    ends_at: datetime.datetime | None = None
    if started_at is not None:
        ends_at = started_at + datetime.timedelta(days=7)

    await cb.answer("Демо активировано ✅")

    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    text = "Демо активировано на 7 дней."
    if ends_at is not None:
        text += f"\n\nДоступно до: {ends_at:%Y-%m-%d %H:%M}"
    text += f"\nДоступно рассылок: {credits}"

    await cb.message.answer(
        text,
        reply_markup=seller_main_menu(is_admin=tg_id in settings.admin_ids_set),
    )

@router.message(BuyerOnboarding.full_years)
async def buyer_onboarding_full_years(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Введите число (например: 25).")
        return

    years = int(text)
    if years < 1 or years > 120:
        await message.answer("Введите возраст от 1 до 120.")
        return

    data = await state.get_data()
    customer_id = data.get("customer_id")
    shop_id = data.get("shop_id")
    if not isinstance(customer_id, int) or not isinstance(shop_id, int):
        await state.clear()
        await message.answer("Ошибка состояния. Перейдите по ссылке магазина ещё раз.")
        return

    await update_customer_profile(pool, customer_id, full_years=years)

    await state.set_state(BuyerOnboarding.gender)
    await message.answer("2) Укажите ваш пол:", reply_markup=buyer_gender_menu(shop_id))


@router.callback_query(BuyerOnboarding.gender, F.data.startswith("buyer:gender:"))
async def buyer_onboarding_gender(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    code = cb.data.split(":")[-1]
    if code not in {"m", "f", "u"}:
        await cb.answer("Некорректный выбор", show_alert=True)
        return

    data = await state.get_data()
    customer_id = data.get("customer_id")
    shop_id = data.get("shop_id")

    if not isinstance(customer_id, int) or not isinstance(shop_id, int):
        await state.clear()
        await cb.message.answer("Ошибка состояния. Перейдите по ссылке магазина ещё раз.")
        await cb.answer()
        return

    await update_customer_profile(pool, customer_id, gender=code)
    await state.clear()

    await cb.message.answer(
        "Спасибо! Вы подписаны ✅\n\n"
        "Если захотите — можно отписаться кнопкой ниже.",
        reply_markup=buyer_subscription_menu(shop_id),
    )

    await _send_shop_welcome(cb.message, pool, shop_id)

    await cb.answer()


@router.callback_query(F.data == "buyer:subs")
async def buyer_subscriptions_list_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    customer = await get_customer(pool, tg_id)
    customer_id = int(customer["id"])
    subs = await get_customer_subscribed_shops(pool, customer_id=customer_id)
    if not subs:
        await cb.message.answer("У вас нет активных подписок.")
        await cb.answer()
        return

    b = InlineKeyboardBuilder()
    for s in subs:
        sid = int(s["shop_id"])
        name = str(s["name"])
        b.button(text=f"🚫 Отписаться от {name}", callback_data=f"buyer:unsub:{sid}")
    b.adjust(1)

    await cb.message.answer("Ваши подписки:\nВыберите магазин, чтобы отписаться:", reply_markup=b.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("buyer:unsub:"))
async def buyer_unsubscribe_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    customer = await get_customer(pool, tg_id)
    customer_id = int(customer["id"])
    await unsubscribe_customer_from_shop(pool, shop_id=shop_id, customer_id=customer_id)

    await cb.message.edit_text("Вы отписались ✅")
    await cb.answer()
