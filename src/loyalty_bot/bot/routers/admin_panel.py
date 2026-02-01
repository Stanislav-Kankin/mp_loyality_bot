from __future__ import annotations

import asyncpg
import asyncio
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import admin_main_menu, cancel_kb
from loyalty_bot.db.repo import (
    ensure_seller,
    get_admin_overview,
    get_admin_seller_details,
    list_admin_sellers_page,
    set_seller_access_active,
    upsert_seller_access,
)

router = Router()


class AdminAddSeller(StatesGroup):
    tg_user_id = State()


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids_set

async def _format_user_label(bot: Bot, tg_user_id: int) -> str:
    """Return 'First Last (@username)' where possible. Falls back to tg id."""
    try:
        chat = await bot.get_chat(tg_user_id)
    except Exception:
        return str(tg_user_id)

    first = getattr(chat, "first_name", "") or ""
    last = getattr(chat, "last_name", "") or ""
    name = (first + " " + last).strip()
    username = getattr(chat, "username", None)
    if username:
        if name:
            return f"{name} (@{username})"
        return f"@{username}"
    return name or str(tg_user_id)


def _admin_sellers_list_kb(*, page: int, items: list[dict], has_next: bool) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for it in items:
        tg_user_id = int(it["tg_user_id"])
        active = bool(it["is_active"])
        credits = int(it["credits"])
        shops_count = int(it["shops_count"])
        campaigns_count = int(it["campaigns_count"])
        prefix = "✅" if active else "⛔️"
        kb.button(
            text=f"{prefix} {it['label']} · кредиты {credits} · 🏪{shops_count} · 📣{campaigns_count}",
            callback_data=f"admin:seller:open:{tg_user_id}:{page}",
        )

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="⬅️", callback_data=f"admin:sellers:page:{page-1}")
    if has_next:
        nav.button(text="➡️", callback_data=f"admin:sellers:page:{page+1}")

    if nav.buttons:
        for b in nav.buttons:
            kb.add(b)

    kb.button(text="➕ Добавить селлера", callback_data="admin:seller:add")
    kb.button(text="⬅️ Назад", callback_data="admin:home")
    kb.adjust(1)
    return kb


