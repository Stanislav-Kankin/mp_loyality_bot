from __future__ import annotations

import asyncio
import logging
import pathlib
import time

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
    get_shop_audience_counts,
    finalize_completed_campaigns,
    list_unnotified_completed_campaigns,
    mark_campaign_completed_notified,
    list_due_trial_day5_reminders,
    list_due_trial_day7_reminders,
    mark_trial_day5_notified,
    mark_trial_day7_notified,
)
from loyalty_bot.logging_setup import setup_logging
from loyalty_bot.metrics.central import create_central_pool, push_heartbeat, push_instance_metrics

logger = logging.getLogger(__name__)


def _calc_backoff_seconds(attempt: int) -> int:
    """Exponential backoff: base * 2^(attempt-1), capped."""
    a = max(1, int(attempt))
    seconds = int(settings.retry_base_seconds) * (2 ** max(0, a - 1))
    seconds = max(int(settings.retry_base_seconds), seconds)
    seconds = min(int(settings.retry_max_seconds), seconds)
    return max(1, seconds)


def _build_campaign_kb(*, url: str, button_title: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    title = (button_title or "").strip() or "Открыть ссылку"
    kb.button(text=title, url=(url or ""))
    kb.adjust(1)
    return kb


def _build_trial_day5_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Хочу такого бота", callback_data="trial:day5:want")
    kb.button(text="⏳ Пока ещё смотрю", callback_data="trial:day5:later")
    kb.adjust(1)
    return kb


def _build_trial_day7_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Хочу такого бота", callback_data="trial:day7:want")
    kb.button(text="🚫 Нет, спасибо", callback_data="trial:day7:no")
    kb.adjust(1)
    return kb



def _format_delivery_text(*, shop_name: str, text: str) -> str:
    sn = (shop_name or "").strip()
    if not sn:
        return text or ""
    prefix = f"🏷 Магазин: {sn}\n\n"
    return prefix + (text or "")


async def _process_delivery(bot: Bot, pool: asyncpg.Pool, item: dict) -> None:
    delivery_id = int(item["delivery_id"])
    campaign_id = int(item["campaign_id"])
    tg_user_id = int(item["tg_user_id"])
    shop_name = str(item.get("shop_name") or "").strip()
    text = str(item.get("text") or "")
    button_title = str(item.get("button_title") or "")
    url = str(item.get("url") or "")
    photo_file_id = item.get("photo_file_id")
    attempt = int(item.get("attempt") or 1)

    try:
        formatted = _format_delivery_text(shop_name=shop_name, text=text)
        if photo_file_id:
            msg = await bot.send_photo(
                chat_id=tg_user_id,
                photo=str(photo_file_id),
                caption=formatted[:1024] if formatted else None,
                reply_markup=_build_campaign_kb(url=url, button_title=button_title).as_markup(),
            )
            if formatted and len(formatted) > 1024:
                await bot.send_message(chat_id=tg_user_id, text=formatted[1024:], disable_web_page_preview=True)
        else:
            msg = await bot.send_message(
                chat_id=tg_user_id,
                text=formatted,
                reply_markup=_build_campaign_kb(url=url, button_title=button_title).as_markup(),
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


async def _notify_completed_campaigns(bot: Bot, pool: asyncpg.Pool) -> None:
    items = await list_unnotified_completed_campaigns(pool, limit=50)
    for it in items:
        campaign_id = int(it["campaign_id"])
        shop_id = int(it["shop_id"])

        # Audience stats for the shop (total/subscribed/unsubscribed).
        audience = await get_shop_audience_counts(pool, shop_id)
        total_recipients = int(it.get("total_recipients") or 0)
        sent_count = int(it.get("sent_count") or 0)
        failed_count = int(it.get("failed_count") or 0)
        blocked_count = int(it.get("blocked_count") or 0)
        not_delivered = max(0, total_recipients - sent_count - failed_count - blocked_count)

        text = (
            f"✅ Рассылка №{campaign_id} завершена\n\n"
            f"👥 Получателей в рассылке: {total_recipients}\n"
            f"✅ Доставлено: {sent_count}\n"
            f"❌ Ошибки: {failed_count}\n"
            f"⛔ Заблокировали: {blocked_count}\n"
            f"📭 Не доставлено: {not_delivered}\n\n"
            f"📦 База магазина: {it.get('shop_name','')}\n"
            f"— всего записей: {int(audience.get('total', 0))}\n"
            f"— активные (подписаны): {int(audience.get('subscribed', 0))}\n"
            f"— отписанные: {int(audience.get('unsubscribed', 0))}"
        )

        try:
            await bot.send_message(int(it["seller_tg_user_id"]), text)
            await mark_campaign_completed_notified(pool, campaign_id=campaign_id)
            logger.info("campaign completed notified campaign_id=%s seller_tg=%s", campaign_id, it["seller_tg_user_id"])
        except Exception:
            logger.exception("failed to notify seller for completed campaign_id=%s", campaign_id)


async def _notify_trial_reminders(bot: Bot, pool: asyncpg.Pool) -> None:
    """Send DEMO reminders on day 5 and day 7 (once)."""
    if getattr(settings, "bot_mode", "demo") != "demo":
        return

    # Day 5
    day5 = await list_due_trial_day5_reminders(pool, limit=50)
    for it in day5:
        tg_user_id = int(it["tg_user_id"])
        try:
            await bot.send_message(
                tg_user_id,
                "⏰ Уже 5-й день демо. Хотите такого персонального бота?",
                reply_markup=_build_trial_day5_kb().as_markup(),
            )
            await mark_trial_day5_notified(pool, tg_user_id=tg_user_id)
            logger.info("trial day5 notified tg_id=%s", tg_user_id)
        except Exception:
            logger.exception("failed to send trial day5 reminder tg_id=%s", tg_user_id)

    # Day 7
    day7 = await list_due_trial_day7_reminders(pool, limit=50)
    for it in day7:
        tg_user_id = int(it["tg_user_id"])
        try:
            await bot.send_message(
                tg_user_id,
                "⏰ Демо закончилось. Хотите подключить персонального бота?",
                reply_markup=_build_trial_day7_kb().as_markup(),
            )
            await mark_trial_day7_notified(pool, tg_user_id=tg_user_id)
            logger.info("trial day7 notified tg_id=%s", tg_user_id)
        except Exception:
            logger.exception("failed to send trial day7 reminder tg_id=%s", tg_user_id)

async def main() -> None:
    setup_logging(level=settings.log_level, service_name="worker", log_dir=settings.log_dir)

    pool: asyncpg.Pool = await create_pool(settings.database_dsn)
    async with pool.acquire() as conn:
        await apply_migrations(conn, pathlib.Path("/app/migrations"))

    bot = Bot(token=settings.bot_token)

    central_pool = await create_central_pool()
    last_hb = 0.0

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
            # Heartbeat to SuperAdmin central DB (optional).
            if central_pool is not None:
                now_m = time.monotonic()
                if now_m - last_hb >= float(getattr(settings, "metrics_push_interval_seconds", 60)):
                    try:
                        await push_heartbeat(central_pool, service="worker")

                        # Aggregated metrics snapshot (no PII). Safe to fail silently.
                        async with pool.acquire() as conn:
                            row = await conn.fetchrow(
                                """
                                SELECT
                                    (SELECT COUNT(*) FROM campaigns) AS campaigns_total,
                                    (SELECT COUNT(*) FROM campaigns WHERE created_at >= date_trunc('day', now())) AS campaigns_today,
                                    (SELECT COUNT(*) FROM campaign_deliveries WHERE status = 'sent' AND sent_at >= date_trunc('day', now())) AS deliveries_sent_today,
                                    (SELECT COUNT(*) FROM campaign_deliveries WHERE status = 'failed' AND next_attempt_at >= date_trunc('day', now())) AS deliveries_failed_today,
                                    (SELECT COUNT(*) FROM campaign_deliveries WHERE status = 'blocked' AND next_attempt_at >= date_trunc('day', now())) AS deliveries_blocked_today,
                                    (SELECT COUNT(*) FROM shop_customers WHERE status = 'subscribed') AS subscribers_active;
                                """
                            )
                        if row is not None:
                            await push_instance_metrics(
                                central_pool,
                                campaigns_total=int(row["campaigns_total"] or 0),
                                campaigns_today=int(row["campaigns_today"] or 0),
                                deliveries_sent_today=int(row["deliveries_sent_today"] or 0),
                                deliveries_failed_today=int(row["deliveries_failed_today"] or 0),
                                deliveries_blocked_today=int(row["deliveries_blocked_today"] or 0),
                                subscribers_active=int(row["subscribers_active"] or 0),
                            )
                    except Exception:
                        logger.exception("failed to push worker heartbeat")
                    last_hb = now_m

            items = await lease_due_deliveries(pool, batch_size=int(settings.send_batch_size))
            if not items:
                # Still try to finalize campaigns periodically.
                await finalize_completed_campaigns(pool)
                try:
                    await _notify_completed_campaigns(bot, pool)
                    await _notify_trial_reminders(bot, pool)
                except Exception:
                    logger.exception('notify_completed_campaigns failed (will retry later)')
                await asyncio.sleep(float(settings.send_tick_seconds))
                continue

            for item in items:
                await _process_delivery(bot, pool, item)
                await asyncio.sleep(min_delay)

            await finalize_completed_campaigns(pool)
            await _notify_completed_campaigns(bot, pool)
            await _notify_trial_reminders(bot, pool)

    finally:
        await bot.session.close()
        await pool.close()
        if central_pool is not None:
            await central_pool.close()
        if central_pool is not None:
            await central_pool.close()


if __name__ == "__main__":
    asyncio.run(main())