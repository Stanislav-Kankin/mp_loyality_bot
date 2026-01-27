from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import campaigns_menu, campaigns_list_kb, campaign_actions
from loyalty_bot.db.repo import (
    create_campaign_draft,
    get_campaign_for_seller,
    list_seller_campaigns,
    list_seller_shops,
)

router = Router()


class CampaignCreate(StatesGroup):
    shop_id = State()
    text = State()
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
    await state.set_state(CampaignCreate.button_title)
    await message.answer("Введите название кнопки (например: Открыть ссылку):")


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
        f"Стоимость: {_format_price(settings.price_per_campaign_minor, settings.currency)}

"
        "Оплата будет на следующем этапе.",
        reply_markup=campaign_actions(campaign_id),
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
        items.append((c["id"], f"#{c['id']} {c['status']} ({c['created_at'].date()})"))

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
        f"Кампания #{camp['id']}\n"
        f"Статус: {camp['status']}\n"
        f"Создана: {camp['created_at']}\n\n"
        f"Текст:\n{preview}\n\n"
        f"Кнопка: {camp['button_title']}\n"
        f"URL: {camp['url']}\n"
        f"Цена: {_format_price(camp['price_minor'], camp['currency'])}",
        reply_markup=campaign_actions(campaign_id),
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
    kb.button(text=camp["button_title"] or "Открыть ссылку", callback_data=f"preview:open:{campaign_id}")
    kb.adjust(1)

    await cb.message.answer("Пример сообщения для покупателя:")
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
