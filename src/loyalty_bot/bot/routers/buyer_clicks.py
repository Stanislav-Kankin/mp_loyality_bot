from __future__ import annotations

import asyncpg
from aiogram import F, Router
from aiogram.types import CallbackQuery

from loyalty_bot.db.repo import ensure_customer, record_campaign_click, get_campaign_url

router = Router()


def _parse_campaign_id(data: str) -> int | None:
    # callback format: "campaign:click:<id>"
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    if parts[0] != "campaign" or parts[1] != "click":
        return None
    raw = parts[2]
    if not raw.isdigit():
        return None
    return int(raw)


@router.callback_query(F.data.startswith("campaign:click:"))
async def campaign_click(cb: CallbackQuery, pool: asyncpg.Pool) -> None:
    tg_id = cb.from_user.id
    campaign_id = _parse_campaign_id(cb.data or "")
    if campaign_id is None:
        await cb.answer("Некорректная кнопка", show_alert=True)
        return

    # Ensure customer exists
    customer_id = await ensure_customer(pool, tg_id)

    # Count unique click (insert into clicks if not exists)
    try:
        inserted = await record_campaign_click(pool, campaign_id=campaign_id, customer_tg_user_id=tg_id)
    except Exception:
        inserted = False

    url = await get_campaign_url(pool, campaign_id=campaign_id)
    if not url:
        await cb.answer("Ссылка не найдена", show_alert=True)
        return

    await cb.answer("Открываю ссылку…")
    if inserted:
        await cb.message.answer(url)
    else:
        # still send URL, but do not increment click counter
        await cb.message.answer(url)
