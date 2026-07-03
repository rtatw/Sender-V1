import logging
import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import UserSettings, GlobalSettings, MailtesterKey
from keyboards.main_reply import get_main_reply_keyboard
from keyboards.settings_inline import get_back_settings_keyboard, get_cancel_keyboard
from database.repository import get_or_create_settings, upsert_global_settings
from states.forms import DeepSeekKeyState, MailtesterKeyState, MailLimitState
from config import ADMIN_ID

logger = logging.getLogger(__name__)
admin_settings_router = Router(name="admin_settings")


async def _check_key(api_key: str) -> tuple[bool, str]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://happy.mailtester.ninja/ninja",
                            params={"email": "test@gmail.com", "key": api_key})
            if r.status_code == 200:
                data = r.json()
                code = data.get("code", "")
                if code == "ok":
                    return True, "Работает"
                elif code == "ko":
                    return True, "Работает (email не найден — это нормально)"
                elif code == "--":
                    return False, "Невалидный ключ"
                else:
                    return True, f"Ответ: {code}"
            elif r.status_code == 429:
                return True, "Rate limit — ключ рабочий"
            else:
                return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:100]


@admin_settings_router.callback_query(F.data == "settings_mailtester_keys")
async def mailtester_keys_menu(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    keys = list(await session.scalars(select(MailtesterKey).order_by(MailtesterKey.created_at)))
    lines = ["🔑 <b>Mailtester API Keys</b>\n"]
    for k in keys:
        masked = k.key[:8] + "..." + k.key[-4:] if len(k.key) > 12 else k.key
        status = "✅" if k.is_valid else "❌"
        active = "🟢" if k.is_active else "🔴"
        lines.append(f"{active} {status} <code>{masked}</code>")
    lines.append(f"\nВсего: <b>{len(keys)}</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="mailtester_key_add"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data="mailtester_key_delete")],
        [InlineKeyboardButton(text="✅ Проверить все", callback_data="mailtester_key_check")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
    ])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@admin_settings_router.callback_query(F.data == "mailtester_key_add")
async def mailtester_key_add(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    await state.set_state(MailtesterKeyState.waiting_for_key)
    await callback.message.edit_text(
        "Введите Mailtester API ключ:", parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@admin_settings_router.message(MailtesterKeyState.waiting_for_key, F.text)
async def process_mailtester_key_add(message: Message, state: FSMContext, session: AsyncSession):
    key = message.text.strip()
    if len(key) < 5:
        await message.answer("❌ Слишком короткий ключ."); return

    msg = await message.answer("⏳ Проверяю ключ...")
    ok, status_text = await _check_key(key)

    if ok:
        mk = MailtesterKey(key=key, is_active=True, is_valid=True, last_checked=datetime.datetime.now())
        session.add(mk)
        await session.commit()
        await msg.edit_text(f"✅ Ключ добавлен. Статус: {status_text}")
    else:
        await msg.edit_text(f"❌ Ключ не прошёл проверку: {status_text}.\n\nВсё равно сохранить?",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Да, сохранить", callback_data="mtkey_force_save"),
                                 InlineKeyboardButton(text="Нет", callback_data="cancel_action")],
                            ]))
        await state.update_data(failed_key=key)

    await state.clear()


