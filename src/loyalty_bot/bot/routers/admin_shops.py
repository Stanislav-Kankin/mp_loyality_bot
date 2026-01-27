from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.db.repo import get_shop_by_id, set_shop_active, update_shop

router = Router()


class AdminShopEdit(StatesGroup):
    name = State()
    category = State()


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids_set


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

    await cb.message.answer(
        f"Редактирование магазина #{shop_id}.\n"
        f"Текущее название: {shop['name']}\n\n"
        f"Введите новое название:"
    )
    await cb.answer()


@router.message(AdminShopEdit.name)
async def admin_shop_edit_name(message: Message, state: FSMContext) -> None:
    if not message.from_user or not _is_admin(message.from_user.id):
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
    if not message.from_user or not _is_admin(message.from_user.id):
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
        await message.answer("Ошибка состояния. Откройте магазин заново.")
        return

    await update_shop(pool, shop_id, name=name, category=category)
    await state.clear()
    await message.answer(f"Магазин #{shop_id} обновлён ✅")
