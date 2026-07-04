import asyncio
import html
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import EmailAccount, ReceiveEmail
from keyboards.main_reply import get_main_reply_keyboard
from keyboards.settings_inline import (
    get_emails_menu_keyboard,
    get_email_add_menu_keyboard,
    get_emails_list_keyboard,
    get_email_detail_keyboard,
    get_receive_menu_keyboard,
    get_receive_list_keyboard,
    get_cancel_keyboard,
    MESSAGES,
)
from states.forms import (
    EmailAddState,
    EmailTestState,
    ReceiveEmailAddState,
)
from services.proxy_connection import (
    smtp_verify,
    smtp_send_message,
    imap_verify,
    imap_fetch_parsed,
    _get_smtp_host,
)

logger = logging.getLogger(__name__)
emails_router = Router(name="emails")


async def _send_test_email(account: EmailAccount, target: str, user_id: int = 0) -> tuple[bool, str]:
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart()
    sender_name = account.display_name or account.email
    msg["From"] = f"{Header(sender_name, 'utf-8')} <{account.email}>"
    msg["To"] = target
    msg["Subject"] = Header("Тестовое письмо от TUTTI Bot", "utf-8")
    msg.attach(MIMEText(f"Это тестовое письмо от бота.\nОтправитель: {account.email}", "plain", "utf-8"))

    ok, err = await smtp_send_message(account.email, account.password, msg, user_id)
    return ok, err


# ========================================================================
# SENDING EMAIL ACCOUNTS — новый дизайн со статистикой и списком
# ========================================================================

async def _render_emails_menu(callback: CallbackQuery, session: AsyncSession,
                                page: int = 0, filter_status: str = "all"):
    """Отрисовывает меню почт с новой вёрсткой (статистика + список + управление).

    Заголовок — компактный, как на фото 2: только «Аккаунты (N)».
    Подробная статистика (✅ активные / ⏸ на паузе / ❌ невалидные) видна
    в верхнем ряду кнопок-фильтров.
    """
    emails = list(
        await session.scalars(
            select(EmailAccount)
            .where(EmailAccount.user_id == callback.from_user.id)
            .order_by(EmailAccount.created_at)
        )
    )

    total = len(emails)
    # ✅ Компактный заголовок — только количество
    header = f"📧 <b>Аккаунты ({total})</b>"

    try:
        await callback.message.edit_text(
            header,
            parse_mode="HTML",
            reply_markup=get_emails_menu_keyboard(emails, page=page,
                                                    filter_status=filter_status),
        )
    except TelegramBadRequest:
        pass


@emails_router.callback_query(F.data == "settings_emails")
async def emails_menu(callback: CallbackQuery, session: AsyncSession):
    await _render_emails_menu(callback, session, page=0, filter_status="all")
    await callback.answer()


@emails_router.callback_query(F.data == "email_select")
async def email_select(callback: CallbackQuery, session: AsyncSession):
    """Теперь просто перенаправляет в основное меню (старый отдельный список не нужен)."""
    await _render_emails_menu(callback, session, page=0, filter_status="all")
    await callback.answer()


@emails_router.callback_query(F.data.startswith("emails_page_"))
async def emails_paginate(callback: CallbackQuery, session: AsyncSession):
    """Пагинация списка почт в основном меню."""
    page = int(callback.data.split("_")[-1])
    await _render_emails_menu(callback, session, page=page, filter_status="all")
    await callback.answer()


# ✅ НОВОЕ: фильтры по статусу
@emails_router.callback_query(F.data == "email_filter_all")
async def email_filter_all(callback: CallbackQuery, session: AsyncSession):
    await _render_emails_menu(callback, session, page=0, filter_status="all")
    await callback.answer()


@emails_router.callback_query(F.data == "email_filter_valid")
async def email_filter_valid(callback: CallbackQuery, session: AsyncSession):
    await _render_emails_menu(callback, session, page=0, filter_status="valid")
    await callback.answer()


