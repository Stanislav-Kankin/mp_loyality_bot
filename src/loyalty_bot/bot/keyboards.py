from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def seller_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏪 Магазины", callback_data="seller:shops")
    kb.button(text="📣 Рассылки", callback_data="seller:campaigns:stub")
    kb.button(text="🧾 Заказы", callback_data="seller:orders:stub")
    kb.adjust(1, 2)
    return kb.as_markup()


def shops_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать магазин", callback_data="shops:create")
    kb.button(text="📋 Мои магазины", callback_data="shops:list")
    kb.button(text="⬅️ Назад", callback_data="seller:home")
    kb.adjust(1)
    return kb.as_markup()


def back_to_shops_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К магазинам", callback_data="seller:shops")
    kb.as_markup()
    return kb.as_markup()


def shop_actions(shop_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Ссылка", callback_data=f"shop:link:{shop_id}")
    kb.button(text="🔳 QR", callback_data=f"shop:qr:{shop_id}")
    kb.button(text="⬅️ К списку", callback_data="shops:list")
    kb.adjust(2, 1)
    return kb.as_markup()
