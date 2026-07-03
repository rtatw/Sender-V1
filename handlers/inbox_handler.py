import html as html_mod
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

from database.models import IncomingMessage, Template, EmailAccount, UserSettings
from keyboards.inbox_kb import (
    get_inbox_message_keyboard,
    get_write_more_keyboard,
    get_send_preview_keyboard,
    get_preset_reply_keyboard,
)
from states.forms import WriteCustomTextState
from services.deepseek import translate_text
from services.proxy_connection import smtp_send_message
from services.email_cleaner import clean_email_body

logger = logging.getLogger(__name__)
inbox_router = Router(name="inbox")

_bot_username_cache: str | None = None


async def _get_bot_username(bot) -> str:
    global _bot_username_cache
    if _bot_username_cache is None:
        me = await bot.get_me()
        _bot_username_cache = me.username
    return _bot_username_cache


# ======  TRANSLATE  ======

@inbox_router.callback_query(F.data.startswith("inbox_translate_"))
async def inbox_translate(callback: CallbackQuery, session: AsyncSession):
    msg_id = int(callback.data.split("_")[-1])
    incoming = await session.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))
    if not incoming:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    await callback.answer("🈶 Перевожу...")
    original = incoming.body
    result = await translate_text(original, callback.from_user.id)

    is_error = False
    clean = result
    for err_prefix in ["[Перевод недоступен]", "[Ошибка:", "[Ошибка соединения]", "[Превышен лимит"]:
        if clean.startswith(err_prefix):
            is_error = True
            break

    if is_error:
        await callback.answer("⚠️ Перевод недоступен. Проверьте DeepSeek ключ.", show_alert=True)
        return

    if clean.startswith("[Переведено]"):
        clean = clean[len("[Переведено]"):].strip().lstrip("\n")

    if not clean.strip() or clean == original:
        await callback.answer("⚠️ Перевод не получен.", show_alert=True)
        return

    from_e = html_mod.escape(incoming.from_email)
    subject_e = html_mod.escape(incoming.subject)
    display_clean = clean_email_body(clean)[0]
    safe_clean = html_mod.escape(display_clean) if display_clean else "(пустое письмо)"

    text = f"⚡️ <code>{html_mod.escape(incoming.account_email)}</code> ← <b>{from_e}</b> [Переведено]" + (f"\n<b>{subject_e}</b>" if subject_e else "") + f"\n<blockquote>{safe_clean[:500]}</blockquote>"
    try:
        await callback.message.edit_text(
            text[:4096], parse_mode="HTML",
            reply_markup=get_inbox_message_keyboard(msg_id, translated=True),
        )
    except Exception as e:
        logger.warning("Edit translate failed: %s", e)
        await callback.answer("Ошибка отображения.", show_alert=True)


# ======  SHOW ORIGINAL  ======

@inbox_router.callback_query(F.data.startswith("show_orig_"))
async def show_original(callback: CallbackQuery, session: AsyncSession):
    msg_id = int(callback.data.split("_")[-1])
    incoming = await session.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))
    if not incoming:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    from_e = html_mod.escape(incoming.from_email)
    subject_e = html_mod.escape(incoming.subject)
    display_body = clean_email_body(incoming.body)[0]
    safe_body = html_mod.escape(display_body) if display_body else "(пустое письмо)"

    text = f"⚡️ <code>{html_mod.escape(incoming.account_email)}</code> ← <b>{from_e}</b>" + (f"\n<b>{subject_e}</b>" if subject_e else "") + f"\n<blockquote>{safe_body[:500]}</blockquote>"
    try:
        await callback.message.edit_text(
            text[:4096], parse_mode="HTML",
            reply_markup=get_inbox_message_keyboard(msg_id, translated=False),
        )
    except Exception as e:
        logger.warning("Edit original failed: %s", e)
    await callback.answer()


# ======  WRITE MORE  ======

@inbox_router.callback_query(F.data.startswith("write_more_"))
async def write_more_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    msg_id = int(callback.data.split("_")[-1])
    incoming = await session.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))
    if not incoming:
        await callback.answer("Письмо не найдено.", show_alert=True)
        return
    await state.update_data(reply_msg_id=msg_id, reply_email=incoming.from_email, reply_subject=incoming.subject)
    presets = list(await session.scalars(
        select(Template).where(Template.user_id == callback.from_user.id, Template.type == "custom").order_by(Template.created_at)
    ))
    await callback.message.answer(
        f"📝 <b>Ответ на письмо</b>\nКому: {html_mod.escape(incoming.from_email)}\nТема: Re: {html_mod.escape(incoming.subject)}\n\nВыберите шаблон или напишите свой текст:",
        parse_mode="HTML", reply_markup=get_write_more_keyboard(msg_id, presets),
    )
    await callback.answer()


@inbox_router.callback_query(F.data == "cancel_write")
async def cancel_write(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("❌ Отменено.")


@inbox_router.callback_query(F.data.startswith("write_custom_text_"))
async def write_custom_text_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WriteCustomTextState.waiting_for_text)
    await callback.message.edit_text("✍️ Напишите текст ответа:", reply_markup=None)
    await callback.answer()