@emails_router.callback_query(F.data == "email_filter_paused")
async def email_filter_paused(callback: CallbackQuery, session: AsyncSession):
    await _render_emails_menu(callback, session, page=0, filter_status="paused")
    await callback.answer()


@emails_router.callback_query(F.data == "email_filter_invalid")
async def email_filter_invalid(callback: CallbackQuery, session: AsyncSession):
    await _render_emails_menu(callback, session, page=0, filter_status="invalid")
    await callback.answer()


@emails_router.callback_query(F.data == "emails_noop")
async def emails_noop(callback: CallbackQuery):
    """Пустая кнопка (номер страницы) — ничего не делаем."""
    await callback.answer()


# ✅ НОВОЕ: пауза для конкретного аккаунта
@emails_router.callback_query(F.data.startswith("email_pause_"))
async def email_pause_toggle(callback: CallbackQuery, session: AsyncSession):
    """Ставит аккаунт на паузу и возвращает в меню (быстрое переключение)."""
    email_id = int(callback.data.split("_")[-1])
    acc = await session.scalar(select(EmailAccount).where(EmailAccount.id == email_id))
    if not acc:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    acc.is_paused = True
    await session.commit()
    await callback.answer("⏸️ Аккаунт на паузе", show_alert=False)
    # Возвращаемся в меню (не в детали — для быстрого переключения)
    await _render_emails_menu(callback, session, page=0, filter_status="all")


@emails_router.callback_query(F.data.startswith("email_unpause_"))
async def email_unpause_toggle(callback: CallbackQuery, session: AsyncSession):
    """Снимает аккаунт с паузы и возвращает в меню."""
    email_id = int(callback.data.split("_")[-1])
    acc = await session.scalar(select(EmailAccount).where(EmailAccount.id == email_id))
    if not acc:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    acc.is_paused = False
    await session.commit()
    await callback.answer("▶️ Аккаунт возобновлён", show_alert=False)
    # Возвращаемся в меню
    await _render_emails_menu(callback, session, page=0, filter_status="all")


# ✅ НОВОЕ: поставить ВСЕ аккаунты на паузу
@emails_router.callback_query(F.data == "email_pause_all")
async def email_pause_all(callback: CallbackQuery, session: AsyncSession):
    emails = list(
        await session.scalars(
            select(EmailAccount).where(EmailAccount.user_id == callback.from_user.id)
        )
    )
    if not emails:
        await callback.answer("Нет аккаунтов.", show_alert=True)
        return
    for e in emails:
        e.is_paused = True
    await session.commit()
    await callback.answer(f"⏸️ {len(emails)} аккаунтов на паузе", show_alert=True)
    await _render_emails_menu(callback, session, page=0, filter_status="all")


# ✅ НОВОЕ: удалить все невалидные аккаунты
@emails_router.callback_query(F.data == "email_delete_invalid")
async def email_delete_invalid(callback: CallbackQuery, session: AsyncSession):
    """Удаляет все аккаунты с is_valid=False."""
    invalid = list(
        await session.scalars(
            select(EmailAccount).where(
                EmailAccount.user_id == callback.from_user.id,
                EmailAccount.is_valid == False,
            )
        )
    )
    if not invalid:
        await callback.answer("Нет невалидных аккаунтов.", show_alert=True)
        return
    for e in invalid:
        await session.delete(e)
    await session.commit()
    await callback.answer(f"🗑️ Удалено {len(invalid)} неактивных", show_alert=True)
    await _render_emails_menu(callback, session, page=0, filter_status="all")


@emails_router.callback_query(F.data == "email_add_menu")
async def email_add_menu(callback: CallbackQuery):
    await callback.message.edit_text("Как добавить E-mail?", reply_markup=get_email_add_menu_keyboard())
    await callback.answer()


