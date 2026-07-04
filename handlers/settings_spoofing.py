import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from keyboards.main_reply import get_main_reply_keyboard
from keyboards.settings_inline import (
    get_cancel_keyboard, get_back_settings_keyboard, get_spoofing_menu_keyboard,
    get_nick_menu_keyboard, get_sub_theme_menu_keyboard, get_text_theme_menu_keyboard,
    MESSAGES,
)
from states.forms import (
    SpoofingSenderState, SpoofingNickState, SpoofingThemeState, TextThemeState,
)
from database.repository import get_or_create_settings

logger = logging.getLogger(__name__)
spoofing_router = Router(name="spoofing")


@spoofing_router.callback_query(F.data == "settings_spoofing")
async def settings_spoofing(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    current = settings.spoofing_sender or "не задан"
    await callback.message.edit_text(f"🔴 <b>Спуфинг (имя отправителя)</b>\n\nТекущее: <code>{current}</code>",
                                     parse_mode="HTML", reply_markup=get_spoofing_menu_keyboard())
    await callback.answer()


@spoofing_router.callback_query(F.data == "spoofing_set")
async def spoofing_set(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SpoofingSenderState.waiting_for_text)
    await callback.message.edit_text(MESSAGES["spoofing_set"], parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@spoofing_router.message(SpoofingSenderState.waiting_for_text, F.text)
async def process_spoofing(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 64:
        await message.answer("❌ Не более 64 символов."); return
    settings = await get_or_create_settings(session, message.from_user.id)
    settings.spoofing_sender = text
    await session.commit(); await state.clear()
    await message.answer(MESSAGES["spoofing_saved"].format(name=text), reply_markup=get_main_reply_keyboard())


@spoofing_router.callback_query(F.data == "settings_nick")
async def settings_nick(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    current = settings.spoofing_nick or "не задан"
    await callback.message.edit_text(f"👆 <b>Подмена ника</b>\n\nТекущий: <code>{current}</code>",
                                     parse_mode="HTML", reply_markup=get_nick_menu_keyboard())
    await callback.answer()


@spoofing_router.callback_query(F.data == "nick_set")
async def nick_set(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SpoofingNickState.waiting_for_text)
    await callback.message.edit_text(MESSAGES["nick_set"], parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@spoofing_router.message(SpoofingNickState.waiting_for_text, F.text)
async def process_nick(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 64:
        await message.answer("❌ Не более 64 символов."); return
    settings = await get_or_create_settings(session, message.from_user.id)
    settings.spoofing_nick = text
    await session.commit(); await state.clear()
    await message.answer(MESSAGES["nick_saved"].format(nick=text), reply_markup=get_main_reply_keyboard())


@spoofing_router.callback_query(F.data == "settings_sub_theme")
async def settings_sub_theme(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    current = settings.spoofing_theme or "не задана"
    await callback.message.edit_text(f"📝 <b>Подмена темы</b>\n\nТекущая: <code>{current}</code>",
                                     parse_mode="HTML", reply_markup=get_sub_theme_menu_keyboard())
    await callback.answer()


@spoofing_router.callback_query(F.data == "subtheme_set")
async def subtheme_set(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SpoofingThemeState.waiting_for_text)
    await callback.message.edit_text(MESSAGES["subtheme_set"], parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@spoofing_router.message(SpoofingThemeState.waiting_for_text, F.text)
async def process_sub_theme(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 128:
        await message.answer("❌ Не более 128 символов."); return
    settings = await get_or_create_settings(session, message.from_user.id)
    settings.spoofing_theme = text
    await session.commit(); await state.clear()
    await message.answer(MESSAGES["subtheme_saved"].format(theme=text), reply_markup=get_main_reply_keyboard())


@spoofing_router.callback_query(F.data == "settings_text_theme")
async def settings_text_theme(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    current = settings.text_theme or "не задан"
    await callback.message.edit_text(f"💬 <b>Текст темы</b>\n\nТекущий: <code>{current[:100]}</code>",
                                     parse_mode="HTML", reply_markup=get_text_theme_menu_keyboard())
    await callback.answer()


@spoofing_router.callback_query(F.data == "texttheme_set")
async def texttheme_set(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TextThemeState.waiting_for_text)
    await callback.message.edit_text(MESSAGES["texttheme_set"], parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@spoofing_router.message(TextThemeState.waiting_for_text, F.text)
async def process_text_theme(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 1024:
        await message.answer("❌ Не более 1024 символов."); return
    settings = await get_or_create_settings(session, message.from_user.id)
    settings.text_theme = text
    await session.commit(); await state.clear()
    await message.answer(MESSAGES["texttheme_saved"], reply_markup=get_main_reply_keyboard())
