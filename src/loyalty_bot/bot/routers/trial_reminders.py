from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from loyalty_bot.config import settings
from loyalty_bot.db import repo

logger = logging.getLogger(__name__)

router = Router()


class TrialFeedback(StatesGroup):
    waiting_text = State()


def _admins() -> list[int]:
    # settings.telegram_admin_ids is expected to be list[int]
    try:
        return [int(x) for x in (settings.telegram_admin_ids or [])]
    except Exception:
        return []


async def _notify_admins_about_lead(*, bot, tg_user_id: int, username: str | None, text: str) -> None:
    for admin_id in _admins():
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.exception("failed to notify admin_id=%s", admin_id)


@router.callback_query(F.data == "trial:day5:want")
async def trial_day5_want(call: CallbackQuery) -> None:
    if getattr(settings, "bot_mode", "demo") != "demo":
        await call.answer("Нет доступа")
        return
    u = call.from_user
    username = f"@{u.username}" if u and u.username else "(no username)"
    await call.answer("Ок")
    await call.message.answer(
        "✅ Отлично! Напишите нам вне бота, чтобы мы запустили для вас персонального бота.\n"
        "Токен в боте не запрашиваем (создадите в BotFather и передадите вне бота)."
    )
    await _notify_admins_about_lead(
        bot=call.bot,
        tg_user_id=u.id,
        username=u.username,
        text=f"🟩 Лид (day5): tg_user_id={u.id} {username}",
    )


@router.callback_query(F.data == "trial:day5:later")
async def trial_day5_later(call: CallbackQuery) -> None:
    if getattr(settings, "bot_mode", "demo") != "demo":
        await call.answer("Нет доступа")
        return
    await call.answer("Ок")
    await call.message.answer("⏳ Хорошо, продолжайте тестировать. Я напомню ближе к окончанию демо.")


@router.callback_query(F.data == "trial:day7:want")
async def trial_day7_want(call: CallbackQuery) -> None:
    if getattr(settings, "bot_mode", "demo") != "demo":
        await call.answer("Нет доступа")
        return
    u = call.from_user
    username = f"@{u.username}" if u and u.username else "(no username)"
    await call.answer("Ок")
    await call.message.answer(
        "✅ Заявка принята. Мы свяжемся с вами вне бота.\n"
        "Токен в боте не запрашиваем."
    )
    await _notify_admins_about_lead(
        bot=call.bot,
        tg_user_id=u.id,
        username=u.username,
        text=f"🟩 Лид (day7): tg_user_id={u.id} {username}",
    )


@router.callback_query(F.data == "trial:day7:no")
async def trial_day7_no(call: CallbackQuery, state: FSMContext) -> None:
    if getattr(settings, "bot_mode", "demo") != "demo":
        await call.answer("Нет доступа")
        return
    await call.answer("Ок")
    await state.set_state(TrialFeedback.waiting_text)
    await call.message.answer("🚫 Понял. Напишите, пожалуйста, коротко причину (в свободной форме):")


@router.message(TrialFeedback.waiting_text)
async def trial_feedback_text(message: Message, state: FSMContext, pool) -> None:
    if getattr(settings, "bot_mode", "demo") != "demo":
        await message.answer("Нет доступа")
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напишите текстом, пожалуйста.")
        return

    u = message.from_user
    username = f"@{u.username}" if u and u.username else "(no username)"

    try:
        await repo.save_trial_feedback(pool, tg_user_id=u.id, text=text)
    except Exception:
        logger.exception("failed to save trial feedback")

    await _notify_admins_about_lead(
        bot=message.bot,
        tg_user_id=u.id,
        username=u.username,
        text=f"🟥 Отказ (day7): tg_user_id={u.id} {username}\nПричина: {text}",
    )

    await state.clear()
    await message.answer("Спасибо! Мы учтём обратную связь.")
