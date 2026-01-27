from __future__ import annotations

import asyncpg
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from loyalty_bot.config import settings
from loyalty_bot.bot.keyboards import seller_main_menu
from loyalty_bot.db.repo import ensure_customer, ensure_seller, shop_exists, subscribe_customer_to_shop

router = Router()


def _parse_shop_payload(args: str | None) -> int | None:
    if not args:
        return None
    # MVP payload format: "shop_<id>"
    if not args.startswith("shop_"):
        return None
    raw = args.removeprefix("shop_").strip()
    if not raw.isdigit():
        return None
    return int(raw)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, pool: asyncpg.Pool) -> None:
    tg_id = message.from_user.id if message.from_user else None
    if tg_id is None:
        await message.answer("Ошибка: не удалось определить Telegram user id.")
        return

    shop_id = _parse_shop_payload(command.args)

    # Buyer flow (opt-in via deep-link)
    if shop_id is not None:
        if not await shop_exists(pool, shop_id):
            await message.answer("Магазин не найден. Проверьте ссылку/QR.")
            return

        customer_id = await ensure_customer(pool, tg_id)
        await subscribe_customer_to_shop(pool, shop_id=shop_id, customer_id=customer_id)

        await message.answer(
            "Вы подписаны на уведомления магазина ✅\n\n"
            "Чтобы отписаться — кнопка появится позже (Этап 1.3)."
        )
        return

    # Seller flow (allowlist from env)
    if tg_id in settings.seller_ids_set or tg_id in settings.admin_ids_set:
        await ensure_seller(pool, tg_id)
        await message.answer("Панель селлера:", reply_markup=seller_main_menu(is_admin=(tg_id in settings.admin_ids_set)))
        return

    await message.answer(
        "Это бот лояльности магазина.\n\n"
        "Чтобы подписаться — перейдите по ссылке/QR от продавца.\n"
        "Если вы продавец — попросите администратора добавить ваш TG id в SELLER_TG_IDS."
    )
