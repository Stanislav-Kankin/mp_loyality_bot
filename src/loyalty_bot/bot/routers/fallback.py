from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data)
async def unknown_callback(cb: CallbackQuery) -> None:
    # Catch-all for unexpected callback_data to avoid silent "not handled" cases.
    data = cb.data or ""
    user = cb.from_user.id if cb.from_user else None
    logger.warning("Unhandled callback_query: user=%s data=%r", user, data)
    await cb.answer("Нет доступа или устаревшая кнопка", show_alert=True)
