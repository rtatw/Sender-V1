import asyncio
import html as html_mod
import logging
import re
from collections import OrderedDict
from sqlalchemy import select, delete
from database.models import UserSettings, EmailAccount, IncomingMessage, ReceiveEmail, GlobalSettings
from database.engine import async_session
from services.anti_ban import safe_imap_fetch
from services.email_cleaner import clean_email_body
from keyboards.inbox_kb import get_inbox_message_keyboard

logger = logging.getLogger(__name__)

BOUNCE_SENDERS = ["mailer-daemon@","mail-daemon@","postmaster@","delivery@","mailerdaemon@","noreply+bounce@"]

BOUNCE_SUBJECT_KW = [
    "delivery status","undelivered","returned mail","failure notice","mail delivery","delivery failure",
    "delivery status notification","bounce","mailbox not found","address rejected","permanent failure",
    "undeliverable","could not be delivered","message not delivered",
    "unzustellbar","zustellungsfehler","lieferfehler","nachricht konnte nicht zugestellt","rücksendung",
    "niedostarczona","dostawa nie powiodła","wiadomość nie została dostarczona","błąd dostarczenia",
    "550","5.2.1","5.1.1","5.4.1",
]

BOUNCE_BODY_CODES = [
    "550 ","551 ","552 ","553 ","554 ","5.1.1","5.1.2","5.2.1","5.2.2","5.4.1",
    "mailbox disabled","mailbox not found","user unknown","no such user","does not exist",
    "invalid address","account does not exist","recipient rejected",
]

NOT_BOUNCE_SENDERS = ["noreply@","no-reply@","newsletter@","info@","support@","komunikaty@","mailing_reklamowy@","marketing@"]

# ✅ CRIT-16: лимиты для backoff при ошибках IMAP
MAX_CONSECUTIVE_ERRORS = 3
BACKOFF_BASE_SECONDS = 30      # первая ошибка → ждём 30 сек
BACKOFF_MAX_SECONDS = 1800     # потолок 30 минут


def _is_bounce(from_email, subject, body):
    lf = from_email.lower(); ls = subject.lower(); lb = body.lower()[:2000]
    for kw in NOT_BOUNCE_SENDERS:
        if kw in lf: return False
    score = 0
    for kw in BOUNCE_SENDERS:
        if kw in lf: score += 2; break
    for kw in BOUNCE_SUBJECT_KW:
        if kw in ls: score += 1; break
    for kw in BOUNCE_BODY_CODES:
        if kw in lb: score += 1; break
    return score >= 2


def _extract_bounced_email(body_text, user_accounts):
    for c in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", body_text[:2000]):
        if c.lower() in user_accounts: return c
    return None


async def _fetch_emails(acc, user_id, limit=5):
    """Возвращает (emails, error). emails=[] + error=None — писем нет,
    emails=[] + error=str — ошибка подключения (для backoff)."""
    err = None
    try:
        emails = await safe_imap_fetch(acc.email, acc.password, user_id, limit)
        if emails:
            return [dict(e, **{"_proto":"IMAP"}) for e in emails], None
    except Exception as e:
        err = f"IMAP: {e}"
    # POP3 fallback
    try:
        from services.proxy_connection import pop3_fetch_parsed
        emails = await pop3_fetch_parsed(acc.email, acc.password, user_id, limit)
        if emails:
            return [dict(e, **{"_proto":"POP3"}) for e in emails], None
    except Exception as e:
        err = (err + " | POP3: " + str(e)) if err else f"POP3: {e}"
    return [], err


