import logging
import json
import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import UserSettings, GlobalSettings, MailtesterKey, AdminRole
from database.engine import async_session
from keyboards.main_reply import get_main_reply_keyboard
from keyboards.settings_inline import (
    get_back_settings_keyboard, get_cancel_keyboard,
    get_admin_menu_keyboard, get_user_selection_keyboard,
    get_admin_users_keyboard, get_admin_assistants_keyboard,
)
from database.repository import (
    get_or_create_settings, upsert_global_settings, get_global_settings,
    get_admin_role, get_all_admins, is_superadmin, get_all_users,
    set_admin_role, delete_admin_role,
)
from states.forms import DeepSeekKeyState, MailtesterKeyState, MailLimitState, GlobalReceiveIntervalState
from services.inbox_watcher import get_watcher
from services.state import global_state
from config import ADMIN_ID

logger = logging.getLogger(__name__)
admin_settings_router = Router(name="admin_settings")


async def _check_permission(callback: CallbackQuery, perm: str) -> bool:
    """Проверяет права: суперадмин (ADMIN_ID) или помощник с разрешением."""
    uid = callback.from_user.id
    if is_superadmin(uid):
        return True
    async with async_session() as s:
        ar = await get_admin_role(s, uid)
        if ar and ar.role == "assistant":
            perms = json.loads(ar.permissions)
            if perms.get(perm):
                return True
    await callback.answer("⛔ Нет доступа к этой функции.", show_alert=True)
    return False


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


# ─── Метод проверки email (холодный подбор vs Mailtester API) ────────────────

@admin_settings_router.callback_query(F.data == "settings_email_verifier")
async def email_verifier_menu(callback: CallbackQuery, session: AsyncSession):
    """Меню выбора метода проверки email при поиске."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    from services.email_verifier import get_verifier_info
    info = await get_verifier_info()
    method = info["method"]
    name = info["name"]
    desc = info["description"]

    # Подсветка активного метода
    smtp_icon = "✅ " if method == "smtp_bypass" else "⚪ "
    mt_icon = "✅ " if method == "mailtester" else "⚪ "
    both_icon = "✅ " if method == "both" else "⚪ "

    await callback.message.edit_text(
        f"🔍 <b>Метод проверки email</b>\n\n"
        f"Текущий: <b>{name}</b>\n\n"
        f"<i>{desc}</i>\n\n"
        f"Выберите метод:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{smtp_icon}Холодный подбор (SMTP, бесплатно)",
                                   callback_data="verifier_set_smtp_bypass")],
            [InlineKeyboardButton(text=f"{mt_icon}Mailtester.ninja API (платно)",
                                   callback_data="verifier_set_mailtester")],
            [InlineKeyboardButton(text=f"{both_icon}Гибрид (SMTP + Mailtester fallback)",
                                   callback_data="verifier_set_both")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
        ])
    )
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("verifier_set_"))
async def email_verifier_set(callback: CallbackQuery, session: AsyncSession):
    """Устанавливает метод проверки email."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    method = callback.data.replace("verifier_set_", "")
    if method not in ("smtp_bypass", "mailtester", "both"):
        await callback.answer("Неизвестный метод", show_alert=True)
        return
    from database.repository import upsert_global_settings
    await upsert_global_settings(session, email_verify_method=method)

    names = {
        "smtp_bypass": "Холодный подбор (SMTP)",
        "mailtester": "Mailtester.ninja API",
        "both": "Гибрид (SMTP + Mailtester)",
    }
    await callback.answer(f"✅ Метод: {names[method]}", show_alert=True)

    # Обновляем меню
    from services.email_verifier import get_verifier_info
    info = await get_verifier_info()
    name = info["name"]
    desc = info["description"]
    smtp_icon = "✅ " if method == "smtp_bypass" else "⚪ "
    mt_icon = "✅ " if method == "mailtester" else "⚪ "
    both_icon = "✅ " if method == "both" else "⚪ "
    await callback.message.edit_text(
        f"🔍 <b>Метод проверки email</b>\n\n"
        f"Текущий: <b>{name}</b>\n\n"
        f"<i>{desc}</i>\n\n"
        f"Выберите метод:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{smtp_icon}Холодный подбор (SMTP, бесплатно)",
                                   callback_data="verifier_set_smtp_bypass")],
            [InlineKeyboardButton(text=f"{mt_icon}Mailtester.ninja API (платно)",
                                   callback_data="verifier_set_mailtester")],
            [InlineKeyboardButton(text=f"{both_icon}Гибрид (SMTP + Mailtester fallback)",
                                   callback_data="verifier_set_both")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
        ])
    )


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


