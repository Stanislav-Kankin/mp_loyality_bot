from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "MVP Loyalty Bot: каркас поднят. Следующий шаг — онбординг селлера/покупателя и payload."
    )