def _admin_seller_details_kb(*, tg_user_id: int, is_active: bool, back_page: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    toggle_to = "0" if is_active else "1"
    toggle_text = "⛔️ Отключить" if is_active else "✅ Включить"
    kb.button(text=toggle_text, callback_data=f"admin:seller:toggle:{tg_user_id}:{toggle_to}:{back_page}")
    kb.button(text="⬅️ К списку", callback_data=f"admin:sellers:page:{back_page}")
    kb.button(text="🏠 Админка", callback_data="admin:home")
    kb.adjust(1)
    return kb


@router.callback_query(F.data == "admin:home")
async def admin_home_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    stats = await get_admin_overview(pool)
    text = (
        "🛠 Админка\n\n"
        f"Селлеров (в базе): {stats['sellers_total']}\n"
        f"Разрешённых селлеров: {stats['sellers_allowed']}\n"
        f"Активных магазинов: {stats['shops_active']}\n"
        f"Рассылок всего: {stats['campaigns_total']}\n"
        f"Рассылок за 7 дней: {stats['campaigns_7d']}\n"
        f"Суммарный баланс кредитов: {stats['credits_total']}\n"
    )

    if cb.message:
        await cb.message.edit_text(text, reply_markup=admin_main_menu())
    await cb.answer()


@router.callback_query(F.data.startswith("admin:sellers:page:"))
async def admin_sellers_list(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_page = cb.data.split(":")[-1]
    if not raw_page.lstrip("-").isdigit():
        await cb.answer("Некорректная страница", show_alert=True)
        return
    page = max(0, int(raw_page))

    items, has_next = await list_admin_sellers_page(pool, offset=page * 10, limit=10)

    # Enrich display labels with Telegram name/username (best-effort).
    labels: dict[int, str] = {}
    unique_ids = [int(it["tg_user_id"]) for it in items]
    coros = [ _format_user_label(cb.bot, tg_id) for tg_id in unique_ids ]
    results = await asyncio.gather(*coros, return_exceptions=True)
    for tg_id, res in zip(unique_ids, results):
        if isinstance(res, Exception):
            labels[tg_id] = str(tg_id)
        else:
            labels[tg_id] = res
    for it in items:
        it["label"] = labels.get(int(it["tg_user_id"]), str(it["tg_user_id"]))

    text = f"👥 Селлеры (страница {page+1})\n\n" + (
        "Нет добавленных селлеров." if not items else "Выберите селлера:"
    )

    kb = _admin_sellers_list_kb(page=page, items=items, has_next=has_next).as_markup()

    if cb.message:
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:seller:open:"))
async def admin_seller_open(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    if len(parts) < 5:
        await cb.answer("Некорректные данные", show_alert=True)
        return

    raw_tg = parts[3]
    raw_page = parts[4]
    if not raw_tg.isdigit() or not raw_page.lstrip("-").isdigit():
        await cb.answer("Некорректные данные", show_alert=True)
        return

    tg_user_id = int(raw_tg)
    back_page = max(0, int(raw_page))

    d = await get_admin_seller_details(pool, tg_user_id=tg_user_id)

    label = await _format_user_label(cb.bot, tg_user_id)
    if not d:
        await cb.answer("Селлер не найден", show_alert=True)
        return

    active = bool(d["is_active"])
    last_campaign = d["last_campaign_at"]
    last_campaign_str = last_campaign.strftime("%Y-%m-%d %H:%M") if last_campaign else "—"

    text = (
        f"👤 {label}\n"
        f"ID: {tg_user_id}\n"
        f"Статус: {'активен' if active else 'выключен'}\n"
        f"Кредиты: {d['credits']}\n"
        f"Магазинов: {d['shops_count']}\n"
        f"Рассылок: {d['campaigns_count']}\n"
        f"Списано кредитов (всего): {d['spent_total']}\n"
        f"Последняя рассылка: {last_campaign_str}\n"
    )
    if d.get("note"):
        text += f"Заметка: {d['note']}\n"

    kb = _admin_seller_details_kb(tg_user_id=tg_user_id, is_active=active, back_page=back_page).as_markup()

    if cb.message:
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:seller:toggle:"))
async def admin_seller_toggle(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    # admin:seller:toggle:<tg_user_id>:<to_0_or_1>:<back_page>
    if len(parts) < 6:
        await cb.answer("Некорректные данные", show_alert=True)
        return

    raw_tg = parts[3]
    raw_to = parts[4]
    raw_page = parts[5]

    if not raw_tg.isdigit() or raw_to not in {"0", "1"} or not raw_page.lstrip("-").isdigit():
        await cb.answer("Некорректные данные", show_alert=True)
        return

    tg_user_id = int(raw_tg)
    to_active = raw_to == "1"
    back_page = max(0, int(raw_page))

    await set_seller_access_active(pool, tg_user_id=tg_user_id, is_active=to_active)

    # Re-open details
    d = await get_admin_seller_details(pool, tg_user_id=tg_user_id)

    label = await _format_user_label(cb.bot, tg_user_id)
    if not d:
        await cb.answer("Селлер не найден", show_alert=True)
        return

    active = bool(d["is_active"])
    last_campaign = d["last_campaign_at"]
    last_campaign_str = last_campaign.strftime("%Y-%m-%d %H:%M") if last_campaign else "—"

    text = (
        f"👤 {label}\n"
        f"ID: {tg_user_id}\n"
        f"Статус: {'активен' if active else 'выключен'}\n"
        f"Кредиты: {d['credits']}\n"
        f"Магазинов: {d['shops_count']}\n"
        f"Рассылок: {d['campaigns_count']}\n"
        f"Списано кредитов (всего): {d['spent_total']}\n"
        f"Последняя рассылка: {last_campaign_str}\n"
    )
    if d.get("note"):
        text += f"Заметка: {d['note']}\n"

    kb = _admin_seller_details_kb(tg_user_id=tg_user_id, is_active=active, back_page=back_page).as_markup()
    if cb.message:
        await cb.message.edit_text(text, reply_markup=kb)

    await cb.answer("Обновлено ✅", show_alert=True)


@router.callback_query(F.data == "admin:seller:add")
async def admin_seller_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminAddSeller.tg_user_id)

    await cb.message.answer(
        "Введите Telegram ID селлера (число).\n\n"
        "Подсказка: селлер может прислать вам свой ID через @userinfobot.",
        reply_markup=cancel_kb("admin:home"),
    )
    await cb.answer()


@router.message(AdminAddSeller.tg_user_id)
async def admin_seller_add_finish(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите числовой Telegram ID.", reply_markup=cancel_kb("admin:home"))
        return

    tg_user_id = int(raw)
    if tg_user_id <= 0:
        await message.answer("Некорректный Telegram ID.", reply_markup=cancel_kb("admin:home"))
        return

    # Add to DB allowlist and create seller row (credits will be provisioned lazily if first time)
    await upsert_seller_access(
        pool,
        tg_user_id=tg_user_id,
        is_active=True,
        note=None,
        added_by_tg_user_id=message.from_user.id,
    )
    await ensure_seller(pool, tg_user_id)

    await state.clear()

    d = await get_admin_seller_details(pool, tg_user_id=tg_user_id)

    label = await _format_user_label(cb.bot, tg_user_id)
    if not d:
        await message.answer("Селлер добавлен, но детали не найдены (проверьте БД).")
        return

    active = bool(d["is_active"])
    last_campaign = d["last_campaign_at"]
    last_campaign_str = last_campaign.strftime("%Y-%m-%d %H:%M") if last_campaign else "—"

    text = (
        "✅ Селлер добавлен\n\n"
        f"👤 {label}\n"
        f"ID: {tg_user_id}\n"
        f"Статус: {'активен' if active else 'выключен'}\n"
        f"Кредиты: {d['credits']}\n"
        f"Магазинов: {d['shops_count']}\n"
        f"Рассылок: {d['campaigns_count']}\n"
        f"Списано кредитов (всего): {d['spent_total']}\n"
        f"Последняя рассылка: {last_campaign_str}\n"
    )

    kb = _admin_seller_details_kb(tg_user_id=tg_user_id, is_active=active, back_page=0).as_markup()
    await message.answer(text, reply_markup=kb)