class InboxWatcher:
    """Фоновый наблюдатель за входящими письмами.

    Изменения (CRIT-16/CRIT-17):
      ✅ Экспоненциальный backoff при ошибках IMAP (30 → 60 → 120 → ... до 30 мин).
      ✅ При MAX_CONSECUTIVE_ERRORS подряд — помечаем EmailAccount.is_valid=False
        и пишем пользователю, что аккаунт нужно перепроверить.
      ✅ Обрезка known_uids через OrderedDict (FIFO) — больше не теряем
        последние UID'ы и не дублируем письма.
    """
    MAX_KNOWN_UIDS = 1000  # потолок размера in-memory кеша

    def __init__(self, bot):
        self.bot = bot
        self._tasks = {}
        # user_id -> OrderedDict[uid, True] (FIFO, последние — в конце)
        self._known_uids: dict[int, OrderedDict] = {}
        # user_id -> {email: consecutive_errors}
        self._acc_errors: dict[int, dict[str, int]] = {}

    async def start_for_user(self, user_id, chat_id):
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
        await self._load_known(user_id)
        self._tasks[user_id] = asyncio.create_task(self._watch(user_id, chat_id))

    def stop_for_user(self, user_id):
        if user_id in self._tasks:
            self._tasks[user_id].cancel()
            del self._tasks[user_id]

    async def _load_known(self, user_id):
        try:
            async with async_session() as s:
                msgs = list(await s.scalars(
                    select(IncomingMessage.imap_uid).where(IncomingMessage.user_id == user_id)
                ))
                od = OrderedDict()
                # Загружаем последние MAX_KNOWN_UIDS из БД (по id ascending — последние добавлены последними)
                for m in msgs:
                    if m:
                        od[m] = True
                        if len(od) > self.MAX_KNOWN_UIDS:
                            od.popitem(last=False)  # удаляем самый старый
                self._known_uids[user_id] = od
        except Exception as e:
            logger.warning("InboxWatcher: _load_known failed for %s: %s", user_id, e)
            self._known_uids[user_id] = OrderedDict()

    async def _watch(self, user_id, chat_id):
        while True:
            try:
                await self._check(user_id, chat_id)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Watcher error %s: %s", user_id, e)

            # Интервал берём из глобальных настроек (задаёт админ)
            try:
                async with async_session() as s:
                    gs = await s.scalar(select(GlobalSettings).where(GlobalSettings.id == 1))
                    interval = gs.receive_check_interval if gs else 30
            except Exception:
                interval = 30
            await asyncio.sleep(max(10, interval))

    async def _check(self, user_id, chat_id):
        async with async_session() as session:
            accounts = list(await session.scalars(
                select(EmailAccount).where(
                    EmailAccount.user_id == user_id,
                    EmailAccount.is_valid == True,
                )
            ))
            if not accounts:
                return
            user_account_emails = {a.email.lower() for a in accounts}
            settings = await session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
            known = self._known_uids.setdefault(user_id, OrderedDict())
            acc_errors = self._acc_errors.setdefault(user_id, {})

            for acc in accounts:
                emails, err = await _fetch_emails(acc, user_id, 5)
                if err:
                    # ✅ CRIT-16: считаем ошибки и применяем backoff
                    cnt = acc_errors.get(acc.email, 0) + 1
                    acc_errors[acc.email] = cnt
                    logger.warning("InboxWatcher: %s — ошибка %d/%d: %s",
                                   acc.email, cnt, MAX_CONSECUTIVE_ERRORS, err[:120])
                    if cnt >= MAX_CONSECUTIVE_ERRORS:
                        # Помечаем аккаунт невалидным — пользователь увидит в /health
                        acc.is_valid = False
                        await session.commit()
                        try:
                            await self.bot.send_message(
                                chat_id,
                                f"❌ <b>Аккаунт отключён</b>\n"
                                f"<code>{html_mod.escape(acc.email)}</code> — IMAP не отвечает "
                                f"({cnt} раз подряд). Проверьте пароль или добавьте прокси.",
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                    continue

                # Успешная проверка — сбрасываем счётчик ошибок
                acc_errors.pop(acc.email, None)

                for em in emails:
                    uid = em.get("imap_uid", "")
                    if not uid:
                        continue
                    if uid in known:
                        continue
                    # Добавляем в known (FIFO) — если превысили лимит, удаляем самый старый
                    known[uid] = True
                    if len(known) > self.MAX_KNOWN_UIDS:
                        known.popitem(last=False)

                    from_raw = em.get("from", "")
                    subject_raw = em.get("subject", "")
                    body_text = em.get("body", "")
                    is_bounce = _is_bounce(from_raw, subject_raw, body_text)

                    incoming = IncomingMessage(
                        user_id=user_id,
                        account_email=acc.email,
                        from_email=from_raw,
                        subject=subject_raw,
                        body=body_text,
                        imap_uid=uid,
                        is_bounce=is_bounce,
                    )
                    session.add(incoming)
                    await session.commit()
                    await session.refresh(incoming)

                    if is_bounce:
                        bounced = _extract_bounced_email(body_text, user_account_emails)
                        if bounced and settings and settings.control_block:
                            await session.execute(
                                delete(EmailAccount).where(
                                    EmailAccount.user_id == user_id,
                                    EmailAccount.email == bounced,
                                )
                            )
                            await session.commit()
                            # ✅ HIGH-18: чистим in-memory реестры при удалении аккаунта
                            from services.anti_ban import _health_registry
                            from services.mail_tester import _reputation_registry
                            _health_registry.pop(bounced, None)
                            _reputation_registry.pop(bounced, None)

                            await self.bot.send_message(
                                chat_id,
                                f"⚠️ <b>Bounce:</b> <code>{html_mod.escape(bounced)}</code> удалён.",
                                parse_mode="HTML",
                            )
                        elif bounced:
                            await self.bot.send_message(
                                chat_id,
                                f"⚠️ <b>Bounce:</b> <code>{html_mod.escape(bounced)}</code> — удаление выкл.",
                                parse_mode="HTML",
                            )
                        continue

                    from_e = html_mod.escape(from_raw)
                    subject_e = html_mod.escape(subject_raw)
                    bc = clean_email_body(body_text)[0] if body_text else ""
                    bs = html_mod.escape(bc) if bc else "(пусто)"
                    text = (
                        f"⚡️ <code>{html_mod.escape(acc.email)}</code> ← <b>{from_e}</b>"
                        + (f"\n<b>{subject_e}</b>" if subject_e else "")
                        + f"\n<blockquote>{bs[:500]}</blockquote>"
                    )
                    try:
                        sent = await self.bot.send_message(
                            chat_id, text[:4096], parse_mode="HTML",
                            reply_markup=get_inbox_message_keyboard(incoming.id),
                        )
                        incoming.telegram_msg_id = sent.message_id
                        await session.commit()
                        # ✅ HIGH-19: убрано автоматическое pin_chat_message —
                        # Telegram позволяет только 1 pin, и засоряло чат.
                    except Exception as e:
                        logger.warning("Failed to send inbox msg: %s", e)

            # ✅ CRIT-17: обрезка теперь не нужна — делается инкрементально выше


inbox_watcher = None


def get_watcher(bot):
    global inbox_watcher
    if inbox_watcher is None:
        inbox_watcher = InboxWatcher(bot)
    return inbox_watcher