# ─── Глобальный интервал проверки входящих ─────────────────────────

@admin_settings_router.callback_query(F.data == "receive_interval_set_global")
async def receive_interval_set_global(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    async with async_session() as s:
        gs = await get_global_settings(s)
        current = gs.receive_check_interval if gs else 30
    await state.set_state(GlobalReceiveIntervalState.waiting_for_interval)
    await callback.message.edit_text(
        f"📥 <b>Глобальный интервал проверки входящих</b>\n\n"
        f"Текущий: <b>{current} сек</b>\n"
        f"Все пользователи будут проверять почту с этим интервалом.\n\n"
        f"Введите новое значение (10–3600 секунд):",
        parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@admin_settings_router.message(GlobalReceiveIntervalState.waiting_for_interval, F.text)
async def process_receive_interval_global(message: Message, state: FSMContext, session: AsyncSession):
    if message.from_user.id != ADMIN_ID:
        await state.clear(); await message.answer("❌ Нет доступа."); return
    try:
        interval = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число от 10 до 3600."); return
    if interval < 10 or interval > 3600:
        await message.answer("❌ Интервал должен быть от 10 до 3600 секунд."); return

    gs = await get_global_settings(session)
    if gs is None:
        gs = await upsert_global_settings(session)
    gs.receive_check_interval = interval
    await session.commit()
    await state.clear()

    bot_ref = global_state.bot
    if bot_ref:
        watcher = get_watcher(bot_ref)
        # Restart watcher for admin (other users will pick up interval on next check)
        watcher.stop_for_user(message.from_user.id)
        await watcher.start_for_user(message.from_user.id, message.from_user.id)

    await message.answer(
        f"✅ Глобальный интервал проверки входящих: <b>{interval} сек</b>\n"
        f"Все пользователи будут проверять почту с этой периодичностью.",
        parse_mode="HTML", reply_markup=get_main_reply_keyboard())


# ─── Clear Database ────────────────────────────────────────────

from keyboards.settings_inline import get_clear_db_items_keyboard, get_clear_db_scope_keyboard, CLEAR_DB_ITEMS
from states.forms import ClearDBState
from database.models import ParsedItem, Template, Subject, IncomingMessage, ReceiveEmail, EmailHealth, Proxy, ProxyBinding, EmailAccount

CLEAR_MODELS = {
    "parsed_items": ParsedItem,
    "templates": Template,
    "subjects": Subject,
    "incoming": IncomingMessage,
    "email_accounts": EmailAccount,
    "proxies": Proxy,
    "proxy_bindings": ProxyBinding,
    "receive_emails": ReceiveEmail,
    "email_health": EmailHealth,
    "user_settings": UserSettings,
    "mailtester_keys": MailtesterKey,
}

MODELS_WITH_UID = {ParsedItem, Template, Subject, IncomingMessage,
                   EmailAccount, Proxy, ProxyBinding,
                   ReceiveEmail, EmailHealth, UserSettings}


@admin_settings_router.callback_query(F.data == "clear_db")
async def clear_db_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    await callback.message.edit_text(
        "🗑️ <b>Очистка базы данных</b>\n\nВыберите scope:",
        parse_mode="HTML", reply_markup=get_clear_db_scope_keyboard())
    await callback.answer()


@admin_settings_router.callback_query(F.data == "clear_db_scope_all")
async def clear_db_scope_all(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    await state.set_data({"scope": "all", "selected": set()})
    await callback.message.edit_text(
        "🗑️ <b>Очистка данных: Все пользователи</b>\n\nВыберите что очистить:",
        parse_mode="HTML",
        reply_markup=get_clear_db_items_keyboard(set(), False))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "clear_db_scope_user")
async def clear_db_scope_user(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "clear_db"):
        return
    async with async_session() as s:
        users = await get_all_users(s)
    await callback.message.edit_text(
        "👤 <b>Выберите пользователя для очистки:</b>",
        parse_mode="HTML",
        reply_markup=get_user_selection_keyboard(users, callback_prefix="clear_db_user"))
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("clear_db_user_"))
async def clear_db_process_user_select(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "clear_db"):
        return
    uid_raw = callback.data.replace("clear_db_user_", "")
    if uid_raw == "all":
        await state.set_data({"scope": "all", "selected": set()})
        await callback.message.edit_text(
            "🗑️ <b>Очистка данных: Все пользователи</b>\n\nВыберите что очистить:",
            parse_mode="HTML",
            reply_markup=get_clear_db_items_keyboard(set(), False))
    else:
        try:
            uid = int(uid_raw)
        except ValueError:
            await callback.answer("❌ Ошибка ID.", show_alert=True); return
        await state.set_data({"scope": "user", "user_id": uid, "selected": set()})
        await callback.message.edit_text(
            f"🗑️ <b>Очистка данных: пользователь {uid}</b>\n\nВыберите что очистить:",
            parse_mode="HTML",
            reply_markup=get_clear_db_items_keyboard(set(), False))
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("clear_toggle_"))
async def clear_db_toggle(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    data = await state.get_data()
    selected: set = data.get("selected", set())
    key = callback.data.replace("clear_toggle_", "")
    if key in selected:
        selected.discard(key)
    else:
        selected.add(key)
    await state.update_data(selected=selected)
    await callback.message.edit_text(
        "🗑️ <b>Очистка данных</b>\n\nВыберите что очистить:",
        parse_mode="HTML",
        reply_markup=get_clear_db_items_keyboard(selected, len(selected) == len(CLEAR_DB_ITEMS)))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "clear_db_toggle_all")
