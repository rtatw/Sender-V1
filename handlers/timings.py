import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from keyboards.main_reply import get_main_reply_keyboard
from keyboards.settings_inline import (
    get_timings_keyboard,
    get_cancel_keyboard,
    MESSAGES,
)
from states.forms import TimingState, ReceiveIntervalState
from database.repository import get_or_create_settings
from services.inbox_watcher import get_watcher
from services.state import global_state

logger = logging.getLogger(__name__)

timings_router = Router(name="timings")


@timings_router.callback_query(F.data == "settings_timings")
async def timings_menu(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    await callback.message.edit_text(
        MESSAGES["timings_current"].format(
            min=settings.timing_min,
            max=settings.timing_max,
        ) + f"\n📥 Проверка входящих: каждые <b>{settings.receive_check_interval} сек</b>",
        parse_mode="HTML",
        reply_markup=get_timings_keyboard(settings.timing_min, settings.timing_max),
    )
    await callback.answer()


@timings_router.callback_query(F.data == "timing_edit")
async def timing_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TimingState.waiting_for_interval)
    await callback.message.edit_text(
        MESSAGES["timing_edit"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@timings_router.message(TimingState.waiting_for_interval, F.text)
async def process_timing(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    parts = text.split()
    if len(parts) != 2:
        await message.answer(MESSAGES["timing_invalid"])
        return

    try:
        mn = int(parts[0])
        mx = int(parts[1])
    except ValueError:
        await message.answer(MESSAGES["timing_invalid"])
        return

    if mn < 1 or mx < 1 or mn > mx:
        await message.answer("❌ Мин должен быть >= 1 и <= Макс.")
        return

    settings = await get_or_create_settings(session, message.from_user.id)
    settings.timing_min = mn
    settings.timing_max = mx
    await session.commit()

    await state.clear()
    await message.answer(
        MESSAGES["timing_updated"].format(min=mn, max=mx),
        reply_markup=get_main_reply_keyboard(),
    )


@timings_router.callback_query(F.data == "timing_reset")
async def timing_reset(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    settings.timing_min = 5
    settings.timing_max = 15
    await session.commit()

    await callback.message.edit_text(
        MESSAGES["timings_current"].format(min=5, max=15),
        parse_mode="HTML",
        reply_markup=get_timings_keyboard(5, 15),
    )
    await callback.answer(MESSAGES["timing_reset"])


# --- Receive Check Interval ---

@timings_router.callback_query(F.data == "receive_interval_set")
async def receive_interval_set(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReceiveIntervalState.waiting_for_interval)
    await callback.message.edit_text(
        "Введите интервал проверки входящих в секундах (10-3600):\nНапример: <code>30</code>",
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@timings_router.message(ReceiveIntervalState.waiting_for_interval, F.text)
async def process_receive_interval(message: Message, state: FSMContext, session: AsyncSession):
    try:
        interval = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число от 1 до 60.")
        return

    if interval < 10 or interval > 3600:
        await message.answer("❌ Интервал должен быть от 10 до 3600 секунд.")
        return

    settings = await get_or_create_settings(session, message.from_user.id)
    settings.receive_check_interval = interval
    await session.commit()
    await state.clear()

    # Restart watcher with new interval
    bot_ref = global_state.bot
    if bot_ref:
        watcher = get_watcher(bot_ref)
        watcher.stop_for_user(message.from_user.id)
        await watcher.start_for_user(message.from_user.id, message.chat.id)
    else:
        logger.error("Cannot restart watcher: bot is None in global_state")

    await message.answer(
        f"✅ Интервал проверки входящих: <b>{interval} сек</b>",
        parse_mode="HTML",
        reply_markup=get_main_reply_keyboard(),
    )