@emails_router.callback_query(F.data == "email_add_single")
async def email_add_single(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EmailAddState.waiting_for_display_name)
    sent = await callback.message.edit_text(
        "➕ <b>Добавление аккаунта</b>\n\nШаг 1/3: Введите отображаемое имя (например, Emma Gross):",
        parse_mode="HTML", reply_markup=get_cancel_keyboard(),
    )
    await state.update_data(status_msg_id=sent.message_id, status_chat_id=sent.chat.id)
    await callback.answer()


@emails_router.message(EmailAddState.waiting_for_display_name, F.text)
async def process_email_name(message: Message, state: FSMContext, bot):
    name = message.text.strip()
    if len(name) > 64:
        await message.answer("❌ Не более 64 символов.")
        return

    data = await state.get_data()
    is_bulk = data.get("email_bulk", False)

    await message.delete()

    if is_bulk:
        await state.update_data(email_display_name=name, email_bulk_step=2)
        await state.set_state(EmailAddState.waiting_for_email)
        status_id = data.get("status_msg_id")
        chat_id = data.get("status_chat_id")
        await bot.edit_message_text(
            chat_id=chat_id, message_id=status_id,
            text="➕ <b>Добавление аккаунтов</b>\n\n✅ Имя: <b>{}</b>\n\nШаг 2/3: Введите список E-mail и паролей:\n<code>email@domain.com пароль</code>".format(name),
            parse_mode="HTML", reply_markup=get_cancel_keyboard(),
        )
    else:
        await state.update_data(email_display_name=name)
        await state.set_state(EmailAddState.waiting_for_email)
        status_id = data.get("status_msg_id")
        chat_id = data.get("status_chat_id")
        await bot.edit_message_text(
            chat_id=chat_id, message_id=status_id,
            text="➕ <b>Добавление аккаунта</b>\n\n✅ Имя: <b>{}</b>\n\nШаг 2/3: Введите E-mail адрес:".format(name),
            parse_mode="HTML", reply_markup=get_cancel_keyboard(),
        )


@emails_router.message(EmailAddState.waiting_for_email, F.text)
async def process_email_address(message: Message, state: FSMContext, session: AsyncSession, bot):
    data = await state.get_data()
    is_bulk = data.get("email_bulk", False)
    display_name = data.get("email_display_name", "")
    status_id = data.get("status_msg_id")
    chat_id = data.get("status_chat_id")

    if is_bulk:
        # ✅ HIGH-27: немедленно удаляем сообщение с паролями из чата,
        # чтобы они не остались в истории Telegram. Если delete() fail
        # (нет прав admin на удаление чужих сообщений — но в ЛС бота
        # это всегда работает), предупреждаем пользователя.
        try:
            await message.delete()
        except Exception as e:
            logger.warning("Cannot delete bulk-emails message: %s. "
                           "User must delete it manually!", e)
        lines = [l.strip() for l in message.text.strip().splitlines() if l.strip()]
        await bot.edit_message_text(chat_id=chat_id, message_id=status_id, text=f"⏳ Проверяю {len(lines)} аккаунтов...")
        ok_count = 0
        fail_count = 0
        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            email_addr, pwd = parts[0], parts[1]
            if "@" not in email_addr:
                continue
            smtp_ok, _ = await smtp_verify(email_addr, pwd, message.from_user.id)
            if not smtp_ok:
                fail_count += 1
                continue
            session.add(EmailAccount(user_id=message.from_user.id, email=email_addr, password=pwd, display_name=display_name, is_valid=True))
            ok_count += 1
        await session.commit()
        await state.clear()
        result = f"✅ Валидных: {ok_count}"
        if fail_count:
            result += f"\n❌ Отклонено: {fail_count}"
        result += "\n\nℹ️ Сообщение с паролями удалено из чата для безопасности."
        await bot.edit_message_text(chat_id=chat_id, message_id=status_id, text=result)
        return

    email = message.text.strip()
    if "@" not in email or "." not in email:
        await message.answer("❌ Введите корректный E-mail адрес.")
        return
    await message.delete()
    await state.update_data(email_address=email)
    await state.set_state(EmailAddState.waiting_for_password)
    await bot.edit_message_text(
        chat_id=chat_id, message_id=status_id,
        text="➕ <b>Добавление аккаунта</b>\n\n✅ Имя: <b>{}</b>\n✅ E-mail: <b>{}</b>\n\nШаг 3/3: Введите пароль (app-password):".format(display_name, email),
        parse_mode="HTML", reply_markup=get_cancel_keyboard(),
    )


