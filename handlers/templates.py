import logging
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Template
from keyboards.settings_inline import (
    get_templates_menu_keyboard,
    get_templates_list_keyboard,
    get_smart_presets_keyboard,
    get_smart_presets_list_keyboard,
    get_cancel_keyboard,
    MESSAGES,
)
from states.forms import (
    TemplateAddState,
    TemplateEditState,
    TemplateDeleteState,
    SmartPresetAddState,
    SmartPresetEditState,
)
from database.repository import get_or_create_settings

logger = logging.getLogger(__name__)

templates_router = Router(name="templates")


# --- Smart Presets (view only) ---

@templates_router.callback_query(F.data == "settings_smart_presets")
async def smart_presets(callback: CallbackQuery, session: AsyncSession):
    presets = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == callback.from_user.id, Template.type == "smart_preset")
            .order_by(Template.created_at)
        )
    )
    await callback.message.edit_text(
        "🧠 <b>Умные пресеты</b>\n\n"
        + (f"Всего: <b>{len(presets)}</b>" if presets else "ℹ️ Список пуст. Добавьте первый пресет.\n\n<b>Переменные:</b>\n<code>@товар</code> — название (item_title)\n<code>@цена</code> — цена (item_price)\n<code>@ник</code> — имя продавца (person_name)\n<code>@ссылка</code> — ссылка (item_link)"),
        parse_mode="HTML",
        reply_markup=get_smart_presets_keyboard(presets, page=0),
    )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("presets_page_"))
async def presets_paginate(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split("_")[-1])
    presets = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == callback.from_user.id, Template.type == "smart_preset")
            .order_by(Template.created_at)
        )
    )
    await callback.message.edit_text(
        "🧠 <b>Умные пресеты</b>",
        parse_mode="HTML",
        reply_markup=get_smart_presets_keyboard(presets, page=page),
    )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("preset_view_"))
async def preset_view(callback: CallbackQuery, session: AsyncSession):
    preset_id = int(callback.data.split("_")[-1])
    preset = await session.scalar(
        select(Template).where(Template.id == preset_id)
    )
    if preset:
        await callback.answer(f"{preset.name}: {preset.text[:200]}", show_alert=True)
    else:
        await callback.answer("Пресет не найден.", show_alert=True)