async def clear_db_toggle_all(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    data = await state.get_data()
    selected: set = data.get("selected", set())
    if len(selected) == len(CLEAR_DB_ITEMS):
        selected = set()
    else:
        selected = {k for k, _ in CLEAR_DB_ITEMS}
    await state.update_data(selected=selected)
    await callback.message.edit_text(
        "🗑️ <b>Очистка данных</b>\n\nВыберите что очистить:",
        parse_mode="HTML",
        reply_markup=get_clear_db_items_keyboard(selected, len(selected) == len(CLEAR_DB_ITEMS)))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "clear_db_do_confirm")
async def clear_db_do_confirm(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    data = await state.get_data()
    selected: set = data.get("selected", set())
    if not selected:
        await callback.answer("❌ Ничего не выбрано.", show_alert=True)
        return
    scope = data.get("scope", "all")
    scope_label = "всех пользователей" if scope == "all" else f"пользователя {data.get('user_id')}"
    items = [label for k, label in CLEAR_DB_ITEMS if k in selected]
    await callback.message.edit_text(
        f"⚠️ <b>Подтверждение очистки</b>\n\n"
        f"Scope: <b>{scope_label}</b>\n"
        f"Будут удалены:\n" + "\n".join(f"• {it}" for it in items) + "\n\n"
        f"<b>Это действие необратимо!</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔴 Да, очистить", callback_data="clear_db_execute")],
            [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="clear_db")],
        ]))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "clear_db_execute")
async def clear_db_execute(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    data = await state.get_data()
    selected: set = data.get("selected", set())
    scope = data.get("scope", "all")
    user_id = data.get("user_id")
    await state.clear()

    cleared = []
    async with async_session() as s:
        for key in selected:
            model = CLEAR_MODELS.get(key)
            if not model:
                continue
            try:
                stmt = delete(model)
                if scope == "user" and model in MODELS_WITH_UID:
                    stmt = stmt.where(model.user_id == user_id)
                await s.execute(stmt)
                cleared.append(key)
            except Exception as e:
                logger.warning("Clear %s failed: %s", key, e)
        await s.commit()

    labels = [label for k, label in CLEAR_DB_ITEMS if k in cleared]
    scope_label = "всех пользователей" if scope == "all" else f"пользователя {user_id}"
    await callback.message.edit_text(
        f"✅ <b>Очистка завершена</b>\n\n"
        f"Scope: <b>{scope_label}</b>\n"
        f"Удалены:\n" + "\n".join(f"• {it}" for it in labels),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В админку", callback_data="back_admin")],
        ]))
    await callback.answer()


