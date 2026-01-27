from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

import asyncpg
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class DbMiddleware(BaseMiddleware):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["pool"] = self._pool
        return await handler(event, data)