@emails_router.message(EmailAddState.waiting_for_password, F.text)
async def process_email_password(message: Message, state: FSMContext, session: AsyncSession, bot):
    password = message.text.strip()
    data = await state.get_data()
    name = data["email_display_name"]
    email = data["email_address"]
    status_id = data.get("status_msg_id")
    chat_id = data.get("status_chat_id")

    await message.delete()
    await bot.edit_message_text(chat_id=chat_id, message_id=status_id, text=f"⏳ Проверяю подключение к {email}...")

    smtp_ok, smtp_err = await smtp_verify(email, password, message.from_user.id)
    if not smtp_ok:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=status_id,
            text=f"❌ <b>Не удалось подключиться</b>\n\n"
                 f"Сервер: <code>{_get_smtp_host(email)}</code>\n"
                 f"Ошибка: {smtp_err[:300]}\n\n"
                 f"<b>Причины:</b>\n"
                 f"— Gmail/Outlook заблокированы в РФ → добавьте прокси в «Loma Proxy»\n"
                 f"— Gmail требует <b>пароль приложения</b>\n"
                 f"— Mail.ru/Yandex требуют пароль для внешнего приложения",
            parse_mode="HTML",
        )
        await state.clear()
        return

    imap_ok, _ = await imap_verify(email, password, message.from_user.id)
    session.add(EmailAccount(user_id=message.from_user.id, email=email, password=password, display_name=name, is_valid=True))
    await session.commit()
    await state.clear()

    imap_status = "✅ IMAP доступен" if imap_ok else "⚠️ IMAP недоступен (только отправка)"
    await bot.edit_message_text(
        chat_id=chat_id, message_id=status_id,
        text=f"✅ <b>{email}</b> добавлен\nSMTP: ✅ работает | {imap_status}",
        parse_mode="HTML",
    )


@emails_router.callback_query(F.data == "email_add_list")
async def email_add_list(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EmailAddState.waiting_for_display_name)
    await state.update_data(email_bulk=True, email_bulk_step=1)
    sent = await callback.message.edit_text(
        "➕ <b>Добавление аккаунтов</b>\n\nШаг 1/3: Введите отображаемое имя (одно для всех):",
        parse_mode="HTML", reply_markup=get_cancel_keyboard(),
    )
    await state.update_data(status_msg_id=sent.message_id, status_chat_id=sent.chat.id)
    await callback.answer()


async def _show_email_detail(callback: CallbackQuery, session: AsyncSession, email_id: int):
    """Показывает детали конкретного email-аккаунта (с кнопкой паузы)."""
    account = await session.scalar(select(EmailAccount).where(EmailAccount.id == email_id))
    if not account:
        await callback.answer("E-mail не найден.", show_alert=True)
        return
    # Статус
    if not account.is_valid:
        status = "❌ Не валиден"
    elif getattr(account, "is_paused", False):
        status = "⏸️ На паузе"
    else:
        status = "✅ Активен"
    await callback.message.edit_text(
        f"📧 <b>{account.display_name or account.email}</b>\n"
        f"E-mail: <code>{account.email}</code>\n"
        f"Статус: {status}",
        parse_mode="HTML",
        reply_markup=get_email_detail_keyboard(email_id,
                                                is_paused=getattr(account, "is_paused", False)),
    )


@emails_router.callback_query(F.data.startswith("email_detail_"))
async def email_detail(callback: CallbackQuery, session: AsyncSession):
    email_id = int(callback.data.split("_")[-1])
    await _show_email_detail(callback, session, email_id)
    await callback.answer()


