import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import UserSettings
from keyboards.settings_inline import (
    get_cancel_keyboard, get_domains_keyboard, get_domains_delete_keyboard,
    get_domains_edit_keyboard, MESSAGES,
)
from states.forms import DomainAddState, DomainPriorityState
from database.repository import get_or_create_settings

logger = logging.getLogger(__name__)
domains_router = Router(name="domains")


def _get_domains_list(settings) -> list[str]:
    raw = settings.domain_priority or ""
    return [d.strip() for d in raw.split(",") if d.strip() and "." in d]


@domains_router.callback_query(F.data == "settings_domains")
async def settings_domains(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    domains = _get_domains_list(settings)
    text = MESSAGES["domains_current"].format(priority=settings.domain_priority or "(пусто)")
    text += f"\nВсего доменов: {len(domains)}"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_domains_keyboard(settings.domain_priority or ""))
    await callback.answer()


@domains_router.callback_query(F.data == "domains_reset")
async def domains_reset(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    settings.domain_priority = "gmail.com,mail.ru,gmx.de,web.de,yahoo.com,outlook.com"
    await session.commit()
    await callback.message.edit_text(
        MESSAGES["domains_current"].format(priority=settings.domain_priority),
        parse_mode="HTML", reply_markup=get_domains_keyboard(settings.domain_priority),
    )
    await callback.answer("✅ Сброшено на 6 доменов")


@domains_router.callback_query(F.data == "domains_add")
async def domains_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DomainAddState.waiting_for_domain)
    await callback.message.edit_text(MESSAGES["domains_add_prompt"], parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@domains_router.message(DomainAddState.waiting_for_domain, F.text)
async def process_domain_add(message: Message, state: FSMContext, session: AsyncSession):
    domain = message.text.strip().lower()
    if "." not in domain or " " in domain:
        await message.answer(MESSAGES["domains_invalid"]); return
    settings = await get_or_create_settings(session, message.from_user.id)
    domains = _get_domains_list(settings)
    if domain in domains:
        await message.answer(MESSAGES["domains_duplicate"]); await state.clear(); return
    domains.append(domain)
    settings.domain_priority = ",".join(domains)
    await session.commit(); await state.clear()
    await message.answer(MESSAGES["domains_added"].format(domain=domain), parse_mode="HTML",
                         reply_markup=get_domains_keyboard(settings.domain_priority))


@domains_router.callback_query(F.data == "domains_delete")
async def domains_delete(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    domains = _get_domains_list(settings)
    if not domains:
        await callback.answer("Нет доменов для удаления.", show_alert=True); return
    await callback.message.edit_text("Выберите домен для удаления:", reply_markup=get_domains_delete_keyboard(domains))
    await callback.answer()


@domains_router.callback_query(F.data.startswith("domains_del_"))
async def domains_del_confirm(callback: CallbackQuery, session: AsyncSession):
    domain = callback.data.replace("domains_del_", "")
    settings = await get_or_create_settings(session, callback.from_user.id)
    domains = _get_domains_list(settings)
    if domain in domains:
        domains.remove(domain)
        settings.domain_priority = ",".join(domains)
        await session.commit()
    await callback.message.edit_text(
        MESSAGES["domains_current"].format(priority=settings.domain_priority or "(пусто)"),
        parse_mode="HTML", reply_markup=get_domains_keyboard(settings.domain_priority or ""),
    )
    await callback.answer(MESSAGES["domains_deleted"].format(domain=domain))


@domains_router.callback_query(F.data == "domains_edit")
async def domains_edit(callback: CallbackQuery, session: AsyncSession):
    settings = await get_or_create_settings(session, callback.from_user.id)
    domains = _get_domains_list(settings)
    if not domains:
        await callback.answer("Нет доменов.", show_alert=True); return
    await callback.message.edit_text("Выберите домен для перемещения:", reply_markup=get_domains_edit_keyboard(domains))
    await callback.answer()


@domains_router.callback_query(F.data.startswith("domains_order_"))
async def domains_order_click(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DomainPriorityState.waiting_for_priority)
    await callback.message.edit_text(MESSAGES["domains_edit_prompt"], parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@domains_router.message(DomainPriorityState.waiting_for_priority, F.text)
async def process_domain_priority(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    domains = [d.strip().lower() for d in text.replace(",", " ").replace("\n", ",").split(",") if d.strip() and "." in d]
    if not domains:
        await message.answer(MESSAGES["domains_invalid"]); return
    priority_str = ",".join(domains)
    settings = await get_or_create_settings(session, message.from_user.id)
    settings.domain_priority = priority_str
    await session.commit(); await state.clear()
    await message.answer(MESSAGES["domains_updated"].format(priority=priority_str),
                         parse_mode="HTML", reply_markup=get_domains_keyboard(priority_str))
