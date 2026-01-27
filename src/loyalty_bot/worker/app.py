from __future__ import annotations

import asyncio
import logging
import pathlib

from aiogram import Bot

from loyalty_bot.config import settings
from loyalty_bot.db.migrations import apply_migrations
from loyalty_bot.db.pool import create_pool
from loyalty_bot.logging_setup import setup_logging


logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging(settings.log_level)

    pool = await create_pool(settings.database_dsn)
    async with pool.acquire() as conn:
        await apply_migrations(conn, pathlib.Path("/app/migrations"))

    bot = Bot(token=settings.bot_token)

    logger.info("Worker started (stub). No sending logic yet.")
    try:
        while True:
            await asyncio.sleep(5)
    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
