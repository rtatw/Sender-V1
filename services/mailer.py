"""
mailer.py — ОБНОВЛЁННЫЙ (все 4 улучшения интегрированы)

Изменения:
  ✅ Шаг 1: SMTP Connection Pool (smtp_pool.py) — переиспользование соединений
  ✅ Шаг 2: Привязка прокси к аккаунту (proxy_binding.py)
  ✅ Шаг 3: Уникализация текста (text_spinner.py)
  ✅ Шаг 4: Улучшенный HTML-шаблон письма
"""

import asyncio
import random
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formatdate, make_msgid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import UserSettings, EmailAccount, ParsedItem, Proxy
from database.engine import async_session
from services.anti_ban import get_health, pick_best_account, save_health_to_db
from services.mail_tester import check_spam_content, calculate_spam_score, get_reputation, get_high_risk_accounts
from services.smtp_pool import get_smtp_pool
from services.proxy_binding import get_proxy_binder
from services.text_spinner import uniquify_text, uniquify_subject, uniquify_html

logger = logging.getLogger(__name__)

MAILER_AGENTS = [
    "Microsoft Outlook 16.0.16827.20166",
    "Microsoft Outlook 15.0.5589.1000",
    "Apple Mail (2.3774.400.10)",
    "Thunderbird 115.3.1",
    "YahooMailBasic/2.0",
    "Gmail/2023.09.17",
]


def _make_progress_bar(percent: int, length: int = 20) -> str:
    filled = int(length * percent / 100)
    return f"[{'█' * filled}{'░' * (length - filled)}] {percent}%"


# ─── Шаг 4: Улучшенный HTML-шаблон ──────────────────────────────────────────

