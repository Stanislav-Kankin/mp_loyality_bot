from __future__ import annotations

import asyncpg
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import admin_main_menu, cancel_kb
from loyalty_bot.db.repo import (
    add_seller_credits,
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


class AdminGrantCredits(StatesGroup):
    amount = State()


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids_set


async def _safe_edit(cb: CallbackQuery, text: str, reply_markup) -> None:
    """Edit message text safely.

    Telegram returns 'message is not modified' if text/markup are unchanged.
    We silently ignore that case to avoid crashing on repeated button clicks.
    """
    if not cb.message:
        return
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


def _format_user_label(*, tg_user_id: int, first_name: str | None, last_name: str | None, username: str | None) -> str:
    name_parts: list[str] = []
    if first_name:
        name_parts.append(first_name)
    if last_name:
        name_parts.append(last_name)

    name = " ".join(name_parts).strip() or str(tg_user_id)
    if username:
        name = f"{name} (@{username})"
    return name


def _admin_sellers_list_kb(*, page: int, items: list[dict], has_next: bool) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for it in items:
        tg_user_id = int(it["tg_user_id"])
        active = bool(it["is_active"])
        credits = int(it["credits"])
        shops_count = int(it["shops_count"])
        campaigns_count = int(it["campaigns_count"])
        label = str(it.get("label") or tg_user_id)

        prefix = "✅" if active else "⛔️"
        kb.button(
            text=f"{prefix} {label} · кредиты {credits} · 🏪{shops_count} · 📣{campaigns_count}",
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

    # Credits grants
    kb.button(text="🎁 +1", callback_data=f"admin:seller:grant:{tg_user_id}:1:{back_page}")
    kb.button(text="🎁 +3", callback_data=f"admin:seller:grant:{tg_user_id}:3:{back_page}")
    kb.button(text="🎁 +10", callback_data=f"admin:seller:grant:{tg_user_id}:10:{back_page}")
    kb.button(text="✍️ +X", callback_data=f"admin:seller:grant_custom:{tg_user_id}:{back_page}")

    toggle_to = "0" if is_active else "1"
    toggle_text = "⛔️ Отключить" if is_active else "✅ Включить"
    kb.button(text=toggle_text, callback_data=f"admin:seller:toggle:{tg_user_id}:{toggle_to}:{back_page}")
    kb.button(text="⬅️ К списку", callback_data=f"admin:sellers:page:{back_page}")
    kb.button(text="🏠 Админка", callback_data="admin:home")
    kb.adjust(2, 2, 1, 1, 1)
    return kb


async def _build_admin_seller_details_view(
    *,
    pool: asyncpg.Pool,
    bot: Bot,
    tg_user_id: int,
    back_page: int,
) -> tuple[str, object]:
    d = await get_admin_seller_details(pool, tg_user_id=tg_user_id)
    if not d:
        raise ValueError("seller_not_found")

    try:
        chat = await bot.get_chat(tg_user_id)
        label = _format_user_label(
            tg_user_id=tg_user_id,
            first_name=getattr(chat, "first_name", None),
            last_name=getattr(chat, "last_name", None),
            username=getattr(chat, "username", None),
        )
    except Exception:
        label = str(tg_user_id)

    active = bool(d["is_active"])
    last_campaign = d["last_campaign_at"]
    last_campaign_str = last_campaign.strftime("%Y-%m-%d %H:%M") if last_campaign else "—"

    text = (
        f"👤 Селлер: {label}\n"
        f"Telegram ID: {tg_user_id}\n"
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
    return text, kb


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

    await _safe_edit(cb, text, reply_markup=admin_main_menu())
    # Явный фидбек: кнопка работает даже если текст не менялся.
    await cb.answer("Данные успешно обновлены")


@router.callback_query(F.data.startswith("admin:sellers:page:"))
async def admin_sellers_list(cb: CallbackQuery, pool: asyncpg.Pool, bot: Bot) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_page = cb.data.split(":")[-1]
    if not raw_page.lstrip("-").isdigit():
        await cb.answer("Некорректная страница", show_alert=True)
        return
    page = max(0, int(raw_page))

    items, has_next = await list_admin_sellers_page(pool, offset=page * 10, limit=10)

    enriched: list[dict] = []
    for it in items:
        tg_user_id = int(it["tg_user_id"])
        try:
            chat = await bot.get_chat(tg_user_id)
            label = _format_user_label(
                tg_user_id=tg_user_id,
                first_name=getattr(chat, "first_name", None),
                last_name=getattr(chat, "last_name", None),
                username=getattr(chat, "username", None),
            )
        except Exception:
            label = str(tg_user_id)

        it2 = dict(it)
        it2["label"] = label
        enriched.append(it2)

    kb = _admin_sellers_list_kb(page=page, items=enriched, has_next=has_next).as_markup()
    if cb.message:
        await _safe_edit(cb, "👥 Селлеры (страница %d)\n\nВыберите селлера:" % (page + 1), reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:seller:open:"))
async def admin_seller_open(cb: CallbackQuery, pool: asyncpg.Pool, bot: Bot) -> None:
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

    try:
        text, kb = await _build_admin_seller_details_view(
            pool=pool,
            bot=bot,
            tg_user_id=tg_user_id,
            back_page=back_page,
        )
    except ValueError:
        await cb.answer("Селлер не найден", show_alert=True)
        return

    await _safe_edit(cb, text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("admin:seller:grant:"))
async def admin_seller_grant(cb: CallbackQuery, pool: asyncpg.Pool, bot: Bot) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    # admin:seller:grant:<tg_user_id>:<delta>:<back_page>
    if len(parts) < 6:
        await cb.answer("Некорректные данные", show_alert=True)
        return

    raw_tg = parts[3]
    raw_delta = parts[4]
    raw_page = parts[5]

    if not raw_tg.isdigit() or not raw_delta.lstrip("-").isdigit() or not raw_page.lstrip("-").isdigit():
        await cb.answer("Некорректные данные", show_alert=True)
        return

    tg_user_id = int(raw_tg)
    delta = int(raw_delta)
    back_page = max(0, int(raw_page))

    if delta <= 0:
        await cb.answer("Можно начислять только положительное число", show_alert=True)
        return

    d = await get_admin_seller_details(pool, tg_user_id=tg_user_id)
    if not d or not d.get("seller_id"):
        await cb.answer("Селлер не найден", show_alert=True)
        return

    new_balance = await add_seller_credits(pool, seller_id=int(d["seller_id"]), delta=delta, reason="admin_grant")

    try:
        text, kb = await _build_admin_seller_details_view(pool=pool, bot=bot, tg_user_id=tg_user_id, back_page=back_page)
    except ValueError:
        await cb.answer("Селлер не найден", show_alert=True)
        return

    await _safe_edit(cb, text, reply_markup=kb)
    await cb.answer(f"Начислено +{delta}. Баланс: {new_balance}")


@router.callback_query(F.data.startswith("admin:seller:grant_custom:"))
async def admin_seller_grant_custom_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = cb.data.split(":")
    # admin:seller:grant_custom:<tg_user_id>:<back_page>
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

    await state.clear()
    await state.set_state(AdminGrantCredits.amount)
    await state.update_data(
        tg_user_id=tg_user_id,
        back_page=back_page,
        origin_chat_id=cb.message.chat.id if cb.message else None,
        origin_message_id=cb.message.message_id if cb.message else None,
    )

    cancel_cb = f"admin:seller:open:{tg_user_id}:{back_page}"
    if cb.message:
        await cb.message.answer(
            "Введите количество кредитов для начисления (целое число > 0):",
            reply_markup=cancel_kb(cancel_cb),
        )
    await cb.answer()


@router.message(AdminGrantCredits.amount)
async def admin_seller_grant_custom_finish(message: Message, state: FSMContext, pool: asyncpg.Pool, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("Введите целое число (например: 5).")
        return

    delta = int(raw)
    if delta <= 0:
        await message.answer("Введите число больше 0 (например: 5).")
        return

    if delta > 100000:
        await message.answer("Слишком большое число. Введите значение до 100000.")
        return

    data = await state.get_data()
    tg_user_id = data.get("tg_user_id")
    back_page = data.get("back_page")
    origin_chat_id = data.get("origin_chat_id")
    origin_message_id = data.get("origin_message_id")

    if not isinstance(tg_user_id, int) or not isinstance(back_page, int):
        await state.clear()
        await message.answer("Ошибка состояния. Откройте карточку селлера ещё раз.")
        return

    d = await get_admin_seller_details(pool, tg_user_id=tg_user_id)
    if not d or not d.get("seller_id"):
        await state.clear()
        await message.answer("Селлер не найден.")
        return

    new_balance = await add_seller_credits(pool, seller_id=int(d["seller_id"]), delta=delta, reason="admin_grant")
    await state.clear()

    try:
        text, kb = await _build_admin_seller_details_view(pool=pool, bot=bot, tg_user_id=tg_user_id, back_page=back_page)
    except ValueError:
        await message.answer(f"Начислено +{delta}. Баланс: {new_balance}.")
        return

    # Try to refresh the original card message (best UX). If fails — send a new one.
    if isinstance(origin_chat_id, int) and isinstance(origin_message_id, int):
        try:
            await bot.edit_message_text(chat_id=origin_chat_id, message_id=origin_message_id, text=text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)

    await message.answer(f"Начислено +{delta}. Баланс: {new_balance}")


@router.callback_query(F.data.startswith("admin:seller:toggle:"))
async def admin_seller_toggle(cb: CallbackQuery, pool: asyncpg.Pool, bot: Bot) -> None:
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

    try:
        text, kb = await _build_admin_seller_details_view(pool=pool, bot=bot, tg_user_id=tg_user_id, back_page=back_page)
    except ValueError:
        await cb.answer("Селлер не найден", show_alert=True)
        return

    await _safe_edit(cb, text, reply_markup=kb)
    await cb.answer("Обновлено ✅", show_alert=False)


@router.callback_query(F.data == "admin:seller:add")
async def admin_seller_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminAddSeller.tg_user_id)

    await cb.message.answer(
        "Введите Telegram ID селлера (число).\n\n"
        "Подсказка: селлер может узнать свой ID через @FIND_MY_ID_BOT.",
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
    if not d:
        await message.answer("Селлер добавлен, но детали не найдены (проверьте БД).")
        return

    active = bool(d["is_active"])
    last_campaign = d["last_campaign_at"]
    last_campaign_str = last_campaign.strftime("%Y-%m-%d %H:%M") if last_campaign else "—"

    text = (
        "✅ Селлер добавлен\n\n"
        f"Telegram ID: {tg_user_id}\n"
        f"Статус: {'активен' if active else 'выключен'}\n"
        f"Кредиты: {d['credits']}\n"
        f"Магазинов: {d['shops_count']}\n"
        f"Рассылок: {d['campaigns_count']}\n"
        f"Списано кредитов (всего): {d['spent_total']}\n"
        f"Последняя рассылка: {last_campaign_str}\n"
    )

    kb = _admin_seller_details_kb(tg_user_id=tg_user_id, is_active=active, back_page=0).as_markup()
    await message.answer(text, reply_markup=kb)
