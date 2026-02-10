from __future__ import annotations

import asyncio
import logging
import pathlib
import time

from aiogram import Bot, Dispatcher

from loyalty_bot.config import settings
from loyalty_bot.db.migrations import apply_migrations
from loyalty_bot.db.pool import create_pool
from loyalty_bot.logging_setup import setup_logging
from loyalty_bot.bot.middlewares.db import DbMiddleware
from loyalty_bot.bot.routers.start import router as start_router
from loyalty_bot.bot.routers.trial_reminders import router as trial_reminders_router
from loyalty_bot.bot.routers.seller_shops import router as seller_shops_router
from loyalty_bot.bot.routers.seller_campaigns import router as seller_campaigns_router
from loyalty_bot.bot.routers.payments import router as payments_router
from loyalty_bot.bot.routers.admin_shops import router as admin_shops_router
from loyalty_bot.bot.routers.admin_panel import router as admin_panel_router
from loyalty_bot.bot.routers.fallback import router as fallback_router
from loyalty_bot.metrics.central import create_central_pool, push_heartbeat


logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging(level=settings.log_level, service_name="bot", log_dir=settings.log_dir)

    pool = await create_pool(settings.database_dsn)
    async with pool.acquire() as conn:
        await apply_migrations(conn, pathlib.Path("/app/migrations"))

    bot = Bot(token=settings.bot_token)

    central_pool = await create_central_pool()
    hb_task: asyncio.Task[None] | None = None
    if central_pool is not None:
        async def _hb_loop() -> None:
            last = 0.0
            while True:
                now_m = time.monotonic()
                if now_m - last >= float(getattr(settings, "metrics_push_interval_seconds", 60)):
                    try:
                        await push_heartbeat(central_pool, service="bot")
                    except Exception:
                        logger.exception("failed to push bot heartbeat")
                    last = now_m
                await asyncio.sleep(1.0)

        hb_task = asyncio.create_task(_hb_loop())
    dp = Dispatcher()
    dp.update.middleware(DbMiddleware(pool, central_pool=central_pool))

    dp.include_router(start_router)
    dp.include_router(trial_reminders_router)
    dp.include_router(seller_shops_router)
    dp.include_router(seller_campaigns_router)
    dp.include_router(payments_router)
    dp.include_router(admin_panel_router)
    dp.include_router(admin_shops_router)
    dp.include_router(fallback_router)

    logger.info("Bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except Exception:
                pass
        await bot.session.close()
        await pool.close()
        if central_pool is not None:
            await central_pool.close()


if __name__ == "__main__":
    asyncio.run(main())