@admin_settings_router.callback_query(F.data == "mtkey_force_save")
async def mtkey_force_save(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    key = data.get("failed_key", "")
    if key:
        mk = MailtesterKey(key=key, is_active=True, is_valid=False, last_checked=datetime.datetime.now())
        session.add(mk)
        await session.commit()
        await callback.message.edit_text("✅ Ключ сохранён (помечен как невалидный).")
    await state.clear()
    await callback.answer()


@admin_settings_router.callback_query(F.data == "mailtester_key_delete")
async def mailtester_key_delete(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    keys = list(await session.scalars(select(MailtesterKey).order_by(MailtesterKey.created_at)))
    if not keys:
        await callback.answer("Нет ключей.", show_alert=True); return
    kb_rows = []
    for k in keys:
        masked = k.key[:10] + "..." + k.key[-4:] if len(k.key) > 14 else k.key
        kb_rows.append([InlineKeyboardButton(text=f"🗑 {masked}", callback_data=f"mtkey_del_{k.id}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="settings_mailtester_keys")])
    await callback.message.edit_text("Выберите ключ для удаления:",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("mtkey_del_"))
async def mtkey_del_confirm(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    key_id = int(callback.data.split("_")[-1])
    await session.execute(delete(MailtesterKey).where(MailtesterKey.id == key_id))
    await session.commit()
    await callback.answer("✅ Удалён.", show_alert=True)
    # Refresh the menu
    keys = list(await session.scalars(select(MailtesterKey).order_by(MailtesterKey.created_at)))
    lines = ["🔑 <b>Mailtester API Keys</b>\n"]
    for k in keys:
        masked = k.key[:8] + "..." + k.key[-4:] if len(k.key) > 12 else k.key
        status = "✅" if k.is_valid else "❌"
        active = "🟢" if k.is_active else "🔴"
        lines.append(f"{active} {status} <code>{masked}</code>")
    lines.append(f"\nВсего: <b>{len(keys)}</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="mailtester_key_add"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data="mailtester_key_delete")],
        [InlineKeyboardButton(text="✅ Проверить все", callback_data="mailtester_key_check")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
    ])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@admin_settings_router.callback_query(F.data == "mailtester_key_check")
async def mailtester_key_check(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    keys = list(await session.scalars(select(MailtesterKey).order_by(MailtesterKey.created_at)))
    if not keys:
        await callback.answer("Нет ключей.", show_alert=True); return
    await callback.answer("🔍 Проверка...")
    msg = await callback.message.answer("🔍 Проверяю ключи...")
    results = []
    for k in keys:
        ok, status = await _check_key(k.key)
        k.is_valid = ok
        k.last_checked = datetime.datetime.now()
        masked = k.key[:8] + "..." + k.key[-4:]
        icon = "✅" if ok else "❌"
        results.append(f"{icon} <code>{masked}</code> — {status}")
    await session.commit()
    await msg.edit_text("🔑 <b>Результат проверки:</b>\n" + "\n".join(results), parse_mode="HTML")


@admin_settings_router.callback_query(F.data == "settings_mail_limits")
async def mail_limits_menu(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    # ✅ HIGH-23: читаем лимит из GlobalSettings (персистентный), а не из in-memory
    from database.repository import get_global_settings
    gs = await get_global_settings(session)
    current = gs.daily_limit if gs and gs.daily_limit else 100
    await callback.message.edit_text(
        f"📊 <b>Лимиты рассылки</b>\n\n"
        f"Текущий дневной лимит: <b>{current}</b> писем/день\n\n"
        f"По умолчанию: 100 писем/день (значение 0 = использовать доменные лимиты)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="mail_limit_set")],
            [InlineKeyboardButton(text="🔄 Сбросить на 100", callback_data="mail_limit_reset")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
        ])
    )
    await callback.answer()


@admin_settings_router.callback_query(F.data == "mail_limit_reset")
async def mail_limit_reset(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    # ✅ HIGH-23: сброс в GlobalSettings
    from database.repository import upsert_global_settings
    await upsert_global_settings(session, daily_limit=100)
    from services.anti_ban import set_daily_limit
    set_daily_limit(100)
    await callback.message.edit_text(
        f"📊 <b>Лимиты рассылки</b>\n\nТекущий дневной лимит: <b>100</b> писем/день (по умолчанию)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="mail_limit_set")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
        ])
    )
    await callback.answer("✅ Сброшено на 100")


@admin_settings_router.callback_query(F.data == "mail_limit_set")
async def mail_limit_set(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    await state.set_state(MailLimitState.waiting_for_limit)
    await callback.message.edit_text(
        "Введите новый дневной лимит (число, 0 = доменные лимиты по умолчанию):",
        parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@admin_settings_router.message(MailLimitState.waiting_for_limit, F.text)
async def process_mail_limit(message: Message, state: FSMContext, session: AsyncSession):
    try:
        limit = int(message.text.strip())
        if limit < 0 or limit > 100000:
            await message.answer("❌ От 0 до 100000."); return
    except ValueError:
        await message.answer("❌ Введите число."); return
    # ✅ HIGH-23: сохраняем в GlobalSettings (персистентно)
    from database.repository import upsert_global_settings
    await upsert_global_settings(session, daily_limit=limit)
    from services.anti_ban import set_daily_limit
    set_daily_limit(limit)
    await state.clear()
    display = limit if limit else "доменные лимиты"
    await message.answer(f"✅ Дневной лимит установлен: <b>{display}</b>",
                         parse_mode="HTML", reply_markup=get_main_reply_keyboard())


@admin_settings_router.callback_query(F.data == "settings_deepseek")
async def settings_deepseek(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    # ✅ CRIT-22: читаем DeepSeek-ключ ТОЛЬКО из GlobalSettings (зашифровано)
    from database.repository import get_global_settings
    gs = await get_global_settings(session)
    current = gs.api_key_deepseek_plain if gs else ""
    display = (current[:8] + "..." + current[-4:]) if len(current) > 12 else (current or "не задано")
    kb = get_back_settings_keyboard(("Установить ключ", "deepseek_set"))
    await callback.message.edit_text(
        f"<b>DeepSeek API Key (глобальный)</b>\n\nТекущий: <code>{display}</code>",
        parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@admin_settings_router.callback_query(F.data == "deepseek_set")
async def deepseek_set(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    await state.set_state(DeepSeekKeyState.waiting_for_key)
    await callback.message.edit_text(
        "Введите DeepSeek API ключ (будет сохранён глобально, для всех пользователей):",
        parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@admin_settings_router.message(DeepSeekKeyState.waiting_for_key, F.text)
async def process_deepseek_key(message: Message, state: FSMContext, session: AsyncSession):
    key = message.text.strip()
    await message.delete()
    # ✅ CRIT-22: сохраняем только в GlobalSettings с шифрованием (через property)
    from database.repository import upsert_global_settings
    await upsert_global_settings(session)  # создаём запись если её нет
    from database.repository import get_global_settings
    gs = await get_global_settings(session)
    gs.api_key_deepseek_plain = key  # setter шифрует
    await session.commit()
    await state.clear()
    display = key[:8] + "..." + key[-4:] if len(key) > 12 else key
    await message.answer(
        f"✅ DeepSeek ключ сохранён (глобально): <code>{display}</code>",
        parse_mode="HTML", reply_markup=get_main_reply_keyboard())
