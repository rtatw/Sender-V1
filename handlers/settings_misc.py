import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from keyboards.main_reply import get_main_reply_keyboard
from keyboards.settings_inline import get_cancel_keyboard, MESSAGES
from states.forms import ProfileState
from database.repository import get_or_create_settings
from services.team_config import profile_attr_name, TEAM_KEYS

logger = logging.getLogger(__name__)
misc_settings_router = Router(name="misc_settings")


@misc_settings_router.callback_query(F.data == "settings_profile")
async def settings_profile(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    lines = ["👤 <b>Profile</b>\n"]
    for team in TEAM_KEYS:
        pid = getattr(settings, profile_attr_name(team), "") or "не задан"
        lines.append(f"<b>{team}:</b> <code>{pid}</code>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Tsum ✏️", callback_data="profile_set_Tsum"),
         InlineKeyboardButton(text="Nurrp ✏️", callback_data="profile_set_Nurrp")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
    ])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@misc_settings_router.callback_query(F.data == "profile_set_Tsum")
async def profile_set_tsum(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileState.waiting_for_id)
    await state.update_data(profile_team="Tsum")
    await callback.message.edit_text(
        "Введите Profile ID для Tsum:", parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@misc_settings_router.callback_query(F.data == "profile_set_Nurrp")
async def profile_set_nurrp(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileState.waiting_for_id)
    await state.update_data(profile_team="Nurrp")
    await callback.message.edit_text(
        "Введите Profile ID для Nurrp:", parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@misc_settings_router.message(ProfileState.waiting_for_id, F.text)
async def process_profile(message: Message, state: FSMContext, session: AsyncSession):
    profile_id = message.text.strip()
    data = await state.get_data()
    team = data.get("profile_team", "Nurrp")
    settings = await get_or_create_settings(session, message.from_user.id)
    setattr(settings, profile_attr_name(team), profile_id)
    await session.commit()
    await state.clear()
    await message.answer(f"✅ Profile ID сохранён для <b>{team}</b>.", parse_mode="HTML",
                         reply_markup=get_main_reply_keyboard())


@misc_settings_router.callback_query(F.data == "settings_command")
async def settings_command(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    from keyboards.settings_inline import get_command_keyboard
    await callback.message.edit_text(
        f"🎭 <b>Команда</b>\n\nТекущая: <b>{settings.active_command}</b>",
        parse_mode="HTML", reply_markup=get_command_keyboard(settings.active_command))
    await callback.answer()


@misc_settings_router.callback_query(F.data.startswith("command_set_"))
async def command_set(callback: CallbackQuery, session: AsyncSession):
    command = callback.data.replace("command_set_", "")
    settings = await get_or_create_settings(session, callback.from_user.id)
    settings.active_command = command
    await session.commit()
    from keyboards.settings_inline import get_command_keyboard
    await callback.message.edit_text(
        f"🎭 <b>Команда</b>\n\nТекущая: <b>{command}</b>",
        parse_mode="HTML", reply_markup=get_command_keyboard(command))
    await callback.answer(f"✅ Команда изменена на: {command}")


@misc_settings_router.callback_query(F.data == "settings_card_toggle")
async def settings_card_toggle(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    settings.card_enabled = not settings.card_enabled
    await session.commit()
    status = "ВКЛ" if settings.card_enabled else "ВЫКЛ"
    await callback.answer(f"💳 Card: {status}", show_alert=True)
