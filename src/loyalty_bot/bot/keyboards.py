from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def seller_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏪 Магазины", callback_data="seller:shops")
    kb.button(text="📣 Рассылки", callback_data="seller:campaigns")
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


def shop_actions(shop_id: int, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📎 Ссылка", callback_data=f"shop:link:{shop_id}")
    kb.button(text="🔳 QR", callback_data=f"shop:qr:{shop_id}")
    kb.button(text="👥 Подписчики", callback_data=f"shop:stats:{shop_id}")
    if is_admin:
        kb.button(text="✏️ Редактировать", callback_data=f"admin:shop:edit:{shop_id}")
        kb.button(text="🗑 Отключить", callback_data=f"admin:shop:disable:{shop_id}")
    kb.button(text="⬅️ К списку", callback_data="shops:list")
    kb.adjust(2, 1, 2 if is_admin else 0, 1)
    return kb.as_markup()


def buyer_subscription_menu(shop_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔕 Отписаться", callback_data=f"buyer:unsub:{shop_id}")
    kb.adjust(1)
    return kb.as_markup()


def buyer_gender_menu(shop_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👨 Мужской", callback_data=f"buyer:gender:{shop_id}:m")
    kb.button(text="👩 Женский", callback_data=f"buyer:gender:{shop_id}:f")
    kb.button(text="🤷 Не хочу указывать", callback_data=f"buyer:gender:{shop_id}:u")
    kb.adjust(1)
    return kb.as_markup()


def campaigns_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать рассылку", callback_data="campaigns:create")
    kb.button(text="📋 Мои рассылки", callback_data="campaigns:list")
    kb.button(text="⬅️ Назад", callback_data="seller:home")
    kb.adjust(1)
    return kb.as_markup()


def campaigns_list_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cid, title in items:
        kb.button(text=title, callback_data=f"campaign:open:{cid}")
    kb.button(text="⬅️ Назад", callback_data="seller:campaigns")
    kb.adjust(1)
    return kb.as_markup()


def campaign_actions(campaign_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Оплатить", callback_data=f"campaign:pay:stub:{campaign_id}")
    kb.button(text="⬅️ Назад", callback_data="campaigns:list")
    kb.adjust(1)
    return kb.as_markup()
