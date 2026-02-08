from __future__ import annotations

import logging
import math

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from superadmin_bot.config import load_settings
from superadmin_bot.db import ALIVE_WINDOW_MINUTES, create_pool, ensure_schema, get_instance, list_instances

logger = logging.getLogger(__name__)


class InstancesStates(StatesGroup):
    waiting_search = State()


def _get_view_defaults() -> dict:
    return {
        "mode": "all",
        "status": "all",
        "page": 1,
        "sort": "seen",  # seen|name
        "query": "",
    }


async def _get_view_state(state: FSMContext | None) -> dict:
    data = _get_view_defaults()
    if state is None:
        return data
    stored = await state.get_data()
    data.update({k: stored.get(f"iv_{k}", v) for k, v in data.items()})
    return data


async def _set_view_state(state: FSMContext | None, **kwargs: object) -> None:
    if state is None:
        return
    payload = {f"iv_{k}": v for k, v in kwargs.items() if v is not None}
    if payload:
        await state.update_data(**payload)


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
    return ts.strftime("%d.%m.%Y %H:%M:%S")


async def _load_view_state(state: FSMContext | None) -> dict:
    d = _get_view_defaults()
    if state is None:
        return d
    data = await state.get_data()
    for k in ("mode", "status", "page", "sort", "query"):
        if k in data and data[k] is not None:
            d[k] = data[k]
    return d


async def _save_view_state(state: FSMContext | None, **kwargs) -> None:
    if state is None:
        return
    cur = await state.get_data()
    payload = {**cur, **{k: v for k, v in kwargs.items() if v is not None}}
    await state.set_data(payload)


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


def _fmt_metrics(*, r, section: str, period: str) -> str:
    """Render metrics block. In SA-5 we only have 'today' counters in DB."""
    if r.get("metrics_at") is None:
        return "метрики: —"

    # Note: central schema currently stores only 'today' counters.
    period_note = ""
    if period != "today":
        period_note = " (пока есть только метрики за сегодня)"

    if section == "customers":
        return (
            f"👥 Покупатели ({_period_label(period)}{period_note})"
            f"• активные подписчики: {int(r['subscribers_active'] or 0)}"
        )

    # default: campaigns
    return (
        f"📣 Рассылки ({_period_label(period)}{period_note})"
        f"• кампании: всего {int(r['campaigns_total'] or 0)}, сегодня {int(r['campaigns_today'] or 0)}"
        f"• доставки: ✅ {int(r['deliveries_sent_today'] or 0)} / ❌ {int(r['deliveries_failed_today'] or 0)} / 🚫 {int(r['deliveries_blocked_today'] or 0)}"
        f"• активные подписчики: {int(r['subscribers_active'] or 0)}"
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


def _build_instances_kb(rows, *, mode: str, status: str, page: int, pages: int, query: str, sort: str):
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

    # Search + sort (1 row)
    sort_label = "свежие" if sort == "seen" else "имя"
    kb.button(text=f"🔎 Поиск" + (" ✅" if query else ""), callback_data="inst:search")
    kb.button(text=f"↕️ Сорт: {sort_label}", callback_data="inst:sort")
    if query:
        kb.button(text="✖️ Сброс", callback_data="inst:clear")
        kb.adjust(3)
    else:
        kb.adjust(2)

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


async def _render_instances(
    target,
    pool,
    *,
    state: FSMContext | None,
    mode: str | None = None,
    status: str | None = None,
    page: int | None = None,
    page_size: int = 12,
):
    view = await _load_view_state(state)
    if mode is not None:
        view["mode"] = mode
    if status is not None:
        view["status"] = status
    if page is not None:
        view["page"] = int(page)

    mode = view["mode"]
    status = view["status"]
    page = max(1, int(view["page"]))
    query = (view.get("query") or "").strip()
    sort = view.get("sort") or "seen"

    await _save_view_state(state, mode=mode, status=status, page=page, query=query, sort=sort)

    offset = (page - 1) * page_size
    rows, total = await list_instances(pool, mode=mode, status=status, query=query or None, sort=sort, limit=page_size, offset=offset)
    pages = max(1, int(math.ceil((total or 0) / page_size)))
    if page > pages:
        page = pages
        offset = (page - 1) * page_size
        rows, total = await list_instances(pool, mode=mode, status=status, query=query or None, sort=sort, limit=page_size, offset=offset)

    text = (
        "📦 Инстансы\n"
        f"Фильтры: режим={_mode_label(mode)}, статус={_status_label(status)}  | сорт={('свежие' if sort=='seen' else 'имя')} | alive окно: {ALIVE_WINDOW_MINUTES}м\n"
        "🟢 живой / 🔴 нет сигнала\n"
        f"Поиск: {(query if query else '—')}\n"
        f"Страница: {page}/{pages}"
    )
    kb = _build_instances_kb(rows, mode=mode, status=status, page=page, pages=pages, query=query, sort=sort)

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
    dp = Dispatcher(storage=MemoryStorage())

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
    async def instances_cmd(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.from_user.id not in settings.superadmin_ids:
            return
        await _render_instances(message, pool, state=state, mode="all", status="all", page=1)

    @dp.callback_query(F.data.startswith("inst:list:"))
    async def instances_list_cb(cb: CallbackQuery, state: FSMContext) -> None:
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
        await _render_instances(cb, pool, state=state, mode=mode, status=status, page=page)

    @dp.callback_query(F.data == "inst:sort")
    async def instances_sort_cb(cb: CallbackQuery, state: FSMContext) -> None:
        if cb.from_user is None or cb.from_user.id not in settings.superadmin_ids:
            await cb.answer()
            return
        view = await _load_view_state(state)
        new_sort = "name" if view.get("sort") == "seen" else "seen"
        await _save_view_state(state, sort=new_sort, page=1)
        await cb.answer("Сортировка: свежие" if new_sort == "seen" else "Сортировка: имя")
        await _render_instances(cb, pool, state=state)

    @dp.callback_query(F.data.in_({"inst:clear", "inst:search:clear"}))
    async def instances_clear_cb(cb: CallbackQuery, state: FSMContext) -> None:
        if cb.from_user is None or cb.from_user.id not in settings.superadmin_ids:
            await cb.answer()
            return
        await _save_view_state(state, query="", page=1)
        await cb.answer("Поиск сброшен")
        await _render_instances(cb, pool, state=state)

    @dp.callback_query(F.data == "inst:search")
    async def instances_search_cb(cb: CallbackQuery, state: FSMContext) -> None:
        if cb.from_user is None or cb.from_user.id not in settings.superadmin_ids:
            await cb.answer()
            return
        await cb.answer()
        await state.set_state(InstancesStates.waiting_search)
        await cb.message.answer(
            "🔎 Введите строку поиска (id или название).\n\n"
            "Чтобы очистить поиск — нажмите \"✖️ Сброс\" в списке инстансов.",
        )

    @dp.message(InstancesStates.waiting_search)
    async def instances_search_text(message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.from_user.id not in settings.superadmin_ids:
            return
        q = (message.text or "").strip()
        # empty text means clear
        await _save_view_state(state, query=q, page=1)
        # keep view settings (filters/query/sort) in data, only exit FSM state
        await state.set_state(None)
        await _render_instances(message, pool, state=state)

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
