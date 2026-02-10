from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

import asyncpg
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class DbMiddleware(BaseMiddleware):
    def __init__(self, pool: asyncpg.Pool, *, central_pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool
        self._central_pool = central_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["pool"] = self._pool
        data["central_pool"] = self._central_pool
        return await handler(event, data)
