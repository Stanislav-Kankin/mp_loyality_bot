from __future__ import annotations

import logging
import math

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from superadmin_bot.config import load_settings
from superadmin_bot.db import (
    ALIVE_WINDOW_MINUTES,
    create_pool,
    ensure_schema,
    get_instance,
    get_period_metrics,
    list_instances,
)

logger = logging.getLogger(__name__)


def _mode_label(mode: str) -> str:
    return {
        "all": "все",
        "brand": "бренд",
        "demo": "демо",
    }.get(mode, mode)


def _status_label(status: str) -> str:
    return {
        "all": "любые",
        "alive": "живые",
        "dead": "мёртвые",
    }.get(status, status)


async def _safe_edit_text(message: Message, text: str, reply_markup=None) -> None:
    """Telegram may throw `message is not modified` when user clicks same filter again."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


def _fmt_ts(ts) -> str:
    if ts is None:
        return "—"
    # asyncpg returns datetime with tz
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _period_label(period: str) -> str:
    return {
        "today": "сегодня",
        "7d": "7 дней",
        "all": "всё время",
    }.get(period, period)


def _section_label(section: str) -> str:
    return {
        "campaigns": "📣 Рассылки",
        "customers": "👥 Покупатели",
    }.get(section, section)


def _fmt_metrics(*, r: dict[str, object], section: str, period: str, period_metrics: dict[str, object] | None = None) -> str:
    metrics_at = (period_metrics or {}).get("metrics_at") or r.get("metrics_at")
    if metrics_at is None:
        metrics_at_s = "—"
    else:
        metrics_at_s = _fmt_ts(metrics_at)

    subscribers_active = int(r.get("subscribers_active") or 0)

    # Defaults: today from instance_metrics row
    campaigns_today = int(r.get("campaigns_today") or 0)
    deliveries_sent_today = int(r.get("deliveries_sent_today") or 0)
    deliveries_failed_today = int(r.get("deliveries_failed_today") or 0)
    deliveries_blocked_today = int(r.get("deliveries_blocked_today") or 0)

    if period in {"7d", "all"} and period_metrics is not None:
        campaigns_today = int(period_metrics.get("campaigns_created") or 0)
        deliveries_sent_today = int(period_metrics.get("deliveries_sent") or 0)
        deliveries_failed_today = int(period_metrics.get("deliveries_failed") or 0)
        deliveries_blocked_today = int(period_metrics.get("deliveries_blocked") or 0)

    campaigns_total = int(r.get("campaigns_total") or 0)

    if section == "customers":
        return f"👥 Клиенты\n• активные подписчики: {subscribers_active}\n• метрики: {metrics_at_s}"

    if section == "campaigns":
        period_label = _period_label(period)
        return (
            f"📣 Рассылки ({period_label})\n"
            f"• кампании: всего {campaigns_total}\n"
            f"• создано за период: {campaigns_today}\n"
            f"• доставки: ✅ {deliveries_sent_today} / ❌ {deliveries_failed_today} / 🚫 {deliveries_blocked_today}\n"
            f"• активные подписчики: {subscribers_active}\n"
            f"• метрики: {metrics_at_s}"
        )

    return f"метрики: {metrics_at_s}"
def _instance_status_icon(r) -> str:
    # "alive" if bot or worker was seen recently.
    ts = r.get("bot_last_seen") or r.get("worker_last_seen")
    if ts is None:
        return "🔴"
    # heuristic: if either bot or worker updated in last window
    try:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=ALIVE_WINDOW_MINUTES)
        bot_ts = r.get("bot_last_seen")
        worker_ts = r.get("worker_last_seen")
        if (bot_ts and bot_ts >= cutoff) or (worker_ts and worker_ts >= cutoff):
            return "🟢"
    except Exception:
        pass
    return "🔴"


def _build_instances_kb(rows, *, mode: str, status: str, page: int, pages: int):
    kb = InlineKeyboardBuilder()

    # Filters (2 rows)
    kb.button(text="Все", callback_data=f"inst:list:all:{status}:1")
    kb.button(text="Бренд", callback_data=f"inst:list:brand:{status}:1")
    kb.button(text="Демо", callback_data=f"inst:list:demo:{status}:1")
    kb.adjust(3)

    kb.button(text="Живые", callback_data=f"inst:list:{mode}:alive:1")
    kb.button(text="Мёртвые", callback_data=f"inst:list:{mode}:dead:1")
    kb.button(text="Любые", callback_data=f"inst:list:{mode}:all:1")
    kb.adjust(3)

    # Instances list
    for r in rows:
        icon = _instance_status_icon(r)
        name = r["instance_name"]
        m = _mode_label(r["mode"])
        kb.button(text=f"{icon} {name} ({m})", callback_data=f"inst:open:{r['instance_id']}:campaigns:today:{mode}:{status}:{page}")
        kb.adjust(1)

    # Pagination
    if pages > 1:
        prev_page = max(1, page - 1)
        next_page = min(pages, page + 1)
        kb.button(text="◀️", callback_data=f"inst:list:{mode}:{status}:{prev_page}")
        kb.button(text=f"{page}/{pages}", callback_data="noop")
        kb.button(text="▶️", callback_data=f"inst:list:{mode}:{status}:{next_page}")
        kb.adjust(3)

    return kb.as_markup()


def _build_instance_card_kb(*, instance_id: str, mode: str, status: str, page: int, section: str, period: str):
    kb = InlineKeyboardBuilder()

    # period switches
    kb.button(text="Сегодня", callback_data=f"inst:open:{instance_id}:{section}:today:{mode}:{status}:{page}")
    kb.button(text="7 дней", callback_data=f"inst:open:{instance_id}:{section}:7d:{mode}:{status}:{page}")
    kb.button(text="Всё", callback_data=f"inst:open:{instance_id}:{section}:all:{mode}:{status}:{page}")
    kb.adjust(3)

    # section switches
    kb.button(text="📣 Рассылки", callback_data=f"inst:open:{instance_id}:campaigns:{period}:{mode}:{status}:{page}")
    kb.button(text="👥 Покупатели", callback_data=f"inst:open:{instance_id}:customers:{period}:{mode}:{status}:{page}")
    kb.adjust(2)

    # navigation
    kb.button(text="⬅️ К списку", callback_data=f"inst:list:{mode}:{status}:{page}")
    kb.button(text="🔄 Обновить", callback_data=f"inst:open:{instance_id}:{section}:{period}:{mode}:{status}:{page}")
    kb.adjust(2)

    return kb.as_markup()


async def _render_instances(target, pool, *, mode: str, status: str, page: int, page_size: int = 12):
    page = max(1, int(page))
    offset = (page - 1) * page_size
    rows, total = await list_instances(pool, mode=mode, status=status, limit=page_size, offset=offset)
    pages = max(1, int(math.ceil((total or 0) / page_size)))
    if page > pages:
        page = pages
        offset = (page - 1) * page_size
        rows, total = await list_instances(pool, mode=mode, status=status, limit=page_size, offset=offset)

    text = (
        "📦 Инстансы\n"
        f"Фильтры: режим={_mode_label(mode)}, статус={_status_label(status)}  | alive окно: {ALIVE_WINDOW_MINUTES}м\n"
        "🟢 живой / 🔴 нет сигнала\n"
        f"Страница: {page}/{pages}"
    )
    kb = _build_instances_kb(rows, mode=mode, status=status, page=page, pages=pages)

    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        await _safe_edit_text(target.message, text, reply_markup=kb)


async def _render_instance_card(
    cb: CallbackQuery,
    pool,
    *,
    instance_id: str,
    mode: str,
    status: str,
    page: int,
    section: str,
    period: str,
):
    r = await get_instance(pool, instance_id)
    if not r:
        await cb.answer("Инстанс не найден", show_alert=True)
        return

    period_metrics: dict[str, object] | None = None
    if period in {"7d", "all"}:
        period_metrics = await get_period_metrics(pool, instance_id=instance_id, period=period)

    icon = _instance_status_icon(r)
    text = (
        f"{icon} {r['instance_name']} ({_mode_label(r['mode'])})\n"
        f"ID: {r['instance_id']}\n"
        f"⏱ bot: {_fmt_ts(r['bot_last_seen'])}\n"
        f"⏱ worker: {_fmt_ts(r['worker_last_seen'])}\n\n"
        f"{_fmt_metrics(r=r, section=section, period=period)}"
    )

    await _safe_edit_text(
        cb.message,
        text,
        reply_markup=_build_instance_card_kb(
            instance_id=instance_id,
            mode=mode,
            status=status,
            page=page,
            section=section,
            period=period,
        ),
    )

async def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    bot = Bot(settings.bot_token)
    dp = Dispatcher()

    pool = await create_pool(settings.central_database_dsn)
    await ensure_schema(pool)

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in settings.superadmin_ids:
            return
        await message.answer(
            "🛡️ SuperAdmin Control Center\n\n"
            "Команды:\n"
            "/instances — список инстансов"
        )

    @dp.message(Command("instances"))
    async def instances_cmd(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in settings.superadmin_ids:
            return
        await _render_instances(message, pool, mode="all", status="all", page=1)

    @dp.callback_query(F.data.startswith("inst:list:"))
    async def instances_list_cb(cb: CallbackQuery) -> None:
        if cb.from_user is None or cb.from_user.id not in settings.superadmin_ids:
            await cb.answer()
            return
        try:
            _, _, mode, status, page_s = cb.data.split(":", 4)
            page = int(page_s)
        except Exception:
            await cb.answer("Некорректная кнопка", show_alert=True)
            return
        await cb.answer()
        await _render_instances(cb, pool, mode=mode, status=status, page=page)

    @dp.callback_query(F.data.startswith("inst:open:"))
    async def instance_open_cb(cb: CallbackQuery) -> None:
        if cb.from_user is None or cb.from_user.id not in settings.superadmin_ids:
            await cb.answer()
            return
        try:
            _, _, instance_id, section, period, mode, status, page_s = cb.data.split(":", 7)
            page = int(page_s)
        except Exception:
            await cb.answer("Некорректная кнопка", show_alert=True)
            return
        await cb.answer()
        await _render_instance_card(cb, pool, instance_id=instance_id, mode=mode, status=status, page=page, section=section, period=period)

    @dp.callback_query(F.data == "noop")
    async def noop_cb(cb: CallbackQuery) -> None:
        await cb.answer()

    try:
        logger.info("SuperAdmin bot started")
        await dp.start_polling(bot)
    finally:
        await pool.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())