@inbox_router.callback_query(F.data.startswith("write_preset_"))
async def write_preset_menu(callback: CallbackQuery, session: AsyncSession):
    msg_id = int(callback.data.split("_")[-1])
    presets = list(await session.scalars(select(Template).where(Template.user_id == callback.from_user.id, Template.type == "custom").order_by(Template.created_at)))
    if not presets:
        await callback.answer("Нет шаблонов.", show_alert=True)
        return
    await callback.message.edit_text("📂 <b>Выберите шаблон:</b>", parse_mode="HTML", reply_markup=get_preset_reply_keyboard(presets, msg_id))
    await callback.answer()


@inbox_router.callback_query(F.data.startswith("preset_reply_"))
async def preset_reply_send(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("_")
    preset_id, msg_id = int(parts[2]), int(parts[3])
    preset = await session.scalar(select(Template).where(Template.id == preset_id))
    incoming = await session.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))
    if not preset or not incoming:
        await callback.answer("Данные не найдены.", show_alert=True)
        return
    accounts = list(await session.scalars(select(EmailAccount).where(EmailAccount.user_id == callback.from_user.id, EmailAccount.is_valid == True)))
    if not accounts:
        await callback.answer("Нет активных аккаунтов.", show_alert=True)
        return
    settings = await session.scalar(select(UserSettings).where(UserSettings.user_id == callback.from_user.id))
    nick = settings.spoofing_nick if settings and settings.spoofing_nick else None
    body_text = preset.text
    if nick:
        body_text = body_text.replace("{nick}", nick).replace("{name}", nick)
    reply_subj = f"Re: {incoming.subject}" if not incoming.subject.lower().startswith("re:") else incoming.subject
    await _do_send(callback, state, accounts[0], settings, incoming.from_email, reply_subj, body_text)


@inbox_router.message(WriteCustomTextState.waiting_for_text, F.text)
async def catch_text_input(message: Message, state: FSMContext, session: AsyncSession):
    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("Введите текст ответа или выберите шаблон.")
        return
    data = await state.get_data()
    msg_id = data.get("reply_msg_id")
    to_email = data.get("reply_email", "")
    subject = data.get("reply_subject", "")
    logger.info("Custom reply text: to=%s, len=%d", to_email, len(text))
    await state.update_data(custom_text=text)
    preview = (
        f"✍️ <b>Предпросмотр ответа:</b>\n\nКому: {html_mod.escape(to_email)}\n"
        f"Тема: Re: {html_mod.escape(subject)}\n\n{html_mod.escape(text[:500])}{'...' if len(text) > 500 else ''}"
    )
    await message.answer(preview[:2048], parse_mode="HTML", reply_markup=get_send_preview_keyboard(msg_id or 0))


@inbox_router.callback_query(F.data.startswith("send_custom_"))
async def send_custom_text(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    text = data.get("custom_text", "")
    to_email = data.get("reply_email", "")
    subject = data.get("reply_subject", "")
    if not text or not to_email:
        await callback.answer("Нет текста или получателя.", show_alert=True)
        return
    accounts = list(await session.scalars(select(EmailAccount).where(EmailAccount.user_id == callback.from_user.id, EmailAccount.is_valid == True)))
    if not accounts:
        await callback.answer("Нет активных аккаунтов.", show_alert=True)
        return
    settings = await session.scalar(select(UserSettings).where(UserSettings.user_id == callback.from_user.id))
    reply_subj = f"Re: {subject}" if not subject.lower().startswith("re:") else subject
    await _do_send(callback, state, accounts[0], settings, to_email, reply_subj, text)


async def _do_send(event, state, account, settings, to_email, subject, body):
    from_name = settings.spoofing_sender if settings and settings.spoofing_sender else None
    msg = MIMEMultipart()
    sender_name = from_name or account.display_name or account.email
    msg["From"] = f"{Header(sender_name, 'utf-8')} <{account.email}>"
    msg["To"] = to_email
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    await state.clear()
    await event.answer("📤 Отправляю...")
    ok, err = await smtp_send_message(account.email, account.password, msg, event.from_user.id)
    if ok:
        try:
            await event.message.edit_text(
                f"✅ <b>Ответ отправлен!</b>\n\n<b>От:</b> {html_mod.escape(account.email)}\n<b>Кому:</b> {html_mod.escape(to_email)}\n<b>Тема:</b> {html_mod.escape(str(subject))}",
                parse_mode="HTML", reply_markup=None,
            )
        except Exception:
            await event.message.answer(f"✅ <b>Ответ отправлен!</b>\nОт: {html_mod.escape(account.email)}\nКому: {html_mod.escape(to_email)}", parse_mode="HTML")
    else:
        await event.message.answer(f"❌ Ошибка отправки: {html_mod.escape(err[:300])}\n\nПроверьте текст и попробуйте снова.")


@inbox_router.callback_query(F.data.startswith("inbox_link_"))
async def inbox_link(callback: CallbackQuery, session: AsyncSession):
    msg_id = int(callback.data.split("_")[-1])
    incoming = await session.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))
    if not incoming:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return
    bot_username = await _get_bot_username(callback.bot)
    link = f"https://t.me/{bot_username}?start=view_msg_{msg_id}"
    await callback.answer(f"ID: {msg_id}\n{link}", show_alert=True)
