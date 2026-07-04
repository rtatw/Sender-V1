import logging
import asyncio
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, F
from aiogram.types import BotCommand, Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage

from sqlalchemy import select, func, case

from config import settings as app_settings, ADMIN_ID
from database.engine import engine, async_session
from database.models import Base, UserSettings, ParsedItem, EmailAccount, IncomingMessage, Proxy, ProxyBinding

from middlewares.db_middleware import DatabaseMiddleware
from middlewares.throttling import ThrottlingMiddleware
from middlewares.fsm_timeout import FSMTimeoutMiddleware

from keyboards.main_reply import get_main_reply_keyboard

from services.state import _active_hunters, _active_mailers, global_state

from handlers.file_handler import file_router
from handlers.settings import settings_router
from handlers.settings_domains import domains_router
from handlers.settings_spoofing import spoofing_router
from handlers.settings_admin import admin_settings_router
from handlers.settings_misc import misc_settings_router
from handlers.templates import templates_router
from handlers.subjects import subjects_router
from handlers.proxies import proxies_router
from handlers.emails import emails_router
from handlers.timings import timings_router
from handlers.inbox_handler import inbox_router
from handlers.card import card_router

from utils.exceptions import global_error_handler

logger = logging.getLogger(__name__)


def _safe_html_truncate(text: str, max_len: int) -> str:
    """Truncate text without breaking HTML tags.

    ВАЖНО (MED-35): предыдущая реализация считала open_tags и closed_tags
    отдельно и закрывала только разницу. Это работало для простых случаев,
    но ломалось на вложенных тегах и тегах, обрезанных внутри атрибутов.
    Теперь используем стек open-тегов в порядке открытия — закрываем
    в обратном порядке. Также поддерживаем self-closing void-теги.
    """
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    import re

    # Void-теги (HTML5) — не нужно закрывать
    void_tags = {"br", "img", "hr", "input", "meta", "link", "area", "base",
                 "col", "embed", "param", "source", "track", "wbr"}

    # Сканируем truncated и собираем стек открытых тегов
    open_stack: list[str] = []
    tag_pattern = re.compile(r'<(/?)(\w+)[^>]*?(/?)>', re.IGNORECASE)

    for m in tag_pattern.finditer(truncated):
        is_closing = m.group(1) == "/"
        tag_name = m.group(2).lower()
        is_self_closing = m.group(3) == "/"

        if tag_name in void_tags or is_self_closing:
            continue

        if is_closing:
            # Удаляем из стека последний такой тег
            for i in range(len(open_stack) - 1, -1, -1):
                if open_stack[i] == tag_name:
                    open_stack.pop(i)
                    break
        else:
            open_stack.append(tag_name)

    # Закрываем в обратном порядке
    for tag_name in reversed(open_stack):
        truncated += f"</{tag_name}>"
    return truncated