# ─── User pagination ────────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "users_noop")
async def users_noop(callback: CallbackQuery):
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("users_page_"))
async def users_page_handler(callback: CallbackQuery):
    """Обработчик пагинации списка пользователей."""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("", show_alert=True); return
    try:
        page = int(callback.data.split("_")[-1])
    except ValueError:
        return
    async with async_session() as s:
        users = await get_all_users(s)
    await callback.message.edit_reply_markup(
        reply_markup=get_user_selection_keyboard(users, page=page))
    await callback.answer()


# ─── Back to admin panel ────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "back_admin")
async def back_to_admin(callback: CallbackQuery):
    """Возвращает в админ-панель с проверкой прав."""
    uid = callback.from_user.id
    if not is_superadmin(uid):
        async with async_session() as s:
            ar = await get_admin_role(s, uid)
            if not ar:
                await callback.answer("⛔ Нет доступа.", show_alert=True); return
            import json
            perms_dict = json.loads(ar.permissions)
            perms = [k for k, v in perms_dict.items() if v]
        await callback.message.edit_text(
            "👑 <b>Админ-панель</b>\n\nВыберите раздел:",
            parse_mode="HTML", reply_markup=get_admin_menu_keyboard(perms))
    else:
        await callback.message.edit_text(
            "👑 <b>Админ-панель</b>\n\nВыберите раздел:",
            parse_mode="HTML", reply_markup=get_admin_menu_keyboard())
    await callback.answer()


# ─── Admin: Users ───────────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "admin_users")
async def admin_users_menu(callback: CallbackQuery):
    if not await _check_permission(callback, "view_users"):
        return
    await callback.message.edit_text(
        "👁️ <b>Пользователи</b>\n\nВыберите действие:",
        parse_mode="HTML", reply_markup=get_admin_users_keyboard())
    await callback.answer()


@admin_settings_router.callback_query(F.data == "admin_users_list")
async def admin_users_list(callback: CallbackQuery):
    if not await _check_permission(callback, "view_users"):
        return
    async with async_session() as s:
        users = await get_all_users(s)
    if not users:
        await callback.message.edit_text("ℹ️ Нет пользователей.", reply_markup=get_admin_users_keyboard())
        await callback.answer(); return
    lines = [f"👤 <b>Всего пользователей: {len(users)}</b>\n"]
    for u in users:
        name = u.display_name or f"ID {u.user_id}"
        username = f" (@{u.username})" if u.username else ""
        lines.append(f"• {name}{username} — <code>{u.user_id}</code>")
    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")],
        ]))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "admin_user_stats")
async def admin_user_stats(callback: CallbackQuery):
    if not await _check_permission(callback, "view_users"):
        return
    async with async_session() as s:
        users = await get_all_users(s)
    await callback.message.edit_text(
        "👤 <b>Выберите пользователя для просмотра статистики:</b>",
        parse_mode="HTML",
        reply_markup=get_user_selection_keyboard(users, callback_prefix="user_stats"))
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("user_stats_"))
async def admin_user_stats_detail(callback: CallbackQuery):
    if not await _check_permission(callback, "view_users"):
        return
    uid_raw = callback.data.replace("user_stats_", "")
    if uid_raw == "all":
        await callback.answer("Выберите конкретного пользователя.", show_alert=True); return
    try:
        uid = int(uid_raw)
    except ValueError:
        return
    async with async_session() as s:
        u = await get_or_create_settings(s, uid)
        from database.repository import (
            get_email_accounts, get_templates, get_subjects,
            get_proxies, get_parsed_items, get_receive_emails,
        )
        emails = await get_email_accounts(s, uid)
        templates = await get_templates(s, uid)
        subjects = await get_subjects(s, uid)
        proxies = await get_proxies(s, uid)
        parsed = await get_parsed_items(s, uid)
        receive_emails = await get_receive_emails(s, uid)
    valid_emails = sum(1 for e in emails if e.is_valid)
    name = u.display_name or f"ID {uid}"
    username = f" (@{u.username})" if u.username else ""
    text = (
        f"📊 <b>Статистика: {name}{username}</b>\n\n"
        f"ID: <code>{uid}</code>\n"
        f"📧 Email аккаунтов: {len(emails)} (✅ {valid_emails})\n"
        f"📝 Шаблонов: {len(templates)}\n"
        f"✍️ Тем: {len(subjects)}\n"
        f"🌐 Прокси: {len(proxies)}\n"
        f"📦 Объявлений: {len(parsed)}\n"
        f"📨 Receive-почт: {len(receive_emails)}\n"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")],
        ]))
    await callback.answer()