@emails_router.callback_query(F.data == "email_test_all")
async def email_test_all(callback: CallbackQuery, state: FSMContext):
    await state.set_state(EmailTestState.waiting_for_target_email)
    await callback.message.edit_text(MESSAGES["email_test_send"], reply_markup=get_cancel_keyboard())
    await callback.answer()


@emails_router.callback_query(F.data.startswith("email_test_"))
async def email_test_specific(callback: CallbackQuery, state: FSMContext):
    email_id = int(callback.data.split("_")[-1])
    await state.update_data(test_email_id=email_id)
    await state.set_state(EmailTestState.waiting_for_target_email)
    await callback.message.edit_text(MESSAGES["email_test_send"], reply_markup=get_cancel_keyboard())
    await callback.answer()


@emails_router.message(EmailTestState.waiting_for_target_email, F.text)
async def process_email_test(message: Message, state: FSMContext, session: AsyncSession):
    target = message.text.strip()
    if "@" not in target:
        await message.answer("❌ Введите корректный E-mail.")
        return

    data = await state.get_data()
    test_email_id = data.get("test_email_id")
    await state.clear()

    if test_email_id:
        account = await session.scalar(select(EmailAccount).where(EmailAccount.id == test_email_id))
    else:
        accounts = list(await session.scalars(select(EmailAccount).where(EmailAccount.user_id == message.from_user.id)))
        account = accounts[0] if accounts else None

    if not account:
        await message.answer("❌ Нет доступного аккаунта для теста.")
        return

    status_msg = await message.answer("📤 Отправка тестового письма...")
    success, error = await _send_test_email(account, target, message.from_user.id)

    if success:
        await status_msg.edit_text(
            f"✅ <b>Письмо отправлено через SMTP!</b>\n\n"
            f"От: {account.email}\nКому: {target}\n\n"
            f"<i>Проверьте ящик получателя (в т.ч. папку Спам).</i>",
            parse_mode="HTML",
        )
    else:
        await status_msg.edit_text(
            f"❌ <b>Ошибка отправки:</b>\n{error[:300]}\n\n"
            f"<i>Проверьте пароль приложения и доступность SMTP-сервера.</i>",
            parse_mode="HTML",
        )


@emails_router.callback_query(F.data.startswith("email_delete_"))
async def email_delete_confirm(callback: CallbackQuery, session: AsyncSession):
    """Удаляет конкретный email-аккаунт и возвращает в новое меню."""
    email_id = int(callback.data.split("_")[-1])
    account = await session.scalar(select(EmailAccount).where(EmailAccount.id == email_id))
    if account:
        await session.delete(account)
        await session.commit()
    await _render_emails_menu(callback, session, page=0, filter_status="all")
    await callback.answer(MESSAGES["email_deleted"])


@emails_router.callback_query(F.data == "email_delete_all")
async def email_delete_all(callback: CallbackQuery, session: AsyncSession):
    await session.execute(delete(EmailAccount).where(EmailAccount.user_id == callback.from_user.id))
    await session.commit()
    await _render_emails_menu(callback, session, page=0, filter_status="all")
    await callback.answer(MESSAGES["email_all_deleted"])


# ========================================================================
# RECEIVE EMAILS
# ========================================================================

@emails_router.callback_query(F.data == "settings_receive")
async def receive_menu(callback: CallbackQuery, session: AsyncSession):
    emails = list(await session.scalars(select(ReceiveEmail).where(ReceiveEmail.user_id == callback.from_user.id).order_by(ReceiveEmail.created_at)))
    await callback.message.edit_text(MESSAGES["receive_menu"].format(count=len(emails)), parse_mode="HTML", reply_markup=get_receive_menu_keyboard(len(emails)))
    await callback.answer()


@emails_router.callback_query(F.data == "receive_add")
async def receive_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ReceiveEmailAddState.waiting_for_email)
    await callback.message.edit_text(MESSAGES["receive_add"], reply_markup=get_cancel_keyboard())
    await callback.answer()


