import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from keyboards.settings_inline import get_settings_menu_keyboard, MESSAGES
from database.repository import get_or_create_settings
from config import ADMIN_ID

logger = logging.getLogger(__name__)
settings_router = Router(name="settings")


@settings_router.message(F.text == "📋 Меню")
async def open_main_menu(message: Message, session: AsyncSession):
    settings = await get_or_create_settings(session, message.from_user.id)
    await message.answer(
        MESSAGES["settings_header"], parse_mode="HTML",
        reply_markup=get_settings_menu_keyboard(message.from_user.id == ADMIN_ID),
    )


@settings_router.callback_query(F.data == "back_settings")
async def back_to_settings(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    settings = await get_or_create_settings(session, callback.from_user.id)
    await callback.message.edit_text(
        MESSAGES["settings_header"], parse_mode="HTML",
        reply_markup=get_settings_menu_keyboard(callback.from_user.id == ADMIN_ID),
    )
    await callback.answer()


@settings_router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(MESSAGES["cancel_action"])
    await callback.answer()


@settings_router.callback_query(F.data == "settings_hide")
async def hide_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()

