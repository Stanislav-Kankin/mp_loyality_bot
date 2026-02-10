from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from loyalty_bot.bot.middlewares.db import DbMiddleware
from loyalty_bot.db.pool import create_pool
from loyalty_bot.logging_setup import setup_logging
from payment_hub_bot.config import hub_settings
from payment_hub_bot.routers.payments import router as payments_router


logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging(level=hub_settings.log_level, service_name="payment_hub", log_dir=hub_settings.log_dir)

    pool = await create_pool(hub_settings.central_database_dsn)
    bot = Bot(token=hub_settings.bot_token)
    dp = Dispatcher()
    dp.update.middleware(DbMiddleware(pool))

    dp.include_router(payments_router)

    logger.info("Payment hub started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
