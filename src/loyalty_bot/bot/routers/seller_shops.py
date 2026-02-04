from __future__ import annotations

import asyncio
import logging
import asyncpg
from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, LabeledPrice

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import (
    cancel_kb,
    cancel_skip_kb,
    credits_packages_menu,
    seller_main_menu,
    shops_menu,
    shop_actions,
    skip_photo_kb,
)
from loyalty_bot.bot.utils.qr import make_qr_png_bytes
from loyalty_bot.db.repo import (
    add_seller_credits,
    create_shop,
    ensure_seller,
    get_seller_credits,
    is_seller_allowed,
    get_shop_for_seller,
    get_shop_welcome,
    get_shop_audience_counts,
    list_seller_shops,
    update_shop_welcome,
)

router = Router()

logger = logging.getLogger(__name__)


class ShopCreate(StatesGroup):
    name = State()
    category = State()

class ShopWelcome(StatesGroup):
    text = State()
    photo = State()
    button_text = State()
    url = State()


def _is_http_url(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith("http://") or v.startswith("https://")


async def _safe_answer(message: Message, text: str, **kwargs) -> None:
    """Send a message with a minimal retry on transient network errors."""
    try:
        await message.answer(text, **kwargs)
    except TelegramNetworkError:
        # Telegram sometimes resets connections; retry once.
        await asyncio.sleep(0.8)
        await message.answer(text, **kwargs)



def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids_set


async def _is_seller(pool: asyncpg.Pool, tg_id: int) -> bool:
    if _is_admin(tg_id):
        return True
    # Prefer DB allowlist; keep legacy env SELLER_TG_IDS as fallback.
    return await is_seller_allowed(pool, tg_id) or (tg_id in settings.seller_ids_set)


def _shop_deeplink(bot_username: str, shop_id: int) -> str:
    return f"https://t.me/{bot_username}?start=shop_{shop_id}"


@router.message(Command("seller"))
async def seller_home_cmd(message: Message, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        await message.answer("Нет доступа.")
        return

    await ensure_seller(pool, tg_id)
    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    await message.answer(
        f"Панель селлера:\nДоступно рассылок: {credits}",
        reply_markup=seller_main_menu(is_admin=_is_admin(tg_id)),
    )


@router.callback_query(F.data == "seller:home")
async def seller_home_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    await ensure_seller(pool, tg_id)
    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    await cb.message.edit_text(
        f"Панель селлера:\nДоступно рассылок: {credits}",
        reply_markup=seller_main_menu(is_admin=_is_admin(tg_id)),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("credits:menu"))
async def credits_menu_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    await ensure_seller(pool, tg_id)
    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)

    parts = (cb.data or "").split(":")
    ctx = parts[2] if len(parts) >= 3 and parts[2] else None

    back_cb = "seller:home"
    if isinstance(ctx, str) and ctx.startswith("c") and ctx[1:].isdigit():
        back_cb = f"campaign:open:{int(ctx[1:])}"

    text = (
        "💰 Покупка рассылок\n"
        f"Текущий баланс: {credits}\n\n"
        "Выберите пакет и оплатите через Telegram Payments (ЮKassa)."
    )
    await cb.message.edit_text(text, reply_markup=credits_packages_menu(back_cb=back_cb, context=ctx))
    await cb.answer()


@router.callback_query(F.data.startswith("credits:pkg:"))
async def credits_pkg_buy_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    """Start credits pack payment by sending Telegram invoice."""
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    parts = (cb.data or "").split(":")
    # expected: credits:pkg:<qty>[:ctx]
    if len(parts) < 3 or not parts[2].isdigit():
        await cb.answer("Некорректные данные", show_alert=True)
        return

    qty = int(parts[2])
    ctx = parts[3] if len(parts) >= 4 and parts[3] else None
    if qty not in (1, 3, 10):
        await cb.answer("Некорректный пакет", show_alert=True)
        return

    amount_minor_map = {
        1: settings.credits_pack_1_minor,
        3: settings.credits_pack_3_minor,
        10: settings.credits_pack_10_minor,
    }
    amount_minor = int(amount_minor_map[qty])

    title = f"Пакет рассылок: {qty}"
    description = f"Покупка пакета на {qty} рассылок."
    payload = f"credits_pack:{qty}" + (f":{ctx}" if ctx else "")

    logger.info(
        "send_invoice credits_pack qty=%s amount_minor=%s tg_id=%s payload=%s",
        qty,
        amount_minor,
        tg_id,
        payload,
    )

    await cb.bot.send_invoice(
        chat_id=tg_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=settings.payment_provider_token,
        currency=settings.currency,
        prices=[LabeledPrice(label=title, amount=amount_minor)],
    )
    await cb.answer("Счет выставлен. Проверьте сообщение с оплатой 👇")


@router.callback_query(F.data.startswith("credits:test:3"))
async def credits_test_buy_3_cb(cb: CallbackQuery) -> None:
    """Deprecated: test purchase is disabled.

    Kept to avoid crashes if old messages with callbacks are still around.
    """
    await cb.answer("Тестовая покупка отключена.", show_alert=True)


@router.callback_query(F.data == "seller:shops")
async def seller_shops_cb(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("Магазины:", reply_markup=shops_menu())
    await cb.answer()


# Stub for unfinished section
@router.callback_query(F.data == "seller:orders:stub")
async def seller_orders_stub(cb: CallbackQuery) -> None:
    await cb.answer("Заказы будут на следующем этапе.", show_alert=True)


@router.callback_query(F.data == "shops:create")
async def shops_create_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(ShopCreate.name)
    await cb.message.edit_text("Введите название магазина (текстом):")
    await cb.answer()


@router.message(ShopCreate.name)
async def shops_create_name(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        await message.answer("Нет доступа.")
        return

    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое. Введите ещё раз:")
        return

    await state.update_data(name=name)
    await state.set_state(ShopCreate.category)
    await message.answer("Введите категорию магазина (например: Одежда, Косметика, Электроника):")


@router.message(ShopCreate.category)
async def shops_create_category(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        await message.answer("Нет доступа.")
        return

    category = (message.text or "").strip()
    if len(category) < 2:
        await message.answer("Категория слишком короткая. Введите ещё раз:")
        return

    data = await state.get_data()
    name = str(data.get("name", "")).strip()
    if not name:
        await state.clear()
        await message.answer("Ошибка состояния. Начните заново: /seller")
        return

    shop_id = await create_shop(pool, seller_tg_user_id=tg_id, name=name, category=category)
    await state.clear()

    bot_username = (await message.bot.get_me()).username
    link = _shop_deeplink(bot_username, shop_id)

    await message.answer(
        f"Магазин создан ✅\n\n"
        f"🏪 {name}\n"
        f"Категория: {category}\n\n"
        f"Ссылка для покупателей:\n{link}",
        reply_markup=shop_actions(shop_id, is_admin=_is_admin(tg_id)),
    )


@router.callback_query(F.data == "shops:list")
async def shops_list(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    shops = await list_seller_shops(pool, seller_tg_user_id=tg_id)
    if not shops:
        await cb.message.edit_text("У вас пока нет магазинов.", reply_markup=shops_menu())
        await cb.answer()
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    for sh in shops[:10]:
        prefix = "✅" if sh["is_active"] else "⛔️"
        kb.button(text=f"{prefix} 🏪 {sh['name']}", callback_data=f"shop:open:{sh['id']}")
    kb.button(text="⬅️ Назад", callback_data="seller:shops")
    kb.adjust(1)

    await cb.message.edit_text("Ваши магазины:", reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("shop:open:"))
async def shop_open(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
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
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    credits = await get_seller_credits(pool, seller_tg_user_id=tg_id)
    status = "✅ активен" if shop["is_active"] else "⛔️ отключён"
    await cb.message.edit_text(
        f"🏪 {shop['name']}\nКатегория: {shop['category']}\nДоступно рассылок: {credits}\nСтатус: {status}",
        reply_markup=shop_actions(shop_id, is_admin=_is_admin(tg_id)),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("shop:link:"))
async def shop_link(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    bot_username = (await cb.bot.get_me()).username
    link = _shop_deeplink(bot_username, shop_id)
    await cb.message.answer(f"Ссылка для покупателей:\n{link}")
    await cb.answer()


@router.callback_query(F.data.startswith("shop:qr:"))
async def shop_qr(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    bot_username = (await cb.bot.get_me()).username
    link = _shop_deeplink(bot_username, shop_id)
    png_bytes = make_qr_png_bytes(link)
    file = BufferedInputFile(png_bytes, filename=f"shop_{shop_id}.png")

    await cb.message.answer_photo(photo=file, caption=f"QR для подписки на магазин\n\n{link}")
    await cb.answer()

@router.callback_query(F.data.startswith("shop:stats:"))
async def shop_stats(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
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
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    stats = await get_shop_audience_counts(pool, shop_id)
    gender = stats.get("gender") or {}
    age = stats.get("age") or {}
    g_male = int(gender.get("male", 0))
    g_female = int(gender.get("female", 0))
    g_unknown = int(gender.get("unknown", 0))

    a_0_17 = int(age.get("0_17", 0))
    a_18_27 = int(age.get("18_27", 0))
    a_28_35 = int(age.get("28_35", 0))
    a_36_45 = int(age.get("36_45", 0))
    a_46_49 = int(age.get("46_49", 0))
    a_50_plus = int(age.get("50_plus", 0))
    a_unknown = int(age.get("unknown", 0))


    text_msg = f"""📊 Подписчики магазина

🏪 {shop['name']} (#{shop_id})

👥 Всего записей: {int(stats.get('total', 0))}
✅ Активные: {int(stats.get('subscribed', 0))}
🔕 Отписанные: {int(stats.get('unsubscribed', 0))}

👤 Пол (среди активных):
  👨 Муж: {g_male}
  👩 Жен: {g_female}
  🤷 Не указан: {g_unknown}

🎂 Возраст (среди активных):
  ≤17: {a_0_17}
  18–27: {a_18_27}
  28–35: {a_28_35}
  36–45: {a_36_45}
  46–49: {a_46_49}
  50+: {a_50_plus}
  Не указан: {a_unknown}

ℹ️ Пол/возраст считаются среди активных (подписанных)."""

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад к магазину", callback_data=f"shop:open:{shop_id}")
    kb.adjust(1)

    await cb.message.edit_text(text_msg, reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("shop:welcome:"))
async def shop_welcome_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
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
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    await state.clear()

    welcome = await get_shop_welcome(pool, shop_id=shop_id)
    w_text = (welcome.get("welcome_text") if welcome else "") or ""
    has_photo = bool(welcome and welcome.get("welcome_photo_file_id"))
    w_btn = (welcome.get("welcome_button_text") if welcome else "") or ""
    w_btn = (welcome.get("welcome_button_text") if welcome else "") or ""
    w_url = (welcome.get("welcome_url") if welcome else "") or ""

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить", callback_data=f"shopwelcome:edit:{shop_id}")
    kb.button(text="👁 Пример как покупатель", callback_data=f"shopwelcome:preview:{shop_id}")
    kb.button(text="⬅️ Назад к магазину", callback_data=f"shop:open:{shop_id}")
    kb.adjust(1)

    summary = (
        f"🎁 Welcome для магазина\n\n"
        f"🏪 {shop['name']} (#{shop_id})\n\n"
        f"📝 Текст: {'есть' if w_text.strip() else '—'}\n"
        f"🖼 Фото: {'есть' if has_photo else '—'}\n"
        f"🔘 Кнопка: {w_btn.strip() if w_btn.strip() else '—'}\n"
        f"🔗 Ссылка: {w_url.strip() if w_url.strip() else '—'}\n\n"
        f"Нажмите «Изменить», чтобы настроить текст/фото/ссылку."
    )

    await cb.message.edit_text(summary, reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("shopwelcome:preview:"))
async def shop_welcome_preview(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await cb.answer("Некорректный id", show_alert=True)
        return
    shop_id = int(raw_id)

    welcome = await get_shop_welcome(pool, shop_id=shop_id)
    if not welcome:
        await cb.answer("Welcome ещё не настроен", show_alert=True)
        return

    # Превью должно быть ровно 1 сообщением (как получит покупатель)
    text = (welcome.get("welcome_text") or "").strip()
    photo_file_id = welcome.get("welcome_photo_file_id")
    btn_text = (welcome.get("welcome_button_text") or "").strip()
    url = (welcome.get("welcome_url") or "").strip() or None

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = None
    if url:
        b = InlineKeyboardBuilder()
        b.button(text=(btn_text or "🔗 Ссылка"), url=url)
        b.adjust(1)
        kb = b.as_markup()

    if photo_file_id:
        # Caption max is 1024
        caption = text[:1024] if text else None
        await cb.message.answer_photo(photo=photo_file_id, caption=caption, reply_markup=kb)
    else:
        # Text max is 4096
        await cb.message.answer((text or "(пусто)")[:4096], reply_markup=kb)

    await cb.answer()


@router.callback_query(F.data.startswith("shopwelcome:edit:"))
async def shop_welcome_edit_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
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
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    # Prefill current values so that "Пропустить" keeps them.
    welcome = await get_shop_welcome(pool, shop_id=shop_id)
    cur_text = (welcome.get("welcome_text") if welcome else "") or ""
    cur_photo_file_id = welcome.get("welcome_photo_file_id") if welcome else None
    cur_button_text = (welcome.get("welcome_button_text") if welcome else "") or ""
    cur_url = (welcome.get("welcome_url") if welcome else "") or ""

    await state.clear()
    await state.update_data(
        shop_id=shop_id,
        cur_welcome_text=cur_text,
        cur_welcome_photo_file_id=cur_photo_file_id,
        cur_welcome_button_text=cur_button_text,
        cur_welcome_url=cur_url,
    )
    await state.set_state(ShopWelcome.text)

    await cb.message.answer(
        """Введите welcome-текст для покупателей.

⏭ «Пропустить» — оставить текущий текст.

Например: какие бонусы получит клиент (промокод, скидка, подарки и т.д.).""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:text",
            cancel_cb=f"shopwelcome:cancel:{shop_id}",
        ),
    )
    await cb.answer()


async def _shop_welcome_finish_update(*, message: Message, pool: asyncpg.Pool, tg_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    shop_id = data.get("shop_id")

    welcome_text = (data.get("welcome_text") or "").strip()
    photo_file_id = data.get("welcome_photo_file_id")
    button_text = (data.get("welcome_button_text") or "").strip()
    url = (data.get("welcome_url") or "").strip()

    if not isinstance(shop_id, int):
        await state.clear()
        await message.answer("Ошибка состояния. Попробуйте ещё раз.")
        return

    if not welcome_text:
        await message.answer(
            "Welcome-текст пустой. Введите текст (или сначала задайте его, затем можно пропускать шаги)."
        )
        return
    if not button_text:
        await message.answer(
            "Текст кнопки пустой. Введите текст кнопки (или сначала задайте его, затем можно пропускать шаги)."
        )
        return
    if not _is_http_url(url):
        await message.answer("Ссылка пустая или некорректная. Введите URL, который начинается с http:// или https://")
        return

    await update_shop_welcome(
        pool,
        seller_tg_user_id=tg_id,
        shop_id=shop_id,
        welcome_text=welcome_text,
        welcome_photo_file_id=str(photo_file_id) if photo_file_id else None,
        welcome_button_text=button_text or None,
        welcome_url=url,
    )

    await state.clear()
    await message.answer("Welcome-сообщение обновлено ✅")


@router.callback_query(F.data == "shopwelcome:skip:text")
async def shop_welcome_skip_text(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    cur_text = (data.get("cur_welcome_text") or "").strip()

    if not isinstance(shop_id, int):
        await state.clear()
        await cb.message.answer("Ошибка состояния. Попробуйте ещё раз.")
        await cb.answer()
        return

    if not cur_text:
        await cb.message.answer("Текущий welcome-текст пустой. Введите текст, чтобы продолжить.")
        await cb.answer()
        return

    await state.update_data(welcome_text=cur_text)
    await state.set_state(ShopWelcome.photo)

    await cb.message.answer(
        """Пришлите картинку для welcome-сообщения или нажмите «Пропустить».

⏭ «Пропустить» — оставить текущее фото.""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:photo",
            cancel_cb=f"shopwelcome:cancel:{shop_id}",
        ),
    )
    await cb.answer()


@router.message(ShopWelcome.text)
async def shop_welcome_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст пустой. Введите welcome-текст.")
        return

    await state.update_data(welcome_text=text)
    await state.set_state(ShopWelcome.photo)
    data = await state.get_data()
    shop_id = data.get("shop_id")

    await _safe_answer(
        message,
        """Пришлите картинку для welcome-сообщения или нажмите «Пропустить».

⏭ «Пропустить» — оставить текущее фото.""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:photo",
            cancel_cb=f"shopwelcome:cancel:{shop_id}" if isinstance(shop_id, int) else "shopwelcome:cancel:0",
        ),
    )


@router.callback_query(F.data == "shopwelcome:skip:photo")
async def shop_welcome_skip_photo(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    welcome_text = data.get("welcome_text")
    cur_photo = data.get("cur_welcome_photo_file_id")

    if not isinstance(shop_id, int) or not isinstance(welcome_text, str):
        await state.clear()
        await cb.message.answer("Ошибка состояния. Попробуйте ещё раз.")
        await cb.answer()
        return

    # In edit flow: 'Skip' keeps current photo (may be None).
    await state.update_data(welcome_photo_file_id=cur_photo if cur_photo else None)
    await state.set_state(ShopWelcome.button_text)

    await cb.message.answer(
        """Введите текст кнопки, которую увидит покупатель (как в рассылке).

⏭ «Пропустить» — оставить текущее значение.

Например: Открыть магазин / Получить скидку / Перейти на сайт""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:button_text",
            cancel_cb=f"shopwelcome:cancel:{shop_id}",
        ),
    )
    await cb.answer()


@router.message(ShopWelcome.photo)
async def shop_welcome_photo(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id
    if not await _is_seller(pool, tg_id):
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    welcome_text = data.get("welcome_text")
    if not isinstance(shop_id, int) or not isinstance(welcome_text, str):
        await state.clear()
        await message.answer("Ошибка состояния. Попробуйте ещё раз.")
        return

    if not message.photo:
        await message.answer(
            "Пришлите картинку (как фото) или нажмите «Пропустить».",
            reply_markup=cancel_skip_kb(
                skip_cb="shopwelcome:skip:photo",
                cancel_cb=f"shopwelcome:cancel:{shop_id}",
            ),
        )
        return

    photo_file_id = message.photo[-1].file_id
    await state.update_data(welcome_photo_file_id=photo_file_id)
    await state.set_state(ShopWelcome.button_text)

    await message.answer(
        """Введите текст кнопки, которую увидит покупатель (как в рассылке).

⏭ «Пропустить» — оставить текущее значение.

Например: Открыть магазин / Получить скидку / Перейти на сайт""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:button_text",
            cancel_cb=f"shopwelcome:cancel:{shop_id}",
        ),
    )


@router.callback_query(F.data == "shopwelcome:skip:button_text")
async def shop_welcome_skip_button_text(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    cur_btn = (data.get("cur_welcome_button_text") or "").strip()

    if not isinstance(shop_id, int):
        await state.clear()
        await cb.message.answer("Ошибка состояния. Попробуйте ещё раз.")
        await cb.answer()
        return

    if not cur_btn:
        await cb.message.answer("Текущий текст кнопки пустой. Введите текст кнопки, чтобы продолжить.")
        await cb.answer()
        return

    await state.update_data(welcome_button_text=cur_btn)
    await state.set_state(ShopWelcome.url)

    await cb.message.answer(
        """Введите ссылку (URL), которую получит покупатель кнопкой.

⏭ «Пропустить» — оставить текущую ссылку.

Формат: https://...""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:url",
            cancel_cb=f"shopwelcome:cancel:{shop_id}",
        ),
    )
    await cb.answer()


@router.message(ShopWelcome.button_text)
async def shop_welcome_button_text(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        return

    btn = (message.text or "").strip()
    if not btn:
        await message.answer("Название пустое. Введите текст для кнопки.")
        return
    if len(btn) > 32:
        await message.answer("Слишком длинно. Максимум 32 символа.")
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    if not isinstance(shop_id, int):
        await state.clear()
        await message.answer("Ошибка состояния. Попробуйте ещё раз.")
        return

    await state.update_data(welcome_button_text=btn)
    await state.set_state(ShopWelcome.url)
    await message.answer(
        f"""Введите ссылку (URL), которую получит покупатель кнопкой «{btn}».

⏭ «Пропустить» — оставить текущую ссылку.

Формат: https://...""",
        reply_markup=cancel_skip_kb(
            skip_cb="shopwelcome:skip:url",
            cancel_cb=f"shopwelcome:cancel:{shop_id}",
        ),
    )


@router.callback_query(F.data == "shopwelcome:skip:url")
async def shop_welcome_skip_url(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    cur_url = (data.get("cur_welcome_url") or "").strip()

    if not isinstance(shop_id, int):
        await state.clear()
        await cb.message.answer("Ошибка состояния. Попробуйте ещё раз.")
        await cb.answer()
        return

    if not _is_http_url(cur_url):
        await cb.message.answer("Текущая ссылка пустая или некорректная. Введите URL, чтобы продолжить.")
        await cb.answer()
        return

    await state.update_data(welcome_url=cur_url)
    # Finalize
    await _shop_welcome_finish_update(message=cb.message, pool=pool, tg_id=tg_id, state=state)
    await cb.answer()


@router.message(ShopWelcome.url)
async def shop_welcome_url(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not await _is_seller(pool, tg_id):
        return

    url = (message.text or "").strip()
    if not _is_http_url(url):
        await message.answer("Некорректная ссылка. Введите URL, который начинается с http:// или https://")
        return

    await state.update_data(welcome_url=url)
    await _shop_welcome_finish_update(message=message, pool=pool, tg_id=tg_id, state=state)


@router.callback_query(F.data.startswith("shopwelcome:cancel:"))
async def shop_welcome_cancel(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if not await _is_seller(pool, tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return

    raw_id = cb.data.split(":")[-1]
    if not raw_id.isdigit():
        await state.clear()
        await cb.answer()
        return
    shop_id = int(raw_id)

    await state.clear()

    shop = await get_shop_for_seller(pool, seller_tg_user_id=tg_id, shop_id=shop_id)
    if shop is None:
        await cb.answer("Магазин не найден", show_alert=True)
        return

    welcome = await get_shop_welcome(pool, shop_id=shop_id)
    w_text = (welcome.get("welcome_text") if welcome else "") or ""
    has_photo = bool(welcome and welcome.get("welcome_photo_file_id"))
    w_btn = (welcome.get("welcome_button_text") if welcome else "") or ""
    w_url = (welcome.get("welcome_url") if welcome else "") or ""

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить", callback_data=f"shopwelcome:edit:{shop_id}")
    kb.button(text="👁 Пример как покупатель", callback_data=f"shopwelcome:preview:{shop_id}")
    kb.button(text="⬅️ Назад к магазину", callback_data=f"shop:open:{shop_id}")
    kb.adjust(1)

    summary = (
        f"🎁 Welcome для магазина\n\n"
        f"🏪 {shop['name']} (#{shop_id})\n\n"
        f"📝 Текст: {'есть' if w_text.strip() else '—'}\n"
        f"🖼 Фото: {'есть' if has_photo else '—'}\n"
        f"🔘 Кнопка: {w_btn.strip() if w_btn.strip() else '—'}\n"
        f"🔗 Ссылка: {w_url.strip() if w_url.strip() else '—'}\n\n"
        f"Нажмите «Изменить», чтобы настроить текст/фото/ссылку."
    )

    await cb.message.edit_text(summary, reply_markup=kb.as_markup())
    await cb.answer()
