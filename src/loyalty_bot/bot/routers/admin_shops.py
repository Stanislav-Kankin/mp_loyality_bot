from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import admin_main_menu, admin_shops_list_kb, shop_actions
from loyalty_bot.db.repo import get_shop_by_id, list_all_shops, set_shop_active, update_shop

router = Router()


class AdminShopEdit(StatesGroup):
    name = State()
    category = State()


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids_set


@router.callback_query(F.data == "admin:home")
async def admin_home(cb: CallbackQuery) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("Админ-панель:", reply_markup=admin_main_menu())
    await cb.answer()


@router.callback_query(F.data == "admin:shops:list")
async def admin_shops_list(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    shops = await list_all_shops(pool, limit=20)
    if not shops:
        await cb.message.edit_text("Магазинов пока нет.", reply_markup=admin_main_menu())
        await cb.answer()
        return

    buttons = []
    for sh in shops:
        prefix = "✅" if sh["is_active"] else "⛔️"
        title = f"{prefix} #{sh['id']} {sh['name']}"
        buttons.append((sh["id"], title))

    await cb.message.edit_text("Все магазины (последние 20):", reply_markup=admin_shops_list_kb(buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("admin:shop:open:"))
async def admin_shop_open(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    shop = await get_shop_by_id(pool, shop_id)
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    status = "✅ активен" if shop["is_active"] else "⛔️ отключён"
    await cb.message.edit_text(
        f"🏪 {shop['name']}\n"
        f"Категория: {shop['category']}\n"
        f"ID: {shop['id']}\n"
        f"Seller tg_id: {shop['seller_tg_user_id']}\n"
        f"Статус: {status}",
        reply_markup=shop_actions(shop_id, is_admin=True),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("admin:shop:disable:"))
async def admin_shop_disable(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    await set_shop_active(pool, shop_id, False)
    await cb.answer("Магазин отключён ✅", show_alert=True)


@router.callback_query(F.data.startswith("admin:shop:edit:"))
async def admin_shop_edit_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    shop = await get_shop_by_id(pool, shop_id)
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    await state.clear()
    await state.update_data(shop_id=shop_id)
    await state.set_state(AdminShopEdit.name)

    await cb.message.answer(f"Редактирование магазина #{shop_id}.\nТекущее название: {shop['name']}\n\nВведите новое название:")
    await cb.answer()


@router.message(AdminShopEdit.name)
async def admin_shop_edit_name(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое. Введите ещё раз:")
        return

    await state.update_data(name=name)
    await state.set_state(AdminShopEdit.category)
    await message.answer("Введите новую категорию:")


@router.message(AdminShopEdit.category)
async def admin_shop_edit_category(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return

    category = (message.text or "").strip()
    if len(category) < 2:
        await message.answer("Категория слишком короткая. Введите ещё раз:")
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    name = data.get("name")
    if not isinstance(shop_id, int) or not isinstance(name, str):
        await state.clear()
        await message.answer("Ошибка состояния. Откройте магазин заново через 🛠 Админ.")
        return

    await update_shop(pool, shop_id, name=name, category=category)
    await state.clear()
    await message.answer(f"Магазин #{shop_id} обновлён ✅")