@emails_router.message(ReceiveEmailAddState.waiting_for_email, F.text)
async def process_receive_add(message: Message, state: FSMContext, session: AsyncSession):
    email = message.text.strip()
    if "@" not in email:
        await message.answer("❌ Введите корректный E-mail.")
        return
    session.add(ReceiveEmail(user_id=message.from_user.id, email=email))
    await session.commit()
    await state.clear()
    await message.answer(MESSAGES["receive_saved"])
    emails = list(await session.scalars(select(ReceiveEmail).where(ReceiveEmail.user_id == message.from_user.id).order_by(ReceiveEmail.created_at)))
    await message.answer(MESSAGES["receive_menu"].format(count=len(emails)), parse_mode="HTML", reply_markup=get_receive_menu_keyboard(len(emails)))


@emails_router.callback_query(F.data == "receive_delete")
async def receive_delete(callback: CallbackQuery, session: AsyncSession):
    emails = list(await session.scalars(select(ReceiveEmail).where(ReceiveEmail.user_id == callback.from_user.id).order_by(ReceiveEmail.created_at)))
    if not emails:
        await callback.answer(MESSAGES["no_items"], show_alert=True)
        return
    await callback.message.edit_text("Выберите E-mail для удаления:", reply_markup=get_receive_list_keyboard(emails, "recv_del"))
    await callback.answer()


@emails_router.callback_query(F.data.startswith("recv_del_"))
async def recv_del_confirm(callback: CallbackQuery, session: AsyncSession):
    rec_id = int(callback.data.split("_")[-1])
    rec = await session.scalar(select(ReceiveEmail).where(ReceiveEmail.id == rec_id))
    if rec:
        await session.delete(rec)
        await session.commit()
    emails = list(await session.scalars(select(ReceiveEmail).where(ReceiveEmail.user_id == callback.from_user.id).order_by(ReceiveEmail.created_at)))
    await callback.message.edit_text(MESSAGES["receive_menu"].format(count=len(emails)), parse_mode="HTML", reply_markup=get_receive_menu_keyboard(len(emails)))
    await callback.answer(MESSAGES["receive_deleted"])


@emails_router.callback_query(F.data == "receive_delete_all")
async def receive_delete_all(callback: CallbackQuery, session: AsyncSession):
    await session.execute(delete(ReceiveEmail).where(ReceiveEmail.user_id == callback.from_user.id))
    await session.commit()
    await callback.message.edit_text(MESSAGES["receive_menu"].format(count=0), parse_mode="HTML", reply_markup=get_receive_menu_keyboard(0))
    await callback.answer(MESSAGES["receive_all_deleted"])


@emails_router.callback_query(F.data == "receive_check")
async def receive_check_inbox(callback: CallbackQuery, session: AsyncSession):
    accounts = list(await session.scalars(select(EmailAccount).where(EmailAccount.user_id == callback.from_user.id, EmailAccount.is_valid == True)))
    if not accounts:
        await callback.answer("❌ Нет валидных аккаунтов.", show_alert=True)
        return

    await callback.answer("📥 Проверяю входящие...")
    status_msg = await callback.message.answer("📥 Подключение к IMAP...")

    results = []
    for acc in accounts:
        try:
            emails = await imap_fetch_parsed(acc.email, acc.password, callback.from_user.id, 10)
            safe_email = html.escape(acc.email)
            results.append(f"📧 <b>{safe_email}</b>: {len(emails)} писем")
            for e in emails[:3]:
                subj = html.escape(e.get("subject", "")[:50])
                frm = html.escape(e.get("from", "")[:40])
                results.append(f"  └ {frm} — {subj}")
        except Exception as ex:
            results.append(f"❌ <b>{html.escape(acc.email)}</b>: {html.escape(str(ex)[:100])}")

    text = "📨 <b>Входящие письма:</b>\n\n" + "\n".join(results) if results else "❌ Не удалось проверить."
    await status_msg.edit_text(text[:4096], parse_mode="HTML")