# ─── Admin: Assistants ──────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "admin_assistants")
async def admin_assistants_menu(callback: CallbackQuery):
    if not await _check_permission(callback, "manage_admins"):
        return
    async with async_session() as s:
        assistants = await get_all_admins(s)
    await callback.message.edit_text(
        "👥 <b>Управление помощниками</b>\n\n"
        "Помощники имеют доступ только к разрешённым функциям.\n"
        "Суперадминистратор (<code>ADMIN_ID</code>) имеет все права.",
        parse_mode="HTML",
        reply_markup=get_admin_assistants_keyboard(assistants))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "admin_assistant_add")
async def admin_assistant_add_start(callback: CallbackQuery):
    if not await _check_permission(callback, "manage_admins"):
        return
    async with async_session() as s:
        users = await get_all_users(s)
    await callback.message.edit_text(
        "👤 <b>Выберите пользователя, которого хотите сделать помощником:</b>",
        parse_mode="HTML",
        reply_markup=get_user_selection_keyboard(users, callback_prefix="assistant_add",
                                                   show_all=False))
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("assistant_add_"))
async def admin_assistant_add_perm(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "manage_admins"):
        return
    uid_raw = callback.data.replace("assistant_add_", "")
    try:
        uid = int(uid_raw)
    except ValueError:
        return
    if is_superadmin(uid):
        await callback.answer("❌ Нельзя добавить суперадмина как помощника.", show_alert=True); return
    await state.set_data({"new_assistant_id": uid, "assistant_perms": {}})
    await show_permission_toggle(callback.message, state)
    await callback.answer()


async def show_permission_toggle(message: Message | CallbackQuery, state: FSMContext):
    """Отображает список прав для включения/выключения."""
    from database.models import ADMIN_PERMISSIONS
    data = await state.get_data()
    perms: dict = data.get("assistant_perms", {})
    rows = []
    for key, label in ADMIN_PERMISSIONS.items():
        checked = perms.get(key, False)
        icon = "✅" if checked else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {label}", callback_data=f"assistant_perm_toggle_{key}")])
    rows.append([
        InlineKeyboardButton(text="✅ Все", callback_data="assistant_perm_all"),
        InlineKeyboardButton(text="❌ Очистить", callback_data="assistant_perm_none"),
    ])
    rows.append([InlineKeyboardButton(text="💾 Сохранить", callback_data="assistant_perm_save")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_assistants")])
    text = (
        "👤 <b>Настройка прав помощника</b>\n\n"
        f"ID: <code>{data.get('new_assistant_id')}</code>\n"
        "Отметьте разрешённые функции:"
    )
    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, parse_mode="HTML",
                                        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await message.edit_text(text, parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@admin_settings_router.callback_query(F.data.startswith("assistant_perm_toggle_"))
async def assistant_perm_toggle(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "manage_admins"):
        return
    data = await state.get_data()
    perms: dict = data.get("assistant_perms", {})
    key = callback.data.replace("assistant_perm_toggle_", "")
    perms[key] = not perms.get(key, False)
    await state.update_data(assistant_perms=perms)
    await show_permission_toggle(callback, state)
    await callback.answer()


@admin_settings_router.callback_query(F.data == "assistant_perm_all")
async def assistant_perm_all(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "manage_admins"):
        return
    from database.models import ADMIN_PERMISSIONS
    await state.update_data(assistant_perms={k: True for k in ADMIN_PERMISSIONS})
    await show_permission_toggle(callback, state)
    await callback.answer()


@admin_settings_router.callback_query(F.data == "assistant_perm_none")
async def assistant_perm_none(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "manage_admins"):
        return
    from database.models import ADMIN_PERMISSIONS
    await state.update_data(assistant_perms={k: False for k in ADMIN_PERMISSIONS})
    await show_permission_toggle(callback, state)
    await callback.answer()


@admin_settings_router.callback_query(F.data == "assistant_perm_save")
async def assistant_perm_save(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "manage_admins"):
        return
    data = await state.get_data()
    uid = data.get("new_assistant_id")
    perms = data.get("assistant_perms", {})
    if not uid:
        await callback.answer("❌ Ошибка: ID не найден.", show_alert=True); return
    async with async_session() as s:
        await set_admin_role(s, uid, role="assistant", permissions=perms)
    await state.clear()
    await callback.message.edit_text(
        f"✅ Помощник <code>{uid}</code> добавлен с настроенными правами.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_assistants")],
        ]))
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("admin_assistant_edit_"))
async def admin_assistant_edit(callback: CallbackQuery, state: FSMContext):
    if not await _check_permission(callback, "manage_admins"):
        return
    uid_raw = callback.data.replace("admin_assistant_edit_", "")
    try:
        uid = int(uid_raw)
    except ValueError:
        return
    async with async_session() as s:
        ar = await get_admin_role(s, uid)
        if not ar:
            await callback.answer("❌ Помощник не найден.", show_alert=True); return
        perms = json.loads(ar.permissions)
    await state.set_data({"new_assistant_id": uid, "assistant_perms": perms})
    await show_permission_toggle(callback, state)
    await callback.answer()