def _build_html(body_text: str, recipient: str = "", custom_html_template: str = "") -> str:
    """Генерирует HTML-тело письма.

    :param body_text: уже за-spin-енный plain text (будет экранирован).
    :param recipient: email получателя — для ZW-подписи.
    :param custom_html_template: HTML-шаблон из Template.html_template.
        Если задан — body_text подставляется в маркер {{body}}.
        Если пустой — используется default-шаблон с таблицами.
    """
    import html as html_lib
    escaped = html_lib.escape(body_text)

    # ✅ MED-36: ZW chars только в HTML, не в plain text
    zw_sig = ""
    if recipient:
        from services.text_spinner import inject_invisible_signature
        zw_sig = inject_invisible_signature("", recipient)

    if custom_html_template and "{{body}}" in custom_html_template:
        # ✅ MED-9: используем кастомный HTML-шаблон пользователя
        # escaped уже безопасен, zw_sig содержит только ZW chars (тоже безопасен)
        return custom_html_template.replace("{{body}}", escaped + zw_sig)

    # Default HTML-шаблон
    paragraphs = escaped.split("\n\n")
    html_paragraphs = ""
    for para in paragraphs:
        para = para.strip()
        if para:
            inner = para.replace("\n", "<br>\n")
            html_paragraphs += f"    <p style=\"margin: 0 0 14px 0;\">{inner}</p>\n"

    body_content = html_paragraphs + zw_sig
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
</head>
<body style="margin:0; padding:0; background:#f5f5f5; font-family: Arial, sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f5f5f5; padding: 20px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
               style="background:#ffffff; border-radius:4px; padding: 32px 40px;
                      box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
          <tr>
            <td style="font-size:15px; line-height:1.6; color:#333333;">
{body_content}
            </td>
          </tr>
          <tr>
            <td style="padding-top:20px; border-top:1px solid #eeeeee;
                       font-size:12px; color:#999999; text-align:center;">
              Если это письмо попало к вам по ошибке — просто проигнорируйте его.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _build_message(sender_email, sender_name, recipient, subject, body,
                   reply_to=None, custom_html_template: str = ""):
    """Строит MIME-письмо с полноценным HTML и всеми нужными заголовками."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{Header(sender_name, 'utf-8')} <{sender_email}>"
    msg["To"] = recipient
    msg["Subject"] = Header(subject, "utf-8")
    msg["Reply-To"] = reply_to or sender_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender_email.split("@")[-1])
    msg["MIME-Version"] = "1.0"
    msg["X-Mailer"] = random.choice(MAILER_AGENTS)
    msg["X-Priority"] = "3"
    msg["Importance"] = "Normal"
    # ✅ MED-8: Precedence: bulk снижает штраф в SpamAssassin для массовой рассылки
    msg["Precedence"] = "bulk"

    # ✅ List-Unsubscribe — обязателен для Gmail mass-mail
    msg["List-Unsubscribe"] = f"<mailto:{sender_email}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # plain text часть (БЕЗ ZW chars — см. text_spinner.uniquify_text)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # ✅ HTML часть с ZW-подписью (уникализация для спам-фильтров).
    # ✅ MED-9: если задан custom_html_template — используется он.
    msg.attach(MIMEText(_build_html(body, recipient, custom_html_template), "html", "utf-8"))

    return msg


# ─── Применение переменных шаблона ──────────────────────────────────────────

def _safe_template_value(value: str | None) -> str:
    """Экранирует значение для подстановки в шаблон.

    ВАЖНО (MED-38): ранее _apply_variables делал text.replace("{nick}", nick)
    без экранирования. Если item.person_name содержал HTML-теги (например,
    пользователь ввёл <script>alert(1)</script> как имя), они попадали
    в письмо. Хотя _build_html вызывает html.escape(body_text), это
    экранировало уже после подстановки — что OK для plain text, но
    ломает шаблоны с HTML-разметкой.

    Решение: экранируем только dynamic-значения (item.title, item.nickname),
    но не статические переменные шаблона ({email}, {nick} из настроек —
    они задаются админом и доверенные).
    """
    if not value:
        return ""
    import html as html_lib
    return html_lib.escape(str(value))


def _apply_variables(text: str, item, email: str, nick: str | None) -> str:
    """Подставляет переменные шаблона.

    Значения из item.* экранируются (могут содержать пользовательский ввод
    с сайта-источника). {email} и {nick} не экранируются — это доверенные
    значения, заданные пользователем в настройках бота.
    """
    if nick:
        # nick задаётся пользователем в настройках бота — доверенное значение
        text = text.replace("{nick}", nick).replace("{name}", nick)
    # email получателя тоже доверенный
    text = text.replace("{email}", email)
    if item:
        # ✅ MED-38: экранируем значения из парсинга (недоверенный источник)
        text = text.replace("@товар", _safe_template_value(item.title))
        text = text.replace("@цена", _safe_template_value(item.price))
        text = text.replace("@ник", _safe_template_value(item.person_name or item.nickname))
        text = text.replace("@ссылка", _safe_template_value(item.link))
    return text


# ─── Отправка через пул с привязанным прокси ─────────────────────────────────

async def _pool_send(account: EmailAccount, msg, user_id: int, proxies: list) -> tuple[bool, str]:
    """
    Отправка письма через SMTP Connection Pool.

    ВАЖНО (CRIT-2): ранее здесь был сломанный код установки
    s.sock = sock; s.file = sock.makefile("rb"); s.login(...) —
    это вызывало SMTPServerDisconnected. Теперь используем
    services.proxy_connection._ProxySMTP(_SSL), который корректно
    переопределяет _get_socket и вызывает connect().

    ВАЖНО (HIGH-4): теперь SMTP pool кеширует не только прямые, но и
    прокси-соединения (по ключу email|proxy_id|host|port). Это даёт
    ускорение 3-8 сек на каждом письме при массовой рассылке через
    прокси — TCP-туннель и SMTP handshake делаются один раз, затем
    переиспользуются до MAX_SENDS_PER_CONNECTION=80 писем.
    """
    from services.proxy_connection import _get_smtp_host
    pool = get_smtp_pool()
    binder = get_proxy_binder()

    # Шаг 2: получаем прокси привязанный к этому аккаунту (асинхронно — БД)
    proxy = await binder.bind(account.email, proxies)

    host = _get_smtp_host(account.email)
    port, use_ssl = 465, True

    # ✅ HIGH-4: единый путь через пул (с прокси или без — пул сам разберётся)
    return await pool.send(
        email=account.email,
        password=account.password,
        host=host,
        port=port,
        use_ssl=use_ssl,
        msg=msg,
        proxy=proxy,  # None для прямого, Proxy для прокси-соединения
    )


# ─── Основной класс Mailer ───────────────────────────────────────────────────

class Mailer:
    def __init__(self, user_id, chat_id, message_id, bot):
        self.user_id = user_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.bot = bot
        self._cancelled = False
        self._custom_html_template: str = ""  # ✅ MED-9: кастомный HTML из Template

    def cancel(self):
        self._cancelled = True

    async def get_cancel_keyboard(self):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⏹️ Отмена", callback_data="mailer_cancel")]]
        )

    async def run(self, recipients, template_text, subject_text,
                  custom_html_template: str = ""):
        """Запуск рассылки с атомарной установкой mailer_lock.

        ВАЖНО (CRIT-31): ранее был race condition:
            SELECT mailer_lock  →  if False → UPDATE mailer_lock=True
        Между SELECT и UPDATE другой процесс мог тоже прочитать False и
        начать рассылку. Теперь — атомарный conditional UPDATE:
            UPDATE ... SET mailer_lock=True, mailer_lock_at=now()
            WHERE user_id=? AND (mailer_lock=False OR mailer_lock_at < now()-1h)
        Если обновилось 0 строк — значит lock уже занят, выходим.

        ВАЖНО (MED-33): lock имеет TTL 1 час. Если процесс упал и не снял
        lock, через час другой запуск сможет его перезаписать (watchdog).
        """
        import datetime
        from sqlalchemy import update as sql_update
        from services.state import _active_mailers, get_mailer_lock

        self._custom_html_template = custom_html_template

        lock = get_mailer_lock(self.user_id)
        async with lock:
            # ✅ CRIT-31: атомарная установка lock с TTL
            ttl_ago = datetime.datetime.now() - datetime.timedelta(hours=1)
            async with async_session() as s:
                result = await s.execute(
                    sql_update(UserSettings)
                    .where(
                        UserSettings.user_id == self.user_id,
                        # Либо lock не установлен, либо устарел (>1 часа)
                        (UserSettings.mailer_lock == False) |
                        (UserSettings.mailer_lock_at < ttl_ago),
                    )
                    .values(mailer_lock=True, mailer_lock_at=datetime.datetime.now())
                )
                await s.commit()
                updated = result.rowcount

            if not updated:
                # Lock уже занят другим процессом
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id, message_id=self.message_id,
                        text="⚠️ Уже идёт рассылка. Если это ошибка — отправьте /reset.",
                    )
                except Exception:
                    pass
                return

            _active_mailers[self.user_id] = self
            try:
                async with async_session() as session:
                    await self._do_mail(session, recipients, template_text, subject_text)
            except Exception as e:
                logger.exception("Mailer error: %s", e)
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id, message_id=self.message_id,
                        text=f"❌ Ошибка рассылки: {e}"
                    )
                except Exception:
                    pass
            finally:
                _active_mailers.pop(self.user_id, None)
                # ✅ Снимаем lock атомарно
                async with async_session() as session:
                    await session.execute(
                        sql_update(UserSettings)
                        .where(UserSettings.user_id == self.user_id)
                        .values(mailer_lock=False, mailer_lock_at=None)
                    )
                    await session.commit()

    async def _do_mail(self, session, recipients, template_text, subject_text):
        settings = await session.scalar(
            select(UserSettings).where(UserSettings.user_id == self.user_id)
        )
        accounts = list(await session.scalars(
            select(EmailAccount).where(
                EmailAccount.user_id == self.user_id,
                EmailAccount.is_valid == True,
                # ✅ NEW: исключаем аккаунты на паузе
                EmailAccount.is_paused == False,
            )
        ))
        if not accounts:
            await self.bot.edit_message_text(
                chat_id=self.chat_id, message_id=self.message_id, text="❌ Нет активных аккаунтов."
            )
            return

        # ✅ HIGH-5: убеждаемся что health для всех аккаунтов загружен из БД.
        # ✅ MED-29 fix: bulk-load одним запросом, а не N запросов.
        from services.anti_ban import load_all_health_from_db
        await load_all_health_from_db(self.user_id)

        # ✅ Шаг 2: Загружаем все прокси пользователя (mailer-тип)
        proxies = list(await session.scalars(
            select(Proxy).where(
                Proxy.user_id == self.user_id,
                Proxy.is_active == True,
                Proxy.status == "alive",
            )
        ))

        min_delay = settings.timing_min if settings else 5
        max_delay = settings.timing_max if settings else 15
        # ✅ HIGH-6: принудительно занижаем скорость до доменных лимитов.
        # Пользователь мог поставить 1 сек в настройках — это убило бы gmail-аккаунт.
        from services.anti_ban import get_domain_limits
        for acc in accounts:
            dl = get_domain_limits(acc.email)
            min_delay = max(min_delay, dl["min_delay"])
            max_delay = max(max_delay, dl["max_delay"])
        # Гарантируем, что max >= min
        if max_delay < min_delay:
            max_delay = min_delay
        from_name = settings.spoofing_sender if settings and settings.spoofing_sender else None
        nick = settings.spoofing_nick if settings and settings.spoofing_nick else None
        subj_override = settings.spoofing_theme if settings and settings.spoofing_theme else None

        items = list(await session.scalars(
            select(ParsedItem).where(
                ParsedItem.user_id == self.user_id,
                ParsedItem.status == "done",
                ParsedItem.found_email != "",
            )
        ))
        item_map = {it.found_email: it for it in items}

        total = len(recipients)
        sent = 0
        failed = 0

        for idx, recipient in enumerate(recipients):
            if self._cancelled:
                break

            delay = random.randint(min_delay, max_delay)

            # Выбор аккаунта — кто меньше разослал
            account = pick_best_account(accounts, user_id=self.user_id)
            if account is None:
                account = accounts[idx % len(accounts)]

            item_obj = item_map.get(recipient)

            # Применяем переменные шаблона
            body = _apply_variables(template_text, item_obj, recipient, nick)
            subj = _apply_variables(subj_override or subject_text, item_obj, recipient, nick)

            # ✅ Шаг 3: Уникализация текста и темы
            body = uniquify_text(body, recipient, use_invisible=True)
            subj = uniquify_subject(subj, recipient)

            # Проверка спам-контента — блокируем если есть триггер-слова
            spam_issues = check_spam_content(subj) + check_spam_content(body)
            spam_score = calculate_spam_score(subj) + calculate_spam_score(body)
            if spam_issues:
                trigger_words = [i.get("word", "") for i in spam_issues if i["type"] == "trigger_word"]
                if trigger_words:
                    failed += 1
                    # ✅ MED-7: обновляем репутацию аккаунта при спам-блокировке
                    get_reputation(account.email).record_send_result(
                        False, error_type="spam_blocked", spam_score=spam_score
                    )
                    try:
                        await self.bot.send_message(self.chat_id,
                            f"❌ <b>Письмо для {recipient} не отправлено</b>\n"
                            f"Причина: обнаружены спам-слова: «{', '.join(trigger_words[:5])}»",
                            parse_mode="HTML")
                    except Exception: pass
                    await asyncio.sleep(delay)
                    continue

            try:
                sender_name = from_name or account.display_name or account.email.split("@")[0]

                # ✅ Шаг 4: _build_message теперь с полноценным HTML
                # ✅ MED-9: передаём custom_html_template из Template
                msg = _build_message(
                    account.email, sender_name, recipient, subj, body,
                    reply_to=account.email,
                    custom_html_template=self._custom_html_template,
                )

                # ✅ Шаг 1+2: отправка через пул с привязанным прокси
                ok, err = await _pool_send(account, msg, self.user_id, proxies)

                # ✅ HIGH-5: регистрируем результат в AccountHealth
                health = get_health(account.email, user_id=self.user_id)
                if ok:
                    sent += 1
                    health.record_success()
                    get_reputation(account.email).record_send_result(True, spam_score=spam_score)
                else:
                    logger.warning("Send fail %s → %s: %s", account.email, recipient, err[:100])
                    failed += 1
                    health.record_error()
                    get_reputation(account.email).record_send_result(False, error_type=err[:50], spam_score=spam_score)

                # ✅ HIGH-5: сохраняем health в БД каждые 5 отправок
                if (sent + failed) % 5 == 0:
                    await save_health_to_db(account.email)

            except Exception as e:
                logger.warning("Send exception %s: %s", recipient, e)
                failed += 1
                try:
                    get_reputation(account.email).record_send_result(False, error_type=str(e)[:50])
                except Exception:
                    pass

            # Предупреждение о высоком риске каждые 20 отправок
            if sent > 0 and sent % 20 == 0:
                for ra in get_high_risk_accounts():
                    if ra.needs_warning():
                        logger.warning("HIGH SPAM RISK: %s (rate: %.0f%%)", ra.email, ra.success_rate * 100)

            progress = int((idx + 1) / total * 100) if total else 100
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=(
                        f"📤 <b>Рассылка</b>\n{_make_progress_bar(progress)}\n\n"
                        f"✅ Отправлено: <b>{sent}</b>\n"
                        f"❌ Ошибок: <b>{failed}</b>\n"
                        f"📬 Осталось: <b>{total - idx - 1}</b>\n"
                        f"📧 Аккаунт: <code>{account.email}</code>\n"
                        f"🌐 Прокси: <code>{await self._proxy_label(proxies, account.email)}</code>\n"
                        f"⏱ Пауза: {delay} сек."
                    ),
                    parse_mode="HTML",
                    reply_markup=await self.get_cancel_keyboard(),
                )
            except Exception:
                pass

            await asyncio.sleep(delay)

        status = "⏹️ Остановлена" if self._cancelled else "✅ Завершена"
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=(
                    f"{status}\n\n📊 <b>Итог рассылки:</b>\n"
                    f"Всего: <b>{total}</b>\n"
                    f"✅ Отправлено: <b>{sent}</b>\n"
                    f"❌ Ошибок: <b>{failed}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    async def _proxy_label(self, proxies: list, email: str) -> str:
        """Отображает прокси привязанный к аккаунту."""
        if not proxies:
            return "прямое"
        binder = get_proxy_binder()
        proxy = await binder.bind(email, proxies)
        if proxy:
            return f"{proxy.host}:{proxy.port} ({getattr(proxy, 'proxy_type', 'socks5')})"
        return "прямое"