# --- Smart Preset ADD (no name, just text) ---
@templates_router.callback_query(F.data == "smart_preset_add")
async def smart_preset_add(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    presets = list(await session.scalars(select(Template).where(Template.user_id == callback.from_user.id, Template.type == "smart_preset")))
    if len(presets) >= 99:
        await callback.answer("❌ Максимум 99 пресетов.", show_alert=True); return
    await state.set_state(SmartPresetAddState.waiting_for_text)
    await callback.message.edit_text(
        "🧠 <b>Новый умный пресет</b>\n\nВведите текст пресета (до 2000 символов):\n\n"
        "Переменные:\n<code>@товар</code> — название\n<code>@цена</code> — цена\n<code>@ник</code> — ник\n<code>@ссылка</code> — ссылка",
        parse_mode="HTML", reply_markup=get_cancel_keyboard("settings_smart_presets"))
    await callback.answer()


@templates_router.message(SmartPresetAddState.waiting_for_text, F.text)
async def smart_preset_add_text(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 2000:
        await message.answer("❌ До 2000 символов."); return
    count = await session.scalar(select(func.count(Template.id)).where(Template.user_id == message.from_user.id, Template.type == "smart_preset"))
    name = f"Preset #{count + 1}"
    preset = Template(user_id=message.from_user.id, name=name, text=text, type="smart_preset")
    session.add(preset); await session.commit(); await state.clear()
    presets = list(await session.scalars(select(Template).where(Template.user_id == message.from_user.id, Template.type == "smart_preset").order_by(Template.created_at)))
    await message.answer(f"✅ Пресет <b>{name}</b> сохранён.", parse_mode="HTML", reply_markup=get_smart_presets_keyboard(presets))


# --- Smart Preset EDIT ---
@templates_router.callback_query(F.data == "smart_preset_edit")
async def smart_preset_edit(callback: CallbackQuery, session: AsyncSession):
    presets = list(await session.scalars(select(Template).where(Template.user_id == callback.from_user.id, Template.type == "smart_preset").order_by(Template.created_at)))
    if not presets: await callback.answer("Нет пресетов.", show_alert=True); return
    await callback.message.edit_text("Выберите пресет для изменения:", reply_markup=get_smart_presets_list_keyboard(presets, "sp_edit"))
    await callback.answer()


@templates_router.callback_query(F.data.startswith("sp_edit_"))
async def sp_edit_select(callback: CallbackQuery, state: FSMContext):
    preset_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_preset_id=preset_id)
    await state.set_state(SmartPresetEditState.waiting_for_name)
    await callback.message.edit_text("Введите новое название пресета:", reply_markup=get_cancel_keyboard("settings_smart_presets"))
    await callback.answer()


@templates_router.message(SmartPresetEditState.waiting_for_name, F.text)
async def sp_edit_name(message: Message, state: FSMContext):
    name = message.text.strip().upper()
    if len(name) > 32: await message.answer("❌ До 32 символов."); return
    await state.update_data(edit_preset_name=name)
    await state.set_state(SmartPresetEditState.waiting_for_text)
    await message.answer("Введите новый текст пресета:", reply_markup=get_cancel_keyboard("settings_smart_presets"))


@templates_router.message(SmartPresetEditState.waiting_for_text, F.text)
async def sp_edit_text(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 2000: await message.answer("❌ До 2000 символов."); return
    data = await state.get_data()
    preset = await session.scalar(select(Template).where(Template.id == data["edit_preset_id"]))
    if preset: preset.name = data["edit_preset_name"]; preset.text = text; await session.commit()
    await state.clear()
    presets = list(await session.scalars(select(Template).where(Template.user_id == message.from_user.id, Template.type == "smart_preset").order_by(Template.created_at)))
    await message.answer(f"✅ Пресет обновлён.", reply_markup=get_smart_presets_keyboard(presets))


# --- Smart Preset DELETE ---
@templates_router.callback_query(F.data == "smart_preset_delete")
async def smart_preset_delete(callback: CallbackQuery, session: AsyncSession):
    presets = list(await session.scalars(select(Template).where(Template.user_id == callback.from_user.id, Template.type == "smart_preset").order_by(Template.created_at)))
    if not presets: await callback.answer("Нет пресетов.", show_alert=True); return
    await callback.message.edit_text("Выберите пресет для удаления:", reply_markup=get_smart_presets_list_keyboard(presets, "sp_del"))
    await callback.answer()


@templates_router.callback_query(F.data.startswith("sp_del_"))
async def sp_del_confirm(callback: CallbackQuery, session: AsyncSession):
    preset_id = int(callback.data.split("_")[-1])
    preset = await session.scalar(select(Template).where(Template.id == preset_id))
    if preset: await session.delete(preset); await session.commit()
    presets = list(await session.scalars(select(Template).where(Template.user_id == callback.from_user.id, Template.type == "smart_preset").order_by(Template.created_at)))
    await callback.message.edit_text("🧠 <b>Умные пресеты</b>", parse_mode="HTML", reply_markup=get_smart_presets_keyboard(presets))
    await callback.answer("✅ Удалён.")


# --- Templates Menu ---

@templates_router.callback_query(F.data == "settings_templates")
async def templates_menu(callback: CallbackQuery, session: AsyncSession):
    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == callback.from_user.id, Template.type == "custom")
            .order_by(Template.created_at)
        )
    )
    await callback.message.edit_text(
        MESSAGES["templates_menu"].format(count=len(templates)),
        parse_mode="HTML",
        reply_markup=get_templates_menu_keyboard(len(templates)),
    )
    await callback.answer()


# --- Add Template ---

@templates_router.callback_query(F.data == "template_add")
async def template_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TemplateAddState.waiting_for_name)
    await callback.message.edit_text(
        MESSAGES["template_add_name"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@templates_router.message(TemplateAddState.waiting_for_name, F.text)
async def process_template_name(message: Message, state: FSMContext):
    name = message.text.strip().upper()
    if len(name) > 32 or not all(c.isalnum() or c in " _-" for c in name):
        await message.answer(MESSAGES["template_name_invalid"])
        return
    await state.update_data(temp_name=name)
    await state.set_state(TemplateAddState.waiting_for_text)
    await message.answer(
        MESSAGES["template_add_text"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@templates_router.message(TemplateAddState.waiting_for_text, F.text)
async def process_template_text(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 1024:
        await message.answer(MESSAGES["template_text_invalid"])
        return

    data = await state.get_data()
    name = data["temp_name"]

    template = Template(
        user_id=message.from_user.id,
        name=name,
        text=text,
        type="custom",
    )
    session.add(template)
    await session.commit()

    await state.clear()
    await message.answer(
        MESSAGES["template_saved"].format(name=name),
        parse_mode="HTML",
    )

    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == message.from_user.id, Template.type == "custom")
            .order_by(Template.created_at)
        )
    )
    await message.answer(
        MESSAGES["templates_menu"].format(count=len(templates)),
        parse_mode="HTML",
        reply_markup=get_templates_menu_keyboard(len(templates)),
    )


# --- Edit Template ---

@templates_router.callback_query(F.data == "template_edit")
async def template_edit(callback: CallbackQuery, session: AsyncSession):
    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == callback.from_user.id, Template.type == "custom")
            .order_by(Template.created_at)
        )
    )
    if not templates:
        await callback.answer(MESSAGES["no_items"], show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите шаблон для изменения:",
        reply_markup=get_templates_list_keyboard(templates, "tpl_edit"),
    )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl_edit_"))
