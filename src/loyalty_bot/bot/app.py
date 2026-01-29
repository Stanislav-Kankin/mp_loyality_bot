from __future__ import annotations

import asyncio
import logging
import pathlib

from aiogram import Bot, Dispatcher

from loyalty_bot.config import settings
from loyalty_bot.db.migrations import apply_migrations
from loyalty_bot.db.pool import create_pool
from loyalty_bot.logging_setup import setup_logging
from loyalty_bot.bot.middlewares.db import DbMiddleware
from loyalty_bot.bot.routers.start import router as start_router
from loyalty_bot.bot.routers.seller_shops import router as seller_shops_router
from loyalty_bot.bot.routers.seller_campaigns import router as seller_campaigns_router
from loyalty_bot.bot.routers.payments import router as payments_router
from loyalty_bot.bot.routers.admin_shops import router as admin_shops_router


logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging(level=settings.log_level, service_name="bot", log_dir=settings.log_dir)

    pool = await create_pool(settings.database_dsn)
    async with pool.acquire() as conn:
        await apply_migrations(conn, pathlib.Path("/app/migrations"))

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.update.middleware(DbMiddleware(pool))

    dp.include_router(start_router)
    dp.include_router(seller_shops_router)
    dp.include_router(seller_campaigns_router)
    dp.include_router(payments_router)
    dp.include_router(admin_shops_router)

    logger.info("Bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