@admin_settings_router.callback_query(F.data.startswith("admin_assistant_del_"))
async def admin_assistant_del(callback: CallbackQuery):
    if not await _check_permission(callback, "manage_admins"):
        return
    uid_raw = callback.data.replace("admin_assistant_del_", "")
    try:
        uid = int(uid_raw)
    except ValueError:
        return
    async with async_session() as s:
        await delete_admin_role(s, uid)
    await callback.answer("✅ Помощник удалён.", show_alert=True)
    # Refresh
    async with async_session() as s:
        assistants = await get_all_admins(s)
    await callback.message.edit_text(
        "👥 <b>Управление помощниками</b>",
        parse_mode="HTML",
        reply_markup=get_admin_assistants_keyboard(assistants))


# ─── Admin: Stats ───────────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not await _check_permission(callback, "stats"):
        return
    async with async_session() as s:
        users = await get_all_users(s)
        from database.repository import get_email_accounts
        total_emails = 0
        total_valid = 0
        for u in users:
            emails = await get_email_accounts(s, u.user_id)
            total_emails += len(emails)
            total_valid += sum(1 for e in emails if e.is_valid)
        from database.models import ParsedItem, EmailAccount, Proxy, IncomingMessage
        total_parsed = await s.scalar(select(func.count(ParsedItem.id)))
        total_proxies = await s.scalar(select(func.count(Proxy.id)))
        total_incoming = await s.scalar(select(func.count(IncomingMessage.id)))
    text = (
        f"📊 <b>Общая статистика бота</b>\n\n"
        f"👤 Пользователей: {len(users)}\n"
        f"📧 Email аккаунтов: {total_emails} (✅ {total_valid})\n"
        f"📦 Объявлений: {total_parsed or 0}\n"
        f"🌐 Прокси: {total_proxies or 0}\n"
        f"📨 Входящих писем: {total_incoming or 0}\n"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_admin")],
        ]))
    await callback.answer()


# ─── Admin: Logs ────────────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "admin_logs")
async def admin_logs(callback: CallbackQuery):
    if not await _check_permission(callback, "view_logs"):
        return
    import subprocess
    try:
        result = subprocess.run(
            ["journalctl", "-u", "tutti-bot.service", "--no-pager", "-n", "50"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout
        if not output:
            output = "(нет логов)"
    except Exception as e:
        output = f"Ошибка получения логов: {e}"
    await callback.message.edit_text(
        f"📋 <b>Последние логи бота:</b>\n\n<pre>{output}</pre>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_logs")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_admin")],
        ]))
    await callback.answer()


# ─── Admin: Restart Bot ─────────────────────────────────────────────

@admin_settings_router.callback_query(F.data == "admin_restart")
async def admin_restart(callback: CallbackQuery):
    if not await _check_permission(callback, "restart_bot"):
        return
    await callback.message.edit_text(
        "🔄 <b>Перезапуск бота</b>\n\n"
        "Вы уверены, что хотите перезапустить бота?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔴 Да, перезапустить", callback_data="admin_restart_confirm")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="back_admin")],
        ]))
    await callback.answer()


@admin_settings_router.callback_query(F.data == "admin_restart_confirm")
async def admin_restart_confirm(callback: CallbackQuery):
    if not await _check_permission(callback, "restart_bot"):
        return
    await callback.message.edit_text("🔄 Перезапускаю бота...")
    await callback.answer()
    import subprocess, sys
    try:
        subprocess.run(["systemctl", "restart", "tutti-bot.service"], timeout=10)
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