async def tpl_edit_select(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tpl_id = int(callback.data.split("_")[-1])
    await state.update_data(edit_tpl_id=tpl_id)
    await state.set_state(TemplateEditState.waiting_for_name)
    await callback.message.edit_text(
        MESSAGES["template_add_name"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@templates_router.message(TemplateEditState.waiting_for_name, F.text)
async def process_template_edit_name(message: Message, state: FSMContext):
    name = message.text.strip().upper()
    if len(name) > 32 or not all(c.isalnum() or c in " _-" for c in name):
        await message.answer(MESSAGES["template_name_invalid"])
        return
    await state.update_data(edit_tpl_name=name)
    await state.set_state(TemplateEditState.waiting_for_text)
    await message.answer(
        MESSAGES["template_add_text"],
        parse_mode="HTML",
        reply_markup=get_cancel_keyboard(),
    )


@templates_router.message(TemplateEditState.waiting_for_text, F.text)
async def process_template_edit_text(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip()
    if len(text) > 1024:
        await message.answer(MESSAGES["template_text_invalid"])
        return

    data = await state.get_data()
    tpl_id = data["edit_tpl_id"]
    name = data["edit_tpl_name"]

    tpl = await session.scalar(select(Template).where(Template.id == tpl_id))
    if tpl:
        tpl.name = name
        tpl.text = text
        await session.commit()

    await state.clear()
    await message.answer(
        MESSAGES["template_saved"].format(name=name),
        parse_mode="HTML",
    )

    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == message.from_user.id, Template.type == "custom")
            .order_by(Template.created_at)
        )
    )
    await message.answer(
        MESSAGES["templates_menu"].format(count=len(templates)),
        parse_mode="HTML",
        reply_markup=get_templates_menu_keyboard(len(templates)),
    )


# --- Delete Template ---

@templates_router.callback_query(F.data == "template_delete")
async def template_delete(callback: CallbackQuery, session: AsyncSession):
    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == callback.from_user.id, Template.type == "custom")
            .order_by(Template.created_at)
        )
    )
    if not templates:
        await callback.answer(MESSAGES["no_items"], show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите шаблон для удаления:",
        reply_markup=get_templates_list_keyboard(templates, "tpl_del"),
    )
    await callback.answer()


@templates_router.callback_query(F.data.startswith("tpl_del_"))
async def tpl_del_confirm(callback: CallbackQuery, session: AsyncSession):
    tpl_id = int(callback.data.split("_")[-1])
    tpl = await session.scalar(select(Template).where(Template.id == tpl_id))
    if tpl:
        await session.delete(tpl)
        await session.commit()

    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.user_id == callback.from_user.id, Template.type == "custom")
            .order_by(Template.created_at)
        )
    )
    await callback.message.edit_text(
        MESSAGES["templates_menu"].format(count=len(templates)),
        parse_mode="HTML",
        reply_markup=get_templates_menu_keyboard(len(templates)),
    )
    await callback.answer(MESSAGES["template_deleted"])


@templates_router.callback_query(F.data == "template_delete_all")
async def template_delete_all(callback: CallbackQuery, session: AsyncSession):
    await session.execute(
        delete(Template).where(
            Template.user_id == callback.from_user.id,
            Template.type == "custom",
        )
    )
    await session.commit()
    await callback.message.edit_text(
        MESSAGES["templates_menu"].format(count=0),
        parse_mode="HTML",
        reply_markup=get_templates_menu_keyboard(0),
    )
    await callback.answer(MESSAGES["template_all_deleted"])
