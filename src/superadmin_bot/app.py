from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from superadmin_bot.config import load_settings
from superadmin_bot.db import create_pool, ensure_schema, list_instances

logger = logging.getLogger(__name__)


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
        rows = await list_instances(pool)
        if not rows:
            await message.answer("Инстансов пока нет. Ждём первые метрики из клиентских ботов.")
            return
        lines: list[str] = ["📦 Инстансы:"]
        for r in rows:
            lines.append(
                f"\n• {r['instance_name']} ({r['mode']})\n"
                f"  id: {r['instance_id']}\n"
                f"  bot: {_fmt_ts(r['bot_last_seen'])}\n"
                f"  worker: {_fmt_ts(r['worker_last_seen'])}\n"
                f"  {_fmt_metrics(r)}"
            )
        await message.answer("\n".join(lines))

    try:
        logger.info("SuperAdmin bot started")
        await dp.start_polling(bot)
    finally:
        await pool.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
