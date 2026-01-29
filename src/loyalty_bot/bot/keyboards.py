from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def seller_main_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🏪 Магазины", callback_data="seller:shops")
    kb.button(text="📣 Рассылки", callback_data="seller:campaigns")
    kb.button(text="💰 Купить рассылки", callback_data="credits:menu")
    if is_admin:
        kb.button(text="🛠 Админка", callback_data="admin:home")
        kb.adjust(1, 2, 2)
    else:
        kb.adjust(1, 2, 1)
    return kb.as_markup()


def admin_main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="admin:home")
    kb.button(text="👥 Селлеры", callback_data="admin:sellers:page:0")
    kb.button(text="➕ Добавить селлера", callback_data="admin:seller:add")
    kb.button(text="⬅️ Назад", callback_data="seller:home")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def credits_packages_menu(*, back_cb: str = "seller:home", context: str | None = None) -> InlineKeyboardMarkup:
    """Packages screen for buying campaign credits.

    Step B (test-mode): real payments will be implemented later; for now we expose a test button.
    """
    kb = InlineKeyboardBuilder()
    suffix = f":{context}" if context else ""
    kb.button(text="1 рассылка — 1000 ₽", callback_data=f"credits:pkg:1{suffix}")
    kb.button(text="3 рассылки — 2890 ₽", callback_data=f"credits:pkg:3{suffix}")
    kb.button(text="10 рассылок — 27500 ₽", callback_data=f"credits:pkg:10{suffix}")
    kb.button(text="🧪 ТЕСТОВАЯ ПОКУПКА 3 РАССЫЛКИ", callback_data=f"credits:test:3{suffix}")
    kb.button(text="⬅️ Назад", callback_data=back_cb)
    kb.adjust(1)
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
    kb.button(text="📣 Рассылки", callback_data=f"shop:campaigns:{shop_id}")
    kb.button(text="🎁 Welcome", callback_data=f"shop:welcome:{shop_id}")
    kb.button(text="👥 Подписчики", callback_data=f"shop:stats:{shop_id}")
    if is_admin:
        kb.button(text="✏️ Редактировать", callback_data=f"admin:shop:edit:{shop_id}")
        kb.button(text="🗑 Отключить", callback_data=f"admin:shop:disable:{shop_id}")
    kb.button(text="⬅️ К списку", callback_data="shops:list")
    kb.adjust(2, 2, 1, 2 if is_admin else 0, 1)
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


def campaign_actions(
    campaign_id: int,
    *,
    show_test: bool = False,
    show_send: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👁 Пример сообщения", callback_data=f"campaign:preview:{campaign_id}")
    kb.button(text="💳 Оплатить", callback_data=f"campaign:pay:stub:{campaign_id}")
    if show_test:
        kb.button(text="✅ TEST: оплатить", callback_data=f"campaign:pay:test:{campaign_id}")
    if show_send:
        kb.button(text="🚀 Запустить рассылку", callback_data=f"campaign:send:{campaign_id}")
    kb.button(text="⬅️ Назад", callback_data="campaigns:list")
    kb.adjust(1)
    return kb.as_markup()


def campaign_card_actions(
    campaign_id: int,
    *,
    credits: int,
    back_cb: str = "campaigns:list",
) -> InlineKeyboardMarkup:
    """Actions for campaign card.

    Step D: simplified card UI + credits.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="👁 Пример сообщения", callback_data=f"campaign:preview:{campaign_id}")
    kb.button(text="🚀 Запустить рассылку", callback_data=f"campaign:send:{campaign_id}")
    if credits <= 0:
        kb.button(text="💰 Купить рассылки", callback_data=f"credits:menu:c{campaign_id}")
    kb.button(text="⬅️ Назад", callback_data=back_cb)
    kb.adjust(1)
    return kb.as_markup()


def skip_photo_kb(prefix: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data=f"{prefix}:skip")
    kb.adjust(1)
    return kb.as_markup()


def cancel_kb(callback_data: str = "cancel") -> InlineKeyboardMarkup:
    """Single cancel button for inline forms.

    callback_data is customizable to route cancellation back to a specific screen.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data=callback_data)
    kb.adjust(1)
    return kb.as_markup()


def cancel_skip_kb(*, skip_cb: str, cancel_cb: str, skip_text: str = "⏭ Пропустить") -> InlineKeyboardMarkup:
    """Inline keyboard with Skip + Cancel.

    Used in edit flows where 'Skip' means 'keep current value'.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=skip_text, callback_data=skip_cb)
    kb.button(text="❌ Отмена", callback_data=cancel_cb)
    kb.adjust(1)
    return kb.as_markup()
