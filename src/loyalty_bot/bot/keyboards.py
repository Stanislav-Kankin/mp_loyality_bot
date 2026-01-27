from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def seller_main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏪 Магазины", callback_data="seller:shops")
    kb.button(text="📣 Рассылки", callback_data="seller:campaigns:stub")
    kb.button(text="🧾 Заказы", callback_data="seller:orders:stub")
    if is_admin:
        kb.button(text="🛠 Админ", callback_data="admin:home")
    kb.adjust(1, 2, 1 if is_admin else 0)
    return kb.as_markup()


def shops_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать магазин", callback_data="shops:create")
    kb.button(text="📋 Мои магазины", callback_data="shops:list")
    kb.button(text="⬅️ Назад", callback_data="seller:home")
    kb.adjust(1)
    return kb.as_markup()


def shop_actions(shop_id: int, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Ссылка", callback_data=f"shop:link:{shop_id}")
    kb.button(text="🔳 QR", callback_data=f"shop:qr:{shop_id}")
    if is_admin:
        kb.button(text="✏️ Редактировать", callback_data=f"admin:shop:edit:{shop_id}")
        kb.button(text="🗑 Отключить", callback_data=f"admin:shop:disable:{shop_id}")
    kb.button(text="⬅️ К списку", callback_data="shops:list")
    kb.adjust(2, 2 if is_admin else 1, 1)
    return kb.as_markup()


def admin_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏪 Все магазины", callback_data="admin:shops:list")
    kb.button(text="⬅️ Назад", callback_data="seller:home")
    kb.adjust(1)
    return kb.as_markup()


def admin_shops_list_kb(shop_buttons: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for shop_id, title in shop_buttons:
        kb.button(text=title, callback_data=f"admin:shop:open:{shop_id}")
    kb.button(text="⬅️ Назад", callback_data="admin:home")
    kb.adjust(1)
    return kb.as_markup()
