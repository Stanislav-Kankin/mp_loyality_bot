from __future__ import annotations

import asyncio
import logging
import pathlib

import asyncpg
from aiogram import Bot
from aiogram.exceptions import (
    TelegramRetryAfter,
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
    TelegramAPIError,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loyalty_bot.config import settings
from loyalty_bot.db.migrations import apply_migrations
from loyalty_bot.db.pool import create_pool
from loyalty_bot.db.repo import (
    lease_due_deliveries,
    mark_delivery_sent,
    mark_delivery_blocked,
    mark_delivery_failed,
    reschedule_delivery,
    finalize_completed_campaigns,
)
from loyalty_bot.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def _calc_backoff_seconds(attempt: int) -> int:
    """Exponential backoff: base * 2^(attempt-1), capped."""
    a = max(1, int(attempt))
    seconds = int(settings.retry_base_seconds) * (2 ** max(0, a - 1))
    seconds = max(int(settings.retry_base_seconds), seconds)
    seconds = min(int(settings.retry_max_seconds), seconds)
    return max(1, seconds)


def _build_campaign_kb(*, campaign_id: int, button_title: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    title = (button_title or "").strip() or "Открыть ссылку"
    kb.button(text=title, callback_data=f"campaign:click:{campaign_id}")
    kb.adjust(1)
    return kb


async def _process_delivery(bot: Bot, pool: asyncpg.Pool, item: dict) -> None:
    delivery_id = int(item["delivery_id"])
    campaign_id = int(item["campaign_id"])
    tg_user_id = int(item["tg_user_id"])
    text = str(item.get("text") or "")
    button_title = str(item.get("button_title") or "")
    attempt = int(item.get("attempt") or 1)

    try:
        msg = await bot.send_message(
            chat_id=tg_user_id,
            text=text,
            reply_markup=_build_campaign_kb(campaign_id=campaign_id, button_title=button_title).as_markup(),
            disable_web_page_preview=True,
        )
        await mark_delivery_sent(pool, delivery_id=delivery_id, campaign_id=campaign_id, tg_message_id=int(msg.message_id))
        return

    except TelegramRetryAfter as e:
        delay = max(1, int(getattr(e, "retry_after", 1)))
        await reschedule_delivery(pool, delivery_id=delivery_id, next_attempt_in_seconds=delay, last_error=f"retry_after:{delay}")
        return

    except TelegramForbiddenError:
        await mark_delivery_blocked(pool, delivery_id=delivery_id, campaign_id=campaign_id, last_error="forbidden")
        return

    except TelegramBadRequest as e:
        # Typical cases: chat not found / user deactivated / can't message.
        err = str(e)
        await mark_delivery_failed(pool, delivery_id=delivery_id, campaign_id=campaign_id, last_error=f"bad_request:{err}")
        return

    except (TelegramNetworkError, TelegramServerError, TelegramAPIError) as e:
        delay = _calc_backoff_seconds(attempt)
        await reschedule_delivery(pool, delivery_id=delivery_id, next_attempt_in_seconds=delay, last_error=f"api_error:{e}")
        return

    except Exception as e:  # noqa: BLE001
        delay = _calc_backoff_seconds(attempt)
        await reschedule_delivery(pool, delivery_id=delivery_id, next_attempt_in_seconds=delay, last_error=f"unexpected:{e}")
        return


async def main() -> None:
    setup_logging(settings.log_level)

    pool: asyncpg.Pool = await create_pool(settings.database_dsn)
    async with pool.acquire() as conn:
        await apply_migrations(conn, pathlib.Path("/app/migrations"))

    bot = Bot(token=settings.bot_token)

    # Simple global rate limiter: minimum delay between messages.
    rate = max(1, int(settings.tg_global_rate_per_sec))
    min_delay = 1.0 / float(rate)

    logger.info(
        "Worker started. batch=%s tick=%ss rate=%s/s",
        settings.send_batch_size,
        settings.send_tick_seconds,
        rate,
    )

    try:
        while True:
            items = await lease_due_deliveries(pool, batch_size=int(settings.send_batch_size))
            if not items:
                # Still try to finalize campaigns periodically.
                await finalize_completed_campaigns(pool)
                await asyncio.sleep(float(settings.send_tick_seconds))
                continue

            for item in items:
                await _process_delivery(bot, pool, item)
                await asyncio.sleep(min_delay)

            await finalize_completed_campaigns(pool)

    finally:
        await bot.session.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
