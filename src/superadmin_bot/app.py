from __future__ import annotations

import logging
import math

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from superadmin_bot.config import load_settings
from superadmin_bot.db import ALIVE_WINDOW_MINUTES, create_pool, ensure_schema, get_instance, list_instances

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


def _fmt_metrics(r) -> str:
    if r.get("metrics_at") is None:
        return "метрики: —"
    return (
        f"метрики: {_fmt_ts(r['metrics_at'])}\n"
        f"кампании: {int(r['campaigns_total'] or 0)} (сегодня {int(r['campaigns_today'] or 0)})\n"
        f"доставки сегодня: ✅ {int(r['deliveries_sent_today'] or 0)} / ❌ {int(r['deliveries_failed_today'] or 0)} / 🚫 {int(r['deliveries_blocked_today'] or 0)}\n"
        f"подписчики активные: {int(r['subscribers_active'] or 0)}"
    )


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
        kb.button(text=f"{icon} {name} ({m})", callback_data=f"inst:open:{r['instance_id']}:{mode}:{status}:{page}")
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


def _build_instance_card_kb(*, instance_id: str, mode: str, status: str, page: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К списку", callback_data=f"inst:list:{mode}:{status}:{page}")
    kb.button(text="🔄 Обновить", callback_data=f"inst:open:{instance_id}:{mode}:{status}:{page}")
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

    header = "📦 Инстансы"
    text = (
        f"{header}\n"
        f"Режим: {_mode_label(mode)} | Статус: {_status_label(status)} | Alive окно: {ALIVE_WINDOW_MINUTES}м\n"
        "🟢 живой / 🔴 нет сигнала\n"
        f"Страница: {page}/{pages}"
    )
    kb = _build_instances_kb(rows, mode=mode, status=status, page=page, pages=pages)

    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        await _safe_edit_text(target.message, text, reply_markup=kb)


async def _render_instance_card(cb: CallbackQuery, pool, *, instance_id: str, mode: str, status: str, page: int):
    r = await get_instance(pool, instance_id)
    if not r:
        await cb.answer("Инстанс не найден", show_alert=True)
        return
    icon = _instance_status_icon(r)
    text = (
        f"{icon} {r['instance_name']} ({_mode_label(r['mode'])})\n"
        f"id: {r['instance_id']}\n"
        f"bot: {_fmt_ts(r['bot_last_seen'])}\n"
        f"worker: {_fmt_ts(r['worker_last_seen'])}\n\n"
        f"{_fmt_metrics(r)}"
    )
    await _safe_edit_text(
        cb.message,
        text,
        reply_markup=_build_instance_card_kb(instance_id=instance_id, mode=mode, status=status, page=page),
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
            _, _, instance_id, mode, status, page_s = cb.data.split(":", 5)
            page = int(page_s)
        except Exception:
            await cb.answer("Некорректная кнопка", show_alert=True)
            return
        await cb.answer()
        await _render_instance_card(cb, pool, instance_id=instance_id, mode=mode, status=status, page=page)

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
