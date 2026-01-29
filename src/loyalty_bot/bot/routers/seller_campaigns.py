from __future__ import annotations

import html
from datetime import date, datetime
import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import campaigns_menu, campaigns_list_kb, campaign_actions, campaign_card_actions, cancel_kb, cancel_skip_kb, skip_photo_kb
from loyalty_bot.db.repo import (
    is_seller_allowed,
    get_seller_credits,
    start_campaign_sending,
    mark_campaign_paid_test,
    create_campaign_draft,
    update_campaign_draft,
    get_campaign_for_seller,
    list_seller_campaigns_page,
    list_shop_campaigns_page,
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



def _is_edit_flow(data: dict) -> bool:
    return isinstance(data.get("campaign_id"), int)


def _build_campaign_actions_markup(*, campaign_id: int, credits: int) -> InlineKeyboardMarkup:
    """Step D: simplified campaign card actions."""
    return campaign_card_actions(campaign_id, credits=credits, back_cb="campaigns:list")



def _campaign_card_text(camp: dict, *, credits: int) -> str:
    preview = str(camp.get("text") or "")
    if len(preview) > 350:
        preview = preview[:350] + "…"

    return (
        f"Рассылка №{camp['id']}\n"
        f"<b>Доступно рассылок:</b> {credits}\n"
        f"<b>Магазин:</b> {html.escape(str(camp.get('shop_name','')))}\n"
        f"<b>Создана:</b> {_format_dt(camp.get('created_at'))}\n\n"
        f"<b>Текст:</b>\n{html.escape(preview)}\n\n"
        f"<b>Кнопка:</b> {html.escape(str(camp.get('button_title') or ''))}"
    )


async def _render_campaign_card(*, message: Message, camp: dict, tg_id: int, credits: int) -> None:
    await message.edit_text(
        _campaign_card_text(camp, credits=credits),
        reply_markup=_build_campaign_actions_markup(campaign_id=int(camp['id']), credits=credits),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "campaigncreate:cancel")
async def campaign_create_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    # Return to seller campaigns menu (simple & stable).
    await state.clear()
    await cb.message.edit_text("Рассылки:", reply_markup=campaigns_menu())
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith("campaignedit:cancel:"))
async def campaign_edit_cancel(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await state.clear()
        await cb.answer()
        return
    campaign_id = int(raw_id)

    await state.clear()
    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    if camp is None:
        await cb.answer("Кампания не найдена", show_alert=True)
        return

    # Re-render card in-place
    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    await cb.message.edit_text(
        _campaign_card_text(camp, credits=credits),
        reply_markup=_build_campaign_actions_markup(campaign_id=campaign_id, credits=credits),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith("campaign:edit:"))
async def campaign_edit_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
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

    if str(camp.get("status")) != "draft":
        await cb.answer("Можно редактировать только черновики", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        campaign_id=campaign_id,
        shop_id=int(camp.get("shop_id")),
        cur_text=str(camp.get("text") or ""),
        cur_photo_file_id=camp.get("photo_file_id"),
        cur_button_title=str(camp.get("button_title") or ""),
        cur_url=str(camp.get("url") or ""),
    )
    await state.set_state(CampaignCreate.text)

    await cb.message.answer(
        """✏️ Редактирование рассылки

Введите новый текст рассылки.

⏭ «Пропустить» — оставить текущий текст.""",
        reply_markup=cancel_skip_kb(
            skip_cb="campaignedit:skip:text",
            cancel_cb=f"campaignedit:cancel:{campaign_id}",
        ),
    )
    await cb.answer()


async def _campaign_finish_edit(message: Message, state: FSMContext, pool: asyncpg.Pool, tg_id: int) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not isinstance(campaign_id, int):
        await state.clear()
        await message.answer("Ошибка состояния. Попробуйте ещё раз.")
        return

    text_val = (data.get("text") or data.get("cur_text") or "").strip()
    button_title = (data.get("button_title") or data.get("cur_button_title") or "").strip()
    url_val = (data.get("url") or data.get("cur_url") or "").strip()
    photo_file_id = data.get("photo_file_id")
    if photo_file_id is None:
        photo_file_id = data.get("cur_photo_file_id")

    if not text_val:
        await message.answer("Текст пустой. Введите текст (или сначала задайте его, затем можно пропускать шаги).")
        return
    if not button_title:
        await message.answer("Название кнопки пустое. Введите название (или сначала задайте его, затем можно пропускать шаги).")
        return
    if not _is_valid_url(url_val):
        await message.answer("URL пустой или некорректный. Введите URL (http/https).")
        return

    await update_campaign_draft(
        pool,
        seller_tg_user_id=tg_id,
        campaign_id=campaign_id,
        text=text_val,
        button_title=button_title,
        url=url_val,
        photo_file_id=str(photo_file_id) if photo_file_id else None,
    )

    await state.clear()
    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    if camp is None:
        await message.answer("Черновик рассылки обновлён ✅")
        return
    await message.answer(
        _campaign_card_text(camp, credits=credits),
        reply_markup=_build_campaign_actions_markup(campaign_id=campaign_id, credits=credits),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "campaignedit:skip:text")
async def campaignedit_skip_text(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cur_text = (data.get("cur_text") or "").strip()
    campaign_id = data.get("campaign_id")

    if not isinstance(campaign_id, int):
        await state.clear()
        await cb.answer()
        return
    if not cur_text:
        await cb.message.answer("Текущий текст пустой. Введите текст, чтобы продолжить.")
        await cb.answer()
        return

    await state.update_data(text=cur_text)
    await state.set_state(CampaignCreate.photo)

    await cb.message.answer(
        """Пришлите картинку для рассылки.

⏭ «Пропустить» — оставить текущее фото.""",
        reply_markup=cancel_skip_kb(
            skip_cb="campaignedit:skip:photo",
            cancel_cb=f"campaignedit:cancel:{campaign_id}",
        ),
    )
    await cb.answer()


@router.callback_query(F.data == "campaignedit:skip:photo")
async def campaignedit_skip_photo(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not isinstance(campaign_id, int):
        await state.clear()
        await cb.answer()
        return

    await state.update_data(photo_file_id=data.get("cur_photo_file_id"))
    await state.set_state(CampaignCreate.button_title)

    await cb.message.answer(
        """Введите название кнопки.

⏭ «Пропустить» — оставить текущее значение.""",
        reply_markup=cancel_skip_kb(
            skip_cb="campaignedit:skip:button_title",
            cancel_cb=f"campaignedit:cancel:{campaign_id}",
        ),
    )
    await cb.answer()


@router.callback_query(F.data == "campaignedit:skip:button_title")
async def campaignedit_skip_button_title(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    cur_title = (data.get("cur_button_title") or "").strip()

    if not isinstance(campaign_id, int):
        await state.clear()
        await cb.answer()
        return
    if not cur_title:
        await cb.message.answer("Текущее название кнопки пустое. Введите название, чтобы продолжить.")
        await cb.answer()
        return

    await state.update_data(button_title=cur_title)
    await state.set_state(CampaignCreate.url)

    await cb.message.answer(
        """Введите URL (http/https).

⏭ «Пропустить» — оставить текущий URL.""",
        reply_markup=cancel_skip_kb(
            skip_cb="campaignedit:skip:url",
            cancel_cb=f"campaignedit:cancel:{campaign_id}",
        ),
    )
    await cb.answer()


@router.callback_query(F.data == "campaignedit:skip:url")
async def campaignedit_skip_url(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    cur_url = (data.get("cur_url") or "").strip()

    if not isinstance(campaign_id, int):
        await state.clear()
        await cb.answer()
        return
    if not _is_valid_url(cur_url):
        await cb.message.answer("Текущий URL пустой/некорректный. Введите URL, чтобы продолжить.")
        await cb.answer()
        return

    await state.update_data(url=cur_url)
    await _campaign_finish_edit(cb.message, state, pool, tg_id)
    await cb.answer()

def _shop_campaigns_menu_kb(shop_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Новая рассылка", callback_data=f"shop:campaigns:new:{shop_id}")
    kb.button(text="📋 Мои рассылки", callback_data=f"shop:campaigns:list:{shop_id}")
    kb.button(text="⬅️ Назад к магазину", callback_data=f"shop:open:{shop_id}")
    kb.adjust(1)
    return kb


@router.callback_query(F.data.regexp(r"^shop:campaigns:\d+$"))
async def shop_campaigns_menu(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    # Expected: shop:campaigns:<shop_id>
    if len(parts) != 3:
        await cb.answer("Некорректная команда", show_alert=True)
        return
    raw_id = parts[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    shop = await get_shop_for_seller(pool, seller_tg_user_id=tg_id, shop_id=shop_id)
    if shop is None or not shop.get("is_active", True):
        await cb.answer("Магазин не найден/отключён", show_alert=True)
        return

    await state.clear()
    await cb.message.edit_text(
        f"📣 Рассылки магазина: {html.escape(shop.get('name') or shop.get('shop_name') or '')}",
        reply_markup=_shop_campaigns_menu_kb(shop_id).as_markup(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("shop:campaigns:new:"))
async def shop_campaigns_new(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
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
    await cb.message.answer("Введите текст рассылки:", reply_markup=cancel_kb("campaigncreate:cancel"))
    await cb.answer()


_CAMPAIGNS_PAGE_SIZE = 10


@router.callback_query(F.data.regexp(r"^shop:campaigns:list:\d+(?::\d+)?$"))
async def shop_campaigns_list(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    # shop:campaigns:list:<shop_id>[:<page>]
    if len(parts) not in (4, 5):
        await cb.answer("Некорректная команда", show_alert=True)
        return
    raw_shop_id = parts[3]
    raw_page = parts[4] if len(parts) == 5 else "0"

    if not raw_shop_id.isdigit() or not raw_page.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_shop_id)
    page = int(raw_page)
    if page < 0:
        page = 0

    shop = await get_shop_for_seller(pool, seller_tg_user_id=tg_id, shop_id=shop_id)
    if shop is None or not shop.get("is_active", True):
        await cb.answer("Магазин не найден/отключён", show_alert=True)
        return

    await state.clear()

    offset = page * _CAMPAIGNS_PAGE_SIZE
    items, has_next = await list_shop_campaigns_page(
        pool,
        seller_tg_user_id=tg_id,
        shop_id=shop_id,
        limit=_CAMPAIGNS_PAGE_SIZE,
        offset=offset,
    )
    if not items:
        await cb.message.edit_text(
            "У вас пока нет рассылок для этого магазина.",
            reply_markup=_shop_campaigns_menu_kb(shop_id).as_markup(),
        )
        await cb.answer()
        return

    kb = InlineKeyboardBuilder()
    for c in items:
        shop_name = str(c.get("shop_name") or shop.get("name") or "Магазин")
        if len(shop_name) > 28:
            shop_name = shop_name[:28] + "…"
        dt = c.get("created_at")
        date_s = dt.date().isoformat() if dt else ""
        title = f"{shop_name} — {date_s}".strip()
        kb.button(text=title, callback_data=f"campaign:open:{c['id']}")

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"shop:campaigns:list:{shop_id}:{page - 1}")
    nav.button(text="⬅️ Назад", callback_data=f"shop:campaigns:{shop_id}")
    if has_next:
        nav.button(text="➡️", callback_data=f"shop:campaigns:list:{shop_id}:{page + 1}")
    nav.adjust(3)

    kb.adjust(1)
    kb.attach(nav)
    await cb.message.edit_text(
        f"Ваши рассылки (стр. {page + 1}):",
        reply_markup=kb.as_markup(),
    )
    await cb.answer()



class CampaignCreate(StatesGroup):
    shop_id = State()
    text = State()
    photo = State()
    button_title = State()
    url = State()


async def _is_seller(pool: asyncpg.Pool, tg_id: int) -> bool:
    if tg_id in settings.admin_ids_set:
        return True
    return await is_seller_allowed(pool, tg_id) or (tg_id in settings.seller_ids_set)


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
async def seller_campaigns_home(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("Рассылки:", reply_markup=campaigns_menu())
    await cb.answer()


@router.callback_query(F.data == "campaigns:create")
async def campaigns_create_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
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
async def campaigns_shop_selected(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    await state.set_state(CampaignCreate.text)
    await state.update_data(shop_id=shop_id)

    await cb.message.edit_text("Введите текст рассылки (сообщение, которое увидят покупатели):", reply_markup=cancel_kb("campaigncreate:cancel"))
    await cb.answer()


@router.message(CampaignCreate.text)
async def campaigns_text(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        await message.answer("Нет доступа.")
        return

    data = await state.get_data()
    is_edit = _is_edit_flow(data)
    cancel_cb = f"campaignedit:cancel:{data.get('campaign_id')}" if is_edit else "campaigncreate:cancel"

    text_val = (message.text or "").strip()
    if len(text_val) < 1 or len(text_val) > 3500:
        await message.answer("Текст должен быть от 1 до 3500 символов. Введите ещё раз:", reply_markup=cancel_kb(cancel_cb))
        return

    await state.update_data(text=text_val)
    await state.set_state(CampaignCreate.photo)

    if is_edit:
        await message.answer(
            """Пришлите картинку для рассылки.

⏭ «Пропустить» — оставить текущее фото.""",
            reply_markup=cancel_skip_kb(
                skip_cb="campaignedit:skip:photo",
                cancel_cb=cancel_cb,
            ),
        )
    else:
        await message.answer(
            "Пришлите картинку для рассылки или нажмите «Пропустить».",
            reply_markup=cancel_skip_kb(
                skip_cb="campaignphoto:skip",
                cancel_cb=cancel_cb,
            ),
        )


@router.callback_query(F.data == "campaignphoto:skip")
async def campaigns_create_photo_skip(cb: CallbackQuery, state: FSMContext) -> None:
    # Create-flow only (edit flow has its own skip handlers).
    data = await state.get_data()
    if _is_edit_flow(data):
        await cb.answer()
        return

    await state.update_data(photo_file_id=None)
    await state.set_state(CampaignCreate.button_title)
    await cb.message.answer("Введите название кнопки:", reply_markup=cancel_kb("campaigncreate:cancel"))
    await cb.answer()


@router.message(CampaignCreate.photo)
async def campaigns_create_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    is_edit = _is_edit_flow(data)
    cancel_cb = f"campaignedit:cancel:{data.get('campaign_id')}" if is_edit else "campaigncreate:cancel"

    if not message.photo:
        if is_edit:
            await message.answer(
                "Пришлите картинку (как фото) или нажмите «Пропустить».",
                reply_markup=cancel_skip_kb(skip_cb="campaignedit:skip:photo", cancel_cb=cancel_cb),
            )
        else:
            await message.answer(
                "Пришлите картинку (как фото) или нажмите «Пропустить».",
                reply_markup=cancel_skip_kb(skip_cb="campaignphoto:skip", cancel_cb=cancel_cb),
            )
        return

    photo_file_id = message.photo[-1].file_id
    await state.update_data(photo_file_id=photo_file_id)
    await state.set_state(CampaignCreate.button_title)

    if is_edit:
        await message.answer(
            """Введите название кнопки.

⏭ «Пропустить» — оставить текущее значение.""",
            reply_markup=cancel_skip_kb(skip_cb="campaignedit:skip:button_title", cancel_cb=cancel_cb),
        )
    else:
        await message.answer("Введите название кнопки:", reply_markup=cancel_kb(cancel_cb))


@router.message(CampaignCreate.button_title)
async def campaigns_button_title(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        await message.answer("Нет доступа.")
        return

    data = await state.get_data()
    is_edit = _is_edit_flow(data)
    cancel_cb = f"campaignedit:cancel:{data.get('campaign_id')}" if is_edit else "campaigncreate:cancel"

    title = (message.text or "").strip()
    if len(title) < 1 or len(title) > 64:
        await message.answer("Название кнопки должно быть 1..64 символа. Введите ещё раз:", reply_markup=cancel_kb(cancel_cb))
        return

    await state.update_data(button_title=title)
    await state.set_state(CampaignCreate.url)

    if is_edit:
        await message.answer(
            """Введите URL (http/https).

⏭ «Пропустить» — оставить текущий URL.""",
            reply_markup=cancel_skip_kb(skip_cb="campaignedit:skip:url", cancel_cb=cancel_cb),
        )
    else:
        await message.answer(
            "Введите URL (http/https), который будет отправлен после нажатия кнопки:",
            reply_markup=cancel_kb(cancel_cb),
        )


@router.message(CampaignCreate.url)
async def campaigns_url(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        await message.answer("Нет доступа.")
        return

    data = await state.get_data()
    is_edit = _is_edit_flow(data)
    cancel_cb = f"campaignedit:cancel:{data.get('campaign_id')}" if is_edit else "campaigncreate:cancel"

    url = (message.text or "").strip()
    if not _is_valid_url(url):
        await message.answer("Некорректный URL. Нужен http/https. Введите ещё раз:", reply_markup=cancel_kb(cancel_cb))
        return

    await state.update_data(url=url)

    if is_edit:
        await _campaign_finish_edit(message, state, pool, tg_id)
        return

    shop_id = data.get("shop_id")
    text_val = data.get("text")
    button_title = data.get("button_title")
    photo_file_id = data.get("photo_file_id")

    if not isinstance(shop_id, int) or not isinstance(text_val, str) or not isinstance(button_title, str):
        await state.clear()
        await message.answer("Ошибка состояния. Начните заново через 📣 Рассылки.")
        return

    campaign_id = await create_campaign_draft(
        pool,
        seller_tg_user_id=tg_id,
        shop_id=shop_id,
        text=text_val,
        button_title=button_title,
        url=url,
        photo_file_id=str(photo_file_id) if photo_file_id else None,
        price_minor=settings.price_per_campaign_minor,
        currency=settings.currency,
    )
    await state.clear()

    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    if camp is None:
        await message.answer("Рассылка создана ✅")
        return
    await message.answer(
        _campaign_card_text(camp, credits=credits),
        reply_markup=_build_campaign_actions_markup(campaign_id=campaign_id, credits=credits),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

@router.callback_query(F.data.regexp(r"^campaigns:list(?::\d+)?$"))
async def campaigns_list(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    page = 0
    if len(parts) == 3 and parts[2].isdigit():
        page = int(parts[2])
    if page < 0:
        page = 0

    offset = page * _CAMPAIGNS_PAGE_SIZE
    items, has_next = await list_seller_campaigns_page(
        pool,
        seller_tg_user_id=tg_id,
        limit=_CAMPAIGNS_PAGE_SIZE,
        offset=offset,
    )
    if not items:
        await cb.message.edit_text("У вас пока нет рассылок.", reply_markup=campaigns_menu())
        await cb.answer()
        return

    kb = InlineKeyboardBuilder()
    for c in items:
        shop_name = str(c.get("shop_name", "Магазин"))
        if len(shop_name) > 28:
            shop_name = shop_name[:28] + "…"
        dt = c.get("created_at")
        date_s = dt.date().isoformat() if dt else ""
        title = f"{shop_name} — {date_s}".strip()
        kb.button(text=title, callback_data=f"campaign:open:{c['id']}")

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"campaigns:list:{page - 1}")
    nav.button(text="⬅️ Назад", callback_data="seller:campaigns")
    if has_next:
        nav.button(text="➡️", callback_data=f"campaigns:list:{page + 1}")
    nav.adjust(3)

    kb.adjust(1)
    kb.attach(nav)
    await cb.message.edit_text(
        f"Ваши рассылки (стр. {page + 1}):",
        reply_markup=kb.as_markup(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("campaign:open:"))
async def campaign_open(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
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

    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    await cb.message.edit_text(
        _campaign_card_text(camp, credits=credits),
        reply_markup=_build_campaign_actions_markup(campaign_id=campaign_id, credits=credits),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await cb.answer()



@router.callback_query(F.data.startswith("campaign:preview:"))
async def campaign_preview(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
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
    if not await _is_seller(pool, tg_id):
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
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    campaign_id = int(raw_id)

    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    if credits <= 0:
        await cb.message.edit_text(
            "У вас 0 доступных рассылок. Купите пакет:",
            reply_markup=credits_packages_menu(back_cb=f"campaign:open:{campaign_id}", context=f"c{campaign_id}"),
        )
        await cb.answer()
        return

    try:
        total = await start_campaign_sending(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    except ValueError as e:
        code = str(e)
        if code == "campaign_not_found":
            await cb.answer("Кампания не найдена", show_alert=True)
            return
        if code == "campaign_already_started":
            await cb.answer("Эта рассылка уже запущена", show_alert=True)
            return
        if code == "campaign_invalid_status":
            await cb.answer("Эту рассылку нельзя запустить", show_alert=True)
            return
        if code == "no_credits":
            await cb.message.edit_text(
                "У вас 0 доступных рассылок. Купите пакет:",
                reply_markup=credits_packages_menu(back_cb=f"campaign:open:{campaign_id}", context=f"c{campaign_id}"),
            )
            await cb.answer()
            return
        await cb.answer("Не удалось запустить рассылку", show_alert=True)
        return

    await cb.answer("Запущено ✅")
    # Try to refresh the card to show updated credits.
    camp = await get_campaign_for_seller(pool, seller_tg_user_id=tg_id, campaign_id=campaign_id)
    new_credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    if camp is not None:
        await cb.message.edit_text(
            _campaign_card_text(camp, credits=new_credits),
            reply_markup=_build_campaign_actions_markup(campaign_id=campaign_id, credits=new_credits),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    await cb.message.answer(
        f"Рассылка #{campaign_id} запущена. Получателей: {total}.\n"
        "Воркер отправит сообщения в фоне."
    )
