import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Subject
from keyboards.settings_inline import (
    get_subjects_menu_keyboard,
    get_subjects_list_keyboard,
    get_cancel_keyboard,
    MESSAGES,
)
from states.forms import (
    SubjectAddState,
    SubjectEditState,
    SubjectDeleteState,
)

logger = logging.getLogger(__name__)

subjects_router = Router(name="subjects")


@subjects_router.callback_query(F.data == "settings_subjects")
async def subjects_menu(callback: CallbackQuery, session: AsyncSession):
    subjects = list(
        await session.scalars(
            select(Subject)
            .where(Subject.user_id == callback.from_user.id)
            .order_by(Subject.created_at)
        )
    )
    await callback.message.edit_text(
        MESSAGES["subjects_menu"].format(count=len(subjects)),
        parse_mode="HTML",
        reply_markup=get_subjects_menu_keyboard(len(subjects)),
    )
    await callback.answer()


# --- Add Subject ---

@subjects_router.callback_query(F.data == "subject_add")
async def subject_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SubjectAddState.waiting_for_subject)
    await callback.message.edit_text(
        MESSAGES["subject_add"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@subjects_router.message(SubjectAddState.waiting_for_subject, F.text)
async def process_subject_add(message: Message, state: FSMContext, session: AsyncSession):
    subject_text = message.text.strip()
    if len(subject_text) > 200:
        await message.answer("❌ Не более 200 символов.")
        return

    sub = Subject(user_id=message.from_user.id, subject=subject_text)
    session.add(sub)
    await session.commit()

    await state.clear()
    await message.answer(MESSAGES["subject_saved"])

    subjects = list(
        await session.scalars(
            select(Subject)
            .where(Subject.user_id == message.from_user.id)
            .order_by(Subject.created_at)
        )
    )
    await message.answer(
        MESSAGES["subjects_menu"].format(count=len(subjects)),
        parse_mode="HTML",
        reply_markup=get_subjects_menu_keyboard(len(subjects)),
    )


# --- Edit Subject ---

@subjects_router.callback_query(F.data == "subject_edit")
async def subject_edit(callback: CallbackQuery, session: AsyncSession):
    subjects = list(
        await session.scalars(
            select(Subject)
            .where(Subject.user_id == callback.from_user.id)
            .order_by(Subject.created_at)
        )
    )
    if not subjects:
        await callback.answer(MESSAGES["no_items"], show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите тему для изменения:",
        reply_markup=get_subjects_list_keyboard(subjects, "sub_edit"),
    )
    await callback.answer()


@subjects_router.callback_query(F.data.startswith("sub_edit_"))
async def sub_edit_select(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_sub_id=sub_id)
    await state.set_state(SubjectEditState.waiting_for_subject)
    await callback.message.edit_text(
        MESSAGES["subject_add"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@subjects_router.message(SubjectEditState.waiting_for_subject, F.text)
async def process_subject_edit(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    sub_id = data["edit_sub_id"]
    subject_text = message.text.strip()
    if len(subject_text) > 200:
        await message.answer("❌ Не более 200 символов.")
        return

    sub = await session.scalar(select(Subject).where(Subject.id == sub_id))
    if sub:
        sub.subject = subject_text
        await session.commit()

    await state.clear()
    await message.answer(MESSAGES["subject_saved"])

    subjects = list(
        await session.scalars(
            select(Subject)
            .where(Subject.user_id == message.from_user.id)
            .order_by(Subject.created_at)
        )
    )
    await message.answer(
        MESSAGES["subjects_menu"].format(count=len(subjects)),
        parse_mode="HTML",
        reply_markup=get_subjects_menu_keyboard(len(subjects)),
    )


# --- Delete Subject ---

@subjects_router.callback_query(F.data == "subject_delete")
async def subject_delete(callback: CallbackQuery, session: AsyncSession):
    subjects = list(
        await session.scalars(
            select(Subject)
            .where(Subject.user_id == callback.from_user.id)
            .order_by(Subject.created_at)
        )
    )
    if not subjects:
        await callback.answer(MESSAGES["no_items"], show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите тему для удаления:",
        reply_markup=get_subjects_list_keyboard(subjects, "sub_del"),
    )
    await callback.answer()


@subjects_router.callback_query(F.data.startswith("sub_del_"))
async def sub_del_confirm(callback: CallbackQuery, session: AsyncSession):
    sub_id = int(callback.data.split("_")[-1])
    sub = await session.scalar(select(Subject).where(Subject.id == sub_id))
    if sub:
        await session.delete(sub)
        await session.commit()

    subjects = list(
        await session.scalars(
            select(Subject)
            .where(Subject.user_id == callback.from_user.id)
            .order_by(Subject.created_at)
        )
    )
    await callback.message.edit_text(
        MESSAGES["subjects_menu"].format(count=len(subjects)),
        parse_mode="HTML",
        reply_markup=get_subjects_menu_keyboard(len(subjects)),
    )
    await callback.answer(MESSAGES["subject_deleted"])


@subjects_router.callback_query(F.data == "subject_delete_all")
async def subject_delete_all(callback: CallbackQuery, session: AsyncSession):
    await session.execute(
        delete(Subject).where(Subject.user_id == callback.from_user.id)
    )
    await session.commit()
    await callback.message.edit_text(
        MESSAGES["subjects_menu"].format(count=0),
        parse_mode="HTML",
        reply_markup=get_subjects_menu_keyboard(0),
    )
    await callback.answer(MESSAGES["subject_all_deleted"])
