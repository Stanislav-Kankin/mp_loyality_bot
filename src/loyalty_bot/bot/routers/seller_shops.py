from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import seller_main_menu, shops_menu, shop_actions, skip_photo_kb
from loyalty_bot.bot.utils.qr import make_qr_png_bytes
from loyalty_bot.db.repo import create_shop, get_shop_for_seller, get_shop_subscription_stats, list_seller_shops, update_shop_welcome

router = Router()


class ShopCreate(StatesGroup):
    name = State()
    category = State()


class ShopWelcome(StatesGroup):
    text = State()
    photo = State()


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids_set


def _is_seller(tg_id: int) -> bool:
    return tg_id in settings.seller_ids_set or _is_admin(tg_id)


def _shop_deeplink(bot_username: str, shop_id: int) -> str:
    return f"https://t.me/{bot_username}?start=shop_{shop_id}"


@router.message(Command("seller"))
async def seller_home_cmd(message: Message) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not _is_seller(tg_id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Панель селлера:", reply_markup=seller_main_menu())


@router.callback_query(F.data == "seller:home")
async def seller_home_cb(cb: CallbackQuery) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("Панель селлера:", reply_markup=seller_main_menu())
    await cb.answer()


@router.callback_query(F.data == "seller:shops")
async def seller_shops_cb(cb: CallbackQuery) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.edit_text("Магазины:", reply_markup=shops_menu())
    await cb.answer()


# Stub for unfinished section
@router.callback_query(F.data == "seller:orders:stub")
async def seller_orders_stub(cb: CallbackQuery) -> None:
    await cb.answer("Заказы будут на следующем этапе.", show_alert=True)


@router.callback_query(F.data == "shops:create")
async def shops_create_start(cb: CallbackQuery, state: FSMContext) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(ShopCreate.name)
    await cb.message.edit_text("Введите название магазина (текстом):")
    await cb.answer()


@router.message(ShopCreate.name)
async def shops_create_name(message: Message, state: FSMContext) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or not _is_seller(tg_id):
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
    if tg_id is None or not _is_seller(tg_id):
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
    if not _is_seller(tg_id):
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
    if not _is_seller(tg_id):
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

    status = "✅ активен" if shop["is_active"] else "⛔️ отключён"
    await cb.message.edit_text(
        f"🏪 {shop['name']}\nКатегория: {shop['category']}\nID: {shop['id']}\nСтатус: {status}",
        reply_markup=shop_actions(shop_id, is_admin=_is_admin(tg_id)),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("shop:link:"))
async def shop_link(cb: CallbackQuery) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
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
async def shop_qr(cb: CallbackQuery) -> None:
    tg_id = cb.from_user.id
    if not _is_seller(tg_id):
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
    if not _is_seller(tg_id):
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

    stats = await get_shop_subscription_stats(pool, shop_id)
    text = (
        f"📊 Подписчики магазина\n\n"
        f"🏪 {shop['name']} (#{shop_id})\n\n"
        f"✅ Подписано: {stats['subscribed']}\n"
        f"🔕 Отписалось: {stats['unsubscribed']}\n"
        f"👥 Всего записей: {stats['total']}\n\n"
        f"UTM/клики добавим на этапе рассылок."
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад к магазину", callback_data=f"shop:open:{shop_id}")
    kb.adjust(1)

    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("shop:welcome:"))
async def shop_welcome_start(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if tg_id not in settings.seller_ids_set and tg_id not in settings.admin_ids_set:
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
    await state.update_data(shop_id=shop_id)
    await state.set_state(ShopWelcome.text)

    await cb.message.answer(
        "Введите welcome-сообщение для покупателей. "
        "Оно будет показано после короткой анкеты (возраст/пол) при первом входе по ссылке/QR."
    )
    await cb.answer()


@router.message(ShopWelcome.text)
async def shop_welcome_text(message: Message, state: FSMContext) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or (tg_id not in settings.seller_ids_set and tg_id not in settings.admin_ids_set):
        await message.answer("Нет доступа.")
        return

    text = (message.text or "").strip()
    if len(text) < 1 or len(text) > 3500:
        await message.answer("Текст должен быть от 1 до 3500 символов. Введите ещё раз:")
        return

    await state.update_data(welcome_text=text)
    await state.set_state(ShopWelcome.photo)

    await message.answer(
        "Пришлите картинку для welcome-сообщения или нажмите «Пропустить».",
        reply_markup=skip_photo_kb("shop:welcome:skip_photo"),
    )


@router.callback_query(ShopWelcome.photo, F.data == "shop:welcome:skip_photo")
async def shop_welcome_skip_photo(cb: CallbackQuery, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    if tg_id not in settings.seller_ids_set and tg_id not in settings.admin_ids_set:
        await cb.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    welcome_text = data.get("welcome_text")

    if not isinstance(shop_id, int) or not isinstance(welcome_text, str):
        await state.clear()
        await cb.message.answer("Ошибка состояния. Откройте магазин и попробуйте ещё раз.")
        await cb.answer()
        return

    await update_shop_welcome(pool, seller_tg_user_id=tg_id, shop_id=shop_id, welcome_text=welcome_text)
    await state.clear()
    await cb.message.answer("Welcome-сообщение сохранено ✅")
    await cb.answer()


@router.message(ShopWelcome.photo)
async def shop_welcome_photo(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None or (tg_id not in settings.seller_ids_set and tg_id not in settings.admin_ids_set):
        await message.answer("Нет доступа.")
        return

    data = await state.get_data()
    shop_id = data.get("shop_id")
    welcome_text = data.get("welcome_text")
    if not isinstance(shop_id, int) or not isinstance(welcome_text, str):
        await state.clear()
        await message.answer("Ошибка состояния. Откройте магазин и попробуйте ещё раз.")
        return

    if not message.photo:
        await message.answer("Пришлите именно картинку (photo) или нажмите «Пропустить».")
        return

    file_id = message.photo[-1].file_id
    await update_shop_welcome(
        pool,
        seller_tg_user_id=tg_id,
        shop_id=shop_id,
        welcome_text=welcome_text,
        welcome_photo_file_id=file_id,
    )
    await state.clear()
    await message.answer("Welcome-сообщение (с картинкой) сохранено ✅")