async def on_startup(bot: Bot) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified.")

    # ✅ HIGH-23: загружаем дневной лимит из GlobalSettings в память
    try:
        from database.repository import get_global_settings
        from services.anti_ban import set_daily_limit
        async with async_session() as sess:
            gs = await get_global_settings(sess)
            if gs and gs.daily_limit:
                set_daily_limit(gs.daily_limit)
                logger.info("Daily limit loaded from GlobalSettings: %d", gs.daily_limit)
    except Exception as e:
        logger.warning("Failed to load daily_limit from GlobalSettings: %s", e)

    # ✅ HIGH-5: загружаем health для всех email-аккаунтов из БД (bulk-load)
    try:
        from services.anti_ban import load_all_health_from_db
        from database.models import EmailAccount
        async with async_session() as sess:
            accs = list(await sess.scalars(select(EmailAccount)))
        # Group by user_id и загружаем bulk
        from collections import defaultdict
        by_user = defaultdict(list)
        for acc in accs:
            by_user[acc.user_id].append(acc)
        total_loaded = 0
        for uid, user_accs in by_user.items():
            total_loaded += await load_all_health_from_db(uid)
        logger.info("EmailHealth bulk-loaded: %d accounts", total_loaded)
    except Exception as e:
        logger.warning("Failed to bulk-load EmailHealth: %s", e)

    commands = [
        BotCommand(command="start", description="Запустить бота / главное меню"),
        BotCommand(command="stop", description="Остановить текущую рассылку"),
        BotCommand(command="check", description="Проверить входящие письма"),
        BotCommand(command="debug", description="Вкл/выкл режим диагностики"),
        BotCommand(command="admin", description="Админ-панель"),
        BotCommand(command="health", description="Здоровье аккаунтов и пула"),
    ]
    await bot.set_my_commands(commands)
    logger.info("Bot commands set.")

    from services.inbox_watcher import get_watcher
    watcher = get_watcher(bot)

    from database.repository import get_all_user_ids
    async with async_session() as sess:
        user_ids = await get_all_user_ids(sess)

    sem = asyncio.Semaphore(10)

    async def _start_one(uid):
        async with sem:
            try:
                await watcher.start_for_user(uid, uid)
                logger.info("Watcher started for user %s", uid)
            except Exception as e:
                logger.warning("Failed to start watcher for %s: %s", uid, e)

    await asyncio.gather(*[_start_one(uid) for uid in user_ids])


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, app_settings.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                "bot.log",
                maxBytes=app_settings.LOG_MAX_BYTES,
                backupCount=app_settings.LOG_BACKUP_COUNT,
                encoding="utf-8",
            ),
        ],
    )

    if not app_settings.ENCRYPTION_KEY:
        logger.critical(
            "ENCRYPTION_KEY is not set in .env! "
            'Generate one: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
        return

    bot = Bot(token=app_settings.BOT_TOKEN)
    global_state.bot = bot

    if app_settings.REDIS_URL:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis.asyncio import Redis
        redis = Redis.from_url(app_settings.REDIS_URL)
        storage = RedisStorage(redis)
        logger.info("FSM storage: Redis (%s)", app_settings.REDIS_URL)
    else:
        storage = MemoryStorage()
        logger.info("FSM storage: Memory (no REDIS_URL)")

    dp = Dispatcher(storage=storage)
    dp.errors.register(global_error_handler)
    dp.update.middleware(DatabaseMiddleware())
    dp.update.middleware(ThrottlingMiddleware(rate=0.3))
    dp.update.middleware(FSMTimeoutMiddleware(timeout=app_settings.FSM_TIMEOUT_SECONDS))

    dp.include_router(file_router)
    dp.include_router(settings_router)
    dp.include_router(domains_router)
    dp.include_router(spoofing_router)
    dp.include_router(admin_settings_router)
    dp.include_router(misc_settings_router)
    dp.include_router(templates_router)
    dp.include_router(subjects_router)
    dp.include_router(proxies_router)
    dp.include_router(emails_router)
    dp.include_router(timings_router)
    dp.include_router(inbox_router)
    dp.include_router(card_router)

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        args = message.text.split()
        if len(args) > 1 and args[1].startswith("view_msg_"):
            try:
                from database.models import IncomingMessage
                from keyboards.inbox_kb import get_inbox_message_keyboard
                from services.email_cleaner import clean_email_body
                import html as html_mod

                async with async_session() as s:
                    msg_id = int(args[1].split("_")[-1])
                    incoming = await s.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))
                    if incoming:
                        from_e = html_mod.escape(incoming.from_email)
                        subject_e = html_mod.escape(incoming.subject)
                        body_text = incoming.body
                        body_preview = html_mod.escape(
                            clean_email_body(body_text)[0] if body_text else "(пустое письмо)"
                        )
                        await message.answer(
                            f"📨 <b>Письмо #{msg_id}</b>\n\n"
                            f"<b>От:</b> {from_e}\n<b>Тема:</b> {subject_e}\n\n"
                            f"<b>Текст:</b>\n<blockquote>{body_preview}</blockquote>",
                            parse_mode="HTML",
                            reply_markup=get_inbox_message_keyboard(msg_id),
                        )
                        return
            except Exception:
                pass

        # Reset stale locks on /start (только если они устарели — TTL > 1 часа)
        # ✅ MED-34: раньше /start безусловно сбрасывал mailer_lock, даже если
        # рассылка активна. Теперь — только если lock устарел (>1 часа),
        # значит процесс действительно упал.
        import datetime
        from sqlalchemy import update as _upd
        async with async_session() as sess:
            ttl_ago = datetime.datetime.now() - datetime.timedelta(hours=1)
            # Сбрасываем только устаревшие lock-и
            await sess.execute(
                _upd(UserSettings)
                .where(
                    UserSettings.user_id == message.from_user.id,
                    (UserSettings.parser_lock == True) | (UserSettings.mailer_lock == True),
                )
                .where(
                    (UserSettings.parser_lock_at == None) |
                    (UserSettings.parser_lock_at < ttl_ago) |
                    (UserSettings.mailer_lock_at == None) |
                    (UserSettings.mailer_lock_at < ttl_ago)
                )
                .values(parser_lock=False, mailer_lock=False,
                        parser_lock_at=None, mailer_lock_at=None)
            )
            await sess.commit()

        from database.repository import get_or_create_settings
        async with as2() as sess2:
            await get_or_create_settings(
                sess2, message.from_user.id,
                display_name=message.from_user.first_name or "",
                username=message.from_user.username or "",
            )

        await message.answer(
            MESSAGES["settings_header"],
            parse_mode="HTML",
            reply_markup=get_settings_menu_keyboard(message.from_user.id == ADMIN_ID),
        )

        from services.inbox_watcher import get_watcher
        watcher = get_watcher(bot)
        await watcher.start_for_user(message.from_user.id, message.chat.id)

    @dp.message(Command("stop", "Остановить"))
    async def cmd_stop(message: Message):
        uid = message.from_user.id
        stopped = []
        hunter = _active_hunters.pop(uid, None)
        if hunter:
            hunter.cancel()
            stopped.append("Email Hunter")
        mailer = _active_mailers.pop(uid, None)
        if mailer:
            mailer.cancel()
            stopped.append("Рассылка")
        if stopped:
            await message.answer(f"⏹️ Остановлено: {', '.join(stopped)}")
        else:
            await message.answer("ℹ️ Нет активных процессов.")

    @dp.message(Command("check"))
    async def cmd_check(message: Message):
        import html as h
        from services.proxy_connection import imap_fetch_parsed
        from services.email_cleaner import clean_email_body
        from keyboards.inbox_kb import get_inbox_message_keyboard

        async with async_session() as s:
            accounts = list(await s.scalars(
                select(EmailAccount).where(
                    EmailAccount.user_id == message.from_user.id,
                    EmailAccount.is_valid == True,
                )
            ))
            if not accounts:
                await message.answer("❌ Нет валидных аккаунтов. Добавьте почту в Меню -> Почты.")
                return

            status_msg = await message.answer("📥 Проверяю входящие...")
            total_new = 0

            known_uids_result = await s.scalars(
                select(IncomingMessage.imap_uid).where(IncomingMessage.user_id == message.from_user.id)
            )
            known_uids: set[str] = set(known_uids_result.all())

            for acc in accounts:
                try:
                    emails = await asyncio.wait_for(
                        imap_fetch_parsed(acc.email, acc.password, message.from_user.id, 10),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("IMAP timeout for %s", acc.email)
                    continue
                except Exception as e:
                    logger.debug("IMAP fetch failed for %s: %s", acc.email, e, exc_info=True)
                    continue

                for em in emails:
                    uid = em.get("imap_uid", "")
                    if uid in known_uids:
                        continue
                    known_uids.add(uid)

                    from_raw = em.get("from", "")
                    subject_raw = em.get("subject", "")
                    body_text = em.get("body", "")

                    body_preview = h.escape(clean_email_body(body_text)[0]) if body_text else "(пустое письмо)"

                    incoming = IncomingMessage(
                        user_id=message.from_user.id,
                        account_email=acc.email,
                        from_email=from_raw,
                        subject=subject_raw,
                        body=body_text,
                        imap_uid=uid,
                    )
                    s.add(incoming)
                    await s.commit()
                    await s.refresh(incoming)

                    from_e = h.escape(from_raw)
                    subject_e = h.escape(subject_raw)

                    text = (
                        f"⚡️ <code>{h.escape(acc.email)}</code> ← <b>{from_e}</b>"
                        + (f"\n<b>{subject_e}</b>" if subject_e else "")
                        + f"\n<blockquote>{body_preview[:500]}</blockquote>"
                    )
                    try:
                        sent = await message.bot.send_message(
                            message.chat.id,
                            text[:4096],
                            parse_mode="HTML",
                            reply_markup=get_inbox_message_keyboard(incoming.id),
                        )
                        incoming.telegram_msg_id = sent.message_id
                        await s.commit()
                        try:
                            await message.bot.pin_chat_message(message.chat.id, sent.message_id)
                        except Exception:
                            pass
                        total_new += 1
                    except Exception as e:
                        logger.warning("Check send failed: %s", e, exc_info=True)

            if total_new == 0:
                await status_msg.edit_text("📨 Нет новых писем.")
            else:
                await status_msg.edit_text(
                    f"📨 Найдено новых писем: <b>{total_new}</b>", parse_mode="HTML"
                )

        from services.inbox_watcher import get_watcher
        watcher = get_watcher(bot)
        await watcher.start_for_user(message.from_user.id, message.chat.id)

    @dp.message(Command("reset"))
    async def cmd_reset(message: Message):
        """Принудительно снимает застрявшие lock-и.

        Использовать ТОЛЬКО если рассылка точно не идёт, но lock стоит
        (например, бот упал во время рассылки и lock не снялся).
        """
        import datetime
        from sqlalchemy import update as _upd
        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            if not settings:
                await message.answer("ℹ️ Настройки не найдены. Отправьте /start")
                return

            cleared = []
            now = datetime.datetime.now()

            if settings.parser_lock:
                age = (now - settings.parser_lock_at).total_seconds() if settings.parser_lock_at else 9999
                cleared.append(f"parser (age {int(age)}s)")
                settings.parser_lock = False
                settings.parser_lock_at = None

            if settings.mailer_lock:
                age = (now - settings.mailer_lock_at).total_seconds() if settings.mailer_lock_at else 9999
                cleared.append(f"mailer (age {int(age)}s)")
                settings.mailer_lock = False
                settings.mailer_lock_at = None

            if cleared:
                await s.commit()
                await message.answer(
                    f"🔄 Принудительно сброшено: {', '.join(cleared)}\n\n"
                    f"⚠️ Убедитесь, что процесс не активен — иначе возможны двойные рассылки."
                )
            else:
                await message.answer("✅ Нет активных блокировок")

    # ✅ НОВОЕ: команда /health — здоровье аккаунтов + пул + привязки прокси
    @dp.message(Command("health"))
    async def cmd_health(message: Message):
        from services.anti_ban import get_health_report
        from services.mail_tester import get_reputation_report
        from services.smtp_pool import get_smtp_pool
        from services.proxy_binding import get_proxy_binder

        lines = ["🏥 <b>Здоровье системы</b>\n"]

        # Здоровье аккаунтов
        lines.append("<b>📧 Аккаунты:</b>")
        lines.append(f"<pre>{get_health_report()}</pre>\n")

        # Репутация
        lines.append("<b>📊 Репутация:</b>")
        lines.append(f"<pre>{get_reputation_report()}</pre>\n")

        # SMTP Pool
        lines.append("<b>🔌 SMTP Pool:</b>")
        lines.append(f"<pre>{get_smtp_pool().stats()}</pre>\n")

        # Привязки прокси
        async with async_session() as s:
            proxies = list(await s.scalars(
                select(Proxy).where(
                    Proxy.user_id == message.from_user.id,
                    Proxy.is_active == True,
                    Proxy.status == "alive",
                )
            ))
        binder = get_proxy_binder()
        lines.append("<b>🌐 Привязки прокси:</b>")
        # ✅ get_binding_report теперь асинхронный (читает БД)
        lines.append(f"<pre>{await binder.get_binding_report(proxies)}</pre>")

        await message.answer(
            _safe_html_truncate("\n".join(lines), 4096),
            parse_mode="HTML",
        )

    @dp.message(Command("testlink"))
    async def cmd_testlink(message: Message):
        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            if not settings:
                await message.answer("❌ Сначала отправь /start"); return

            from services.team_config import get_team_key, resolve_profile_id, get_user_key_for_team
            team_code = settings.active_command or "Nurrp"
            team_key = get_team_key(team_code)
            team_profile = resolve_profile_id(settings, team_code)
            user_key = get_user_key_for_team(settings, team_code)

            item = await s.scalar(
                select(ParsedItem).where(
                    ParsedItem.user_id == message.from_user.id,
                    ParsedItem.link != "",
                ).order_by(ParsedItem.id.desc())
            )

            info = (
                f"🔍 <b>Диагностика API goo.network</b>\n\n"
                f"🎮 Команда: <b>{team_code}</b>\n"
                f"🔑 Ключ команды: <code>{team_key[:12]}...{team_key[-4:]}</code>\n"
                f"👤 Profile ID: <code>{team_profile or 'не задан'}</code>\n"
            )
            if item:
                info += (
                    f"\n📦 Товар: {item.title or '—'}\n"
                    f"💰 Цена: {item.price or '—'}\n"
                    f"🔗 Ссылка: {item.link or '—'}\n"
                    f"🖼 Фото: {'есть' if item.photo else 'нет'}"
                )
            else:
                info += "\n⚠️ Нет товаров с ссылкой."

            await message.answer(info, parse_mode="HTML")

            if not item:
                return

            if not team_profile:
                await message.answer("❌ Profile ID не задан. Меню → Profile 👤"); return

            status_msg = await message.answer("⏳ Запрос к API (parse)...")
            from services.goo_service import generate_link_with_parser
            ok, result = await generate_link_with_parser(
                team_key=team_key, service="ebay_de", item_url=item.link, profile_id=team_profile, user_key=user_key
            )
            await status_msg.edit_text(
                f"{'✅' if ok else '❌'} <b>API parse:</b>\n<code>{result[:500]}</code>",
                parse_mode="HTML",
            )

    @dp.message(Command("testservices"))
    async def cmd_testservices(message: Message):
        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            item = await s.scalar(
                select(ParsedItem).where(
                    ParsedItem.user_id == message.from_user.id,
                    ParsedItem.link != "",
                ).order_by(ParsedItem.id.desc())
            )
            if not settings or not settings.api_key or not settings.profile_id or not item:
                await message.answer("❌ Нет ключа/профиля/товара.")
                return

            from services.goo_service import generate_link_with_parser
            candidates = ["ebay_de", "ebay-kleinanzeigen", "ebay_kleinanzeigen_de"]
            results = []
            for svc in candidates:
                ok, res = await generate_link_with_parser(
                    settings.api_key, svc, item.link, settings.profile_id
                )
                results.append(f"{'✅' if ok else '❌'} <code>{svc}</code> -> {res[:80]}")

            await message.answer(
                "🔍 <b>Перебор service:</b>\n\n" + "\n".join(results),
                parse_mode="HTML",
            )

    @dp.message(Command("testservices2"))
    async def cmd_testservices2(message: Message):
        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            if not settings or not settings.api_key:
                await message.answer("❌ Нет API ключа.")
                return

            import httpx
            from services.goo_service import _make_headers, GOO_API_BASE
            from services.team_config import get_team_key, get_user_key_for_team
            tk = get_team_key(settings.active_command or "Nurrp")
            uk = get_user_key_for_team(settings, settings.active_command or "Nurrp")
            async with httpx.AsyncClient(timeout=15) as client:
                for ep in ["/api/services", "/api/service/list", "/services"]:
                    try:
                        r = await client.get(
                            f"{GOO_API_BASE}{ep}", headers=_make_headers(tk, uk)
                        )
                        if r.status_code == 200:
                            await message.answer(
                                f"✅ <b>{ep}</b>\n<code>{r.text[:600]}</code>",
                                parse_mode="HTML",
                            )
                            return
                        else:
                            await message.answer(
                                f"<code>{ep}</code> -> HTTP {r.status_code}", parse_mode="HTML"
                            )
                    except Exception as e:
                        await message.answer(f"<code>{ep}</code> -> {e}", parse_mode="HTML")

    @dp.message(Command("rawtest"))
    async def cmd_rawtest(message: Message):
        import httpx, json
        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            item = await s.scalar(
                select(ParsedItem).where(
                    ParsedItem.user_id == message.from_user.id,
                    ParsedItem.link != "",
                ).order_by(ParsedItem.id.desc())
            )
            if not settings or not item:
                await message.answer("❌ Нет товара.")
                return

            from services.goo_service import GOO_API_BASE, _make_headers_for_team
            from services.team_config import get_team_key, resolve_profile_id
            tk = get_team_key(settings.active_command or "Nurrp")
            pid = resolve_profile_id(settings, settings.active_command or "Nurrp")
            payload = {
                "service": "ebay_de",
                "url": item.link,
                "isNeedBalanceChecker": False,
                "profileID": pid or "",
            }
            headers = _make_headers_for_team(tk)
            await message.answer(
                f"📤 <b>Запрос:</b>\nURL: <code>{GOO_API_BASE}/api/generate/single/parse</code>\n"
                f"Headers: <code>{json.dumps(headers, indent=2)}</code>\n"
                f"Body: <code>{json.dumps(payload, indent=2)}</code>",
                parse_mode="HTML",
            )
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{GOO_API_BASE}/api/generate/single/parse",
                    headers=headers,
                    json=payload,
                )
                await message.answer(
                    f"📥 <b>Ответ HTTP {resp.status_code}:</b>\n<code>{resp.text[:1000]}</code>",
                    parse_mode="HTML",
                )

    @dp.message(Command("balance"))
    async def cmd_balance(message: Message):
        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            if not settings:
                await message.answer("❌ Сначала отправь /start"); return

            import httpx
            from services.goo_service import GOO_API_BASE, _make_headers_for_team
            from services.team_config import get_team_key
            tk = get_team_key(settings.active_command or "Nurrp")
            async with httpx.AsyncClient(timeout=15) as client:
                for ep in ["/api/balance", "/api/user/balance", "/api/user", "/api/profile", "/api/account"]:
                    try:
                        r = await client.get(
                            f"{GOO_API_BASE}{ep}", headers=_make_headers_for_team(tk)
                        )
                        await message.answer(
                            f"<code>{ep}</code> -> {r.status_code}\n<code>{r.text[:300]}</code>",
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        await message.answer(f"<code>{ep}</code> -> {e}")

    @dp.message(Command("setoffer"))
    async def cmd_setoffer(message: Message):
        import re
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "Использование: /setoffer <UUID>\n"
                "UUID: Инструменты -> Трафик -> Ваш оффер",
                parse_mode="HTML",
            )
            return

        offer_key = parts[1].strip()
        if not re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            offer_key,
            re.I,
        ):
            await message.answer(
                "❌ Неверный формат. UUID вида: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                parse_mode="HTML",
            )
            return

        async with async_session() as s:
            settings = await s.scalar(select(UserSettings).where(UserSettings.user_id == message.from_user.id))
            if settings:
                settings.offer_key = offer_key
                await s.commit()
                await message.answer(
                    f"✅ Offer Key сохранён: <code>{offer_key}</code>",
                    parse_mode="HTML",
                )
            else:
                await message.answer("❌ Сначала отправь /start")

    @dp.message(Command("admin"))
    async def cmd_admin(message: Message):
        from database.repository import is_superadmin, get_admin_role
        from database.engine import async_session
        uid = message.from_user.id
        if not is_superadmin(uid):
            async with async_session() as s:
                ar = await get_admin_role(s, uid)
                if not ar:
                    await message.answer("❌ Нет доступа.")
                    return
        from keyboards.settings_inline import get_admin_menu_keyboard
        # Determine permissions for menu rendering
        perms = None
        if not is_superadmin(uid):
            async with async_session() as s:
                ar = await get_admin_role(s, uid)
                if ar:
                    import json
                    perms_dict = json.loads(ar.permissions)
                    perms = [k for k, v in perms_dict.items() if v]
        await message.answer(
            "👑 <b>Админ-панель</b>\n\nВыберите раздел:",
            parse_mode="HTML", reply_markup=get_admin_menu_keyboard(perms)
        )

    # ─── Callback: подтверждение рассылки ─────────────────────────────────
    @dp.callback_query(F.data == "mailer_start_confirm")
    async def on_mailer_confirm(callback: CallbackQuery):
        from services.state import _pending_mailer_items
        from services.email_hunter import EmailHunter

        uid = callback.from_user.id
        found_items = _pending_mailer_items.pop(uid, None)
        if not found_items:
            await callback.answer("Нет данных для рассылки.", show_alert=True)
            return
        await callback.message.delete()
        hunter = EmailHunter(uid, callback.message.chat.id, 0, callback.bot)
        await hunter.start_mailer_confirmed(found_items)
        await callback.answer()

    @dp.callback_query(F.data == "mailer_start_cancel")
    async def on_mailer_cancel(callback: CallbackQuery):
        from services.state import _pending_mailer_items
        _pending_mailer_items.pop(callback.from_user.id, None)
        await callback.message.edit_text("❌ Рассылка отменена.")
        await callback.answer()

    @dp.callback_query(F.data == "mailer_cancel")
    async def on_mailer_stop(callback: CallbackQuery):
        from services.state import _active_mailers
        mailer = _active_mailers.get(callback.from_user.id)
        if mailer:
            mailer.cancel()
            await callback.answer("⏹️ Рассылка остановлена.")
        else:
            await callback.answer("Нет активной рассылки.", show_alert=True)

    from aiogram.exceptions import TelegramNetworkError, TelegramServerError

    async def on_shutdown():
        logger.info("Shutting down bot...")
        from services.state import _active_hunters, _active_mailers
        for h in _active_hunters.values():
            h.cancel()
        for m in _active_mailers.values():
            m.cancel()
        _active_hunters.clear()
        _active_mailers.clear()

        # ✅ НОВОЕ: закрываем SMTP Connection Pool
        from services.smtp_pool import get_smtp_pool
        await get_smtp_pool().close_all()
        logger.info("SMTP pool closed.")

        from services.http_client import close_http
        await close_http()

        if hasattr(dp.storage, 'redis'):
            await dp.storage.redis.close()
            logger.info("Redis connection closed.")

        await engine.dispose()
        logger.info("Bot shut down.")

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Starting bot...")
    # ✅ MED-39: экспоненциальный backoff вместо фиксированной задержки.
    # Ранее при кривом хендлере бот бесконечно рестартил каждые 3 сек,
    # засоряя лог. Теперь: 3 → 6 → 12 → 24 → 60 (потолок) сек,
    # + алерт админу при 5+ рестартах подряд.
    retry_delay = 3
    retry_count = 0
    MAX_RETRY_DELAY = 60
    ALERT_AFTER_RETRIES = 5
    while True:
        try:
            await dp.start_polling(bot)
        except (TelegramNetworkError, TelegramServerError) as e:
            logger.warning("Network/server error: %s. Retrying in %s sec...", e, retry_delay)
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.exception("Fatal error: %s. Retrying in %s sec...", e, retry_delay)
            retry_count += 1
            if retry_count >= ALERT_AFTER_RETRIES and ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>Бот упал {retry_count} раз подряд</b>\n"
                        f"Последняя ошибка: <code>{str(e)[:300]}</code>\n"
                        f"Retry delay: {retry_delay} сек",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)
            continue
        else:
            break
        # Успешный старт сбрасывает счётчики
        retry_count = 0
        retry_delay = 3


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user.")
