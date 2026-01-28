from __future__ import annotations

import html
from datetime import date, datetime
import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import campaigns_menu, campaigns_list_kb, campaign_actions, skip_photo_kb
from loyalty_bot.db.repo import (
    start_campaign_sending,
    mark_campaign_paid_test,
    create_campaign_draft,
    get_campaign_for_seller,
    list_seller_campaigns,
    list_seller_shops,
    get_shop_for_seller,
)

def _status_label(status: str) -> str:
    s = (status or "").strip().lower()
    return {
        "draft": "Черновик",
        "awaiting_payment": "Ожидает оплату",
        "unpaid": "Не оплачено",
        "paid": "Оплачено",
        "sending": "Отправляется",
        "completed": "Отправлено",
        "sent": "Отправлено",
        "failed": "Ошибка",
        "canceled": "Отменено",
        "cancelled": "Отменено",
    }.get(s, status)

router = Router()

@router.callback_query(F.data.startswith("shop:campaigns:"))
async def campaign_new_from_shop(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    shop = await get_shop_for_seller(pool, seller_tg_user_id=tg_id, shop_id=shop_id)
    if shop is None or not shop.get("is_active", True):
        await cb.answer("Магазин не найден/отключён", show_alert=True)
        return

    await state.clear()
    await state.update_data(shop_id=shop_id)
    await state.set_state(CampaignCreate.text)
    await cb.message.answer("Введите текст рассылки:")
    await cb.answer()



class CampaignCreate(StatesGroup):
    shop_id = State()
    text = State()
    photo = State()
    button_title = State()
    url = State()


def _is_seller(tg_id: int) -> bool:
    return tg_id in settings.seller_ids_set or tg_id in settings.admin_ids_set


def _is_valid_url(url: str) -> bool:
    u = url.strip()
    return (u.startswith("http://") or u.startswith("https://")) and len(u) <= 2048


def _format_price(price_minor: int, currency: str) -> str:
    # Telegram Payments uses minor units (kopeks for RUB).
    if price_minor < 0:
        price_minor = 0
    major = price_minor / 100
    # Keep as plain number + currency (works for RUB, USD, etc.)
    return f"{major:.2f} {currency}"


def _format_dt(value: object) -> str:
    """Format datetimes from asyncpg records safely.

    asyncpg may return datetime/date objects (with or without tz). We keep formatting
    intentionally simple and stable for MVP UI.
    """

    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    # Fallback (e.g., already a string)
    return str(value)


def _format_dt(val: object) -> str:
    """Format DB datetime/date values safely for UI."""
    if val is None:
        return "—"
    if isinstance(val, datetime):
        # Display without seconds to keep UI compact.
        return val.strftime("%Y-%m-%d %H:%M")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    # Fallback for strings or unknown types.
    return str(val)


@router.callback_query(F.data == "seller:campaigns")
async def seller_campaigns_home(cb: CallbackQuery) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("Рассылки:", reply_markup=campaigns_menu())
    await cb.answer()


@router.callback_query(F.data == "campaigns:create")
async def campaigns_create_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    shops = await list_seller_shops(pool, seller_tg_user_id=tg_id)
    active = [s for s in shops if s["is_active"]]

    if not active:
        await cb.answer()
        await cb.message.edit_text("У вас нет активных магазинов. Сначала создайте магазин.", reply_markup=campaigns_menu())
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    for sh in active[:10]:
        kb.button(text=f"🏪 {sh['name']}", callback_data=f"campaigns:shop:{sh['id']}")
    kb.button(text="⬅️ Назад", callback_data="seller:campaigns")
    kb.adjust(1)

    await state.clear()
    await cb.message.edit_text("Выберите магазин для рассылки:", reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("campaigns:shop:"))
async def campaigns_shop_selected(cb: CallbackQuery, state: FSMContext) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    await state.set_state(CampaignCreate.text)
    await state.update_data(shop_id=shop_id)

    await cb.message.edit_text("Введите текст рассылки (сообщение, которое увидят покупатели):")
    await cb.answer()


@router.message(CampaignCreate.text)
async def campaigns_text(message: Message, state: FSMContext) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not _is_seller(tg_id):
        await message.answer("Нет доступа.")
        return

    text = (message.text or "").strip()
    if len(text) < 1 or len(text) > 3500:
        await message.answer("Текст должен быть от 1 до 3500 символов. Введите ещё раз:")
        return

    await state.update_data(text=text)
    await state.set_state(CampaignCreate.photo)
    await message.answer(
        "Пришлите картинку для рассылки или нажмите «Пропустить».",
        reply_markup=skip_photo_kb("campaignphoto"),
    )


@router.callback_query(F.data == "campaignphoto:skip")
async def campaigns_create_photo_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(photo_file_id=None)
    await state.set_state(CampaignCreate.button_title)
    await cb.message.answer("Введите название кнопки:")
    await cb.answer()


@router.message(CampaignCreate.photo)
async def campaigns_create_photo(message: Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Пришлите картинку (как фото) или нажмите «Пропустить».")
        return
    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=photo_file_id)
    await state.set_state(CampaignCreate.button_title)
    await message.answer("Введите название кнопки:")


@router.message(CampaignCreate.button_title)
async def campaigns_button_title(message: Message, state: FSMContext) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not _is_seller(tg_id):
        await message.answer("Нет доступа.")
        return

    title = (message.text or "").strip()
    if len(title) < 1 or len(title) > 64:
        await message.answer("Название кнопки должно быть 1..64 символа. Введите ещё раз:")
        return

    await state.update_data(button_title=title)
    await state.set_state(CampaignCreate.url)
    await message.answer("Введите URL (http/https), который будет отправлен после нажатия кнопки:")


@router.message(CampaignCreate.url)
async def campaigns_url(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not _is_seller(tg_id):
        await message.answer("Нет доступа.")
        return

    url = (message.text or "").strip()
    if not _is_valid_url(url):
        await message.answer("Некорректный URL. Нужен http/https. Введите ещё раз:")
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    text = data.get("text")
    button_title = data.get("button_title")
    photo_file_id = data.get("photo_file_id")

    if not isinstance(shop_id, int) or not isinstance(text, str) or not isinstance(button_title, str):
        await state.clear()
        await message.answer("Ошибка состояния. Начните заново через 📣 Рассылки.")
        return

    campaign_id = await create_campaign_draft(
        pool,
        seller_tg_user_id=tg_id,
        shop_id=shop_id,
        text=text,
        button_title=button_title,
        url=url,
        photo_file_id=str(photo_file_id) if photo_file_id else None,
        price_minor=settings.price_per_campaign_minor,
        currency=settings.currency,
    )
    await state.clear()

    await message.answer(
        "Черновик рассылки создан ✅\n\n"
        f"ID кампании: {campaign_id}\n"
        f"Текст: {text[:200]}{'…' if len(text) > 200 else ''}\n"
        f"Кнопка: {button_title}\n"
        f"URL: {url}\n\n"
        f"Стоимость: {_format_price(settings.price_per_campaign_minor, settings.currency)}\n"
        "Оплата будет на следующем этапе.",
        reply_markup=campaign_actions(
            campaign_id,
            show_test=(settings.payments_test_mode and tg_id in settings.admin_ids_set),
            show_send=False,
        ),
    )


@router.callback_query(F.data == "campaigns:list")
async def campaigns_list(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    campaigns = await list_seller_campaigns(pool, seller_tg_user_id=tg_id, limit=10)
    if not campaigns:
        await cb.message.edit_text("У вас пока нет рассылок.", reply_markup=campaigns_menu())
        await cb.answer()
        return

    items = []
    for c in campaigns:
        shop_name = str(c.get("shop_name", ""))
        if len(shop_name) > 18:
            shop_name = shop_name[:18] + "…"
        status_h = _status_label(str(c.get("status", "")))
        dt = c.get("created_at")
        date_s = dt.date().isoformat() if dt else ""
        items.append((c["id"], f"#{c['id']} {status_h} · {shop_name} ({date_s})"))

    await cb.message.edit_text("Ваши рассылки (последние 10):", reply_markup=campaigns_list_kb(items))
    await cb.answer()


@router.callback_query(F.data.startswith("campaign:open:"))
async def campaign_open(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    campaign_id = int(raw_id)

    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await cb.answer("Кампания не найдена", show_alert=True)
        return

    preview = camp["text"]
    if len(preview) > 350:
        preview = preview[:350] + "…"

    await cb.message.edit_text(
    f"Рассылка №{camp['id']}\n"
    f"<b>Статус:</b> {_status_label(camp['status'])}\n"
    f"<b>Магазин:</b> {html.escape(camp.get('shop_name',''))}\n"
    f"<b>Создана:</b> {_format_dt(camp['created_at'])}\n\n"
    f"<b>Текст:</b>\n{html.escape(preview)}\n\n"
    f"<b>Кнопка:</b> {html.escape(camp['button_title'])}\n"
    f"<b>URL:</b> {html.escape(camp['url'])}\n"
    f"<b>Цена:</b> {_format_price(camp['price_minor'], camp['currency'])}",
    reply_markup=campaign_actions(
        campaign_id,
        show_test=(settings.payments_test_mode and tg_id in settings.admin_ids_set),
        show_send=(str(camp.get('status')) == 'paid'),
    ),
    parse_mode="HTML",
    disable_web_page_preview=True,
)
    await cb.answer()



@router.callback_query(F.data.startswith("campaign:preview:"))
async def campaign_preview(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    campaign_id = int(raw_id)

    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await cb.answer("Кампания не найдена", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text=camp["button_title"] or "Открыть ссылку", url=camp["url"] or "")
    kb.adjust(1)

    await cb.message.answer("Пример сообщения для покупателя:")
    if camp.get("photo_file_id"):
        text = str(camp.get("text") or "")
        await cb.message.answer_photo(
            photo=camp["photo_file_id"],
            caption=text[:1024] if text else None,
            reply_markup=kb.as_markup(),
        )
        if len(text) > 1024:
            await cb.message.answer(text[1024:])
    else:
        await cb.message.answer(camp["text"], reply_markup=kb.as_markup())

    await cb.answer()


@router.callback_query(F.data.startswith("preview:open:"))
async def preview_open(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    campaign_id = int(raw_id)

    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await cb.answer("Кампания не найдена", show_alert=True)
        return

    await cb.answer("Ок ✅")
    await cb.message.answer(f"Ссылка: {camp['url']}")
@router.callback_query(F.data.startswith("campaign:pay:stub:"))
async def campaign_pay_stub(cb: CallbackQuery) -> None:
    await cb.answer("Оплата будет на следующем этапе (Этап 3).", show_alert=True)


@router.callback_query(F.data.startswith("campaign:pay:test:"))
async def campaign_pay_test(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if tg_id not in settings.admin_ids_set:
        await cb.answer("Нет доступа", show_alert=True)
        return
    if not settings.payments_test_mode:
        await cb.answer("TEST режим выключен", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    campaign_id = int(raw_id)

    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await cb.answer("Кампания не найдена", show_alert=True)
        return

    await mark_campaign_paid_test(pool, campaign_id=campaign_id)
    await cb.message.answer(f"TEST оплата ✅\nКампания #{campaign_id} помечена как оплаченная.")
    await cb.answer()


@router.callback_query(F.data.startswith("campaign:send:"))
async def campaign_send(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    campaign_id = int(raw_id)

    try:
        total = await start_campaign_sending(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    except ValueError as e:
        code = str(e)
        if code == "campaign_not_found":
            await cb.answer("Кампания не найдена", show_alert=True)
            return
        if code == "campaign_not_paid":
            await cb.answer("Кампания не оплачена", show_alert=True)
            return
        if code == "no_credits":
            await cb.answer("Недостаточно рассылок на балансе", show_alert=True)
            return
        await cb.answer("Не удалось запустить рассылку", show_alert=True)
        return

    await cb.answer("Запущено ✅")
    await cb.message.answer(
        f"Рассылка #{campaign_id} запущена. Получателей: {total}.\n"
        "Воркер отправит сообщения в фоне."
    )
