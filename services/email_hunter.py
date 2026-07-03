import asyncio
import logging
import re
import time
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import UserSettings, ParsedItem, Proxy, ItemStatus
from database.engine import async_session
from services.smtp_bypass_checker import ProxyConfig
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

logger = logging.getLogger(__name__)

SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

_CYRILLIC_MAP = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"yo","ж":"zh","з":"z",
    "и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
    "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh",
    "щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
    "А":"A","Б":"B","В":"V","Г":"G","Д":"D","Е":"E","Ё":"Yo","Ж":"Zh","З":"Z",
    "И":"I","Й":"Y","К":"K","Л":"L","М":"M","Н":"N","О":"O","П":"P","Р":"R",
    "С":"S","Т":"T","У":"U","Ф":"F","Х":"H","Ц":"Ts","Ч":"Ch","Ш":"Sh",
    "Щ":"Sch","Ъ":"","Ы":"Y","Ь":"","Э":"E","Ю":"Yu","Я":"Ya",
    "є":"ye","і":"i","ї":"yi","ґ":"g","Є":"Ye","І":"I","Ї":"Yi","Ґ":"G",
}
_GERMAN_MAP = {"ä":"ae","ö":"oe","ü":"ue","ß":"ss","Ä":"Ae","Ö":"Oe","Ü":"Ue"}


def _transliterate(text: str) -> str:
    result = []
    for ch in text:
        if ch in _GERMAN_MAP:
            result.append(_GERMAN_MAP[ch])
        elif ch in _CYRILLIC_MAP:
            result.append(_CYRILLIC_MAP[ch])
        else:
            result.append(ch)
    return "".join(result)


def _clean_part(raw: str) -> str:
    cleaned = _transliterate(raw)
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"[._-]{2,}", ".", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned


def _make_variations(person_name: str) -> list[str]:
    raw = person_name.strip()
    if not raw:
        return []
    raw_parts = re.split(r"[\s]+", raw)
    parts = [_clean_part(p) for p in raw_parts if p.strip()]
    parts = [p for p in parts if p]

    seen: set[str] = set()
    result: list[str] = []

    def add(v: str):
        v = v.lower().strip("._-")
        if v not in seen and len(v) >= 5:
            seen.add(v)
            result.append(v)

    if len(parts) == 1:
        add(parts[0])
    else:
        f, s = parts[0].lower(), parts[1].lower()
        add(f + s)
        add(f + "." + s)

    if any(re.search(r"\d", v) for v in result):
        for v in list(result):
            nd = re.sub(r"\d", "", v).strip("._-")
            if nd:
                add(nd)

    return result


def _make_progress_bar(percent: int, length: int = 20) -> str:
    filled = int(length * percent / 100)
    return f"[{'█'*filled}{'░'*(length-filled)}] {percent}%"


class EmailHunter:
    def __init__(self, user_id: int, chat_id: int, message_id: int, bot):
        self.user_id = user_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.bot = bot
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def run(self) -> None:
        try:
            async with async_session() as session:
                await self._do_hunt(session)
        except asyncio.CancelledError:
            # FIX MED-10: CancelledError — не ошибка, правильное завершение
            logger.info("EmailHunter cancelled for user %s", self.user_id)
            raise
        except Exception as e:
            logger.exception("EmailHunter error for user %s: %s", self.user_id, e)
            await self._update_message(f"❌ Ошибка: {e}")
        finally:
            # Всегда снимаем parser_lock — даже при ошибке
            async with async_session() as session:
                await session.execute(
                    update(UserSettings)
                    .where(UserSettings.user_id == self.user_id)
                    .values(parser_lock=False)
                )
                await session.commit()

    async def _do_hunt(self, session: AsyncSession) -> None:
        settings = await session.scalar(
            select(UserSettings).where(UserSettings.user_id == self.user_id)
        )
        if settings is None:
            return

        ordered_domains = [
            d if "." in d else d + ".com"
            for d in [d.strip() for d in (settings.domain_priority or "gmail").split(",") if d.strip()]
        ]

        # Сбрасываем ранее упавшие элементы для повтора
        await session.execute(
            update(ParsedItem).where(
                ParsedItem.user_id == self.user_id,
                ParsedItem.status == ItemStatus.DONE,
                ParsedItem.found_email == "",
            ).values(status=ItemStatus.PENDING)
        )
        await session.commit()

        items = list(await session.scalars(
            select(ParsedItem).where(
                ParsedItem.user_id == self.user_id,
                ParsedItem.status == ItemStatus.PENDING,
            )
        ))

        proxies_db = list(await session.scalars(
            select(Proxy).where(
                Proxy.user_id == self.user_id,
                Proxy.is_active == True,
            )
        ))
        alive = [p for p in proxies_db if p.status == "alive"] or proxies_db
        proxy_configs = [
            ProxyConfig(host=p.host, port=p.port, username=p.username, password=p.password)
            for p in alive
        ]

        logger.info("Hunter: %d items, %d proxies", len(items), len(proxy_configs))

        items = [it for it in items if len(it.nickname) >= 5]
        total = len(items)
        if total == 0:
            settings.parser_lock = False
            await session.commit()
            await self._update_message("❌ Все никнеймы короче 5 символов.")
            return

        unique_names = len(set(it.person_name for it in items if it.person_name))
        display_total = unique_names if unique_names > 0 else total
        await self._update_message(
            f"🔍 <b>Email Hunter</b>\n\n"
            f"📧 Имён: {display_total}\n"
            f"🌐 Прокси: {len(proxy_configs)}\n"
            f"⚡ Запуск..."
        )

        from services.mailtester_ninja import verify_email as mt_verify

        concurrency = min(max(len(proxy_configs) * 3, 30), 80)
        sem = asyncio.Semaphore(concurrency)
        found = 0
        done = 0
        si = 0
        last_ui = time.time() - 3
        last_commit = 0.0
        lock = asyncio.Lock()
        recent_times: list[float] = []

        async def _one(item):
            nonlocal found, done, si, last_ui, last_commit
            async with sem:
                if self._cancelled:
                    return
                success = False
                for domain in ordered_domains:
                    try:
                        mt = await mt_verify(f"{item.nickname}@{domain}")
                        if mt.get("code") == "ok":
                            item.found_email = f"{item.nickname}@{domain}"
                            item.status = ItemStatus.DONE
                            success = True
                            break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # FIX CRIT-02: логируем сбой API вместо молчаливого проглатывания
                        logger.debug(
                            "mt_verify failed for %s@%s: %s",
                            item.nickname, domain, exc
                        )

                if not success:
                    item.status = ItemStatus.DONE

                now = time.time()
                async with lock:
                    if success:
                        found += 1
                    done += 1
                    si += 1
                    recent_times.append(now)
                    while recent_times and recent_times[0] < now - 10:
                        recent_times.pop(0)
                    speed = len(recent_times) / 10.0

                    if now - last_ui >= 2.0:
                        last_ui = now
                        pct = min(int(done / display_total * 100), 100) if display_total else 0
                        await self._update_message(
                            f"{SPINNERS[si % len(SPINNERS)]} <b>Email Hunter</b>\n"
                            f"{_make_progress_bar(pct)}\n"
                            f"Обработано: {done}/{display_total}\n"
                            f"✅ Найдено: {found}\n"
                            f"⚡ {speed:.1f} шт/сек"
                        )

                    if now - last_commit >= 5.0 or done % 50 == 0:
                        last_commit = now
                        await session.commit()

        tasks = [asyncio.create_task(_one(it)) for it in items]
        await asyncio.gather(*tasks)
        await session.commit()

        if self._cancelled:
            for it in items:
                if it.status != ItemStatus.DONE:
                    it.status = ItemStatus.PENDING
            await session.commit()
            await self._update_message(
                f"⏹️ <b>Остановлен</b>\n\nОбработано: {done}/{display_total}\n✅ Найдено: {found}"
            )
            return

        found_items = [it for it in items if it.found_email]
        await self._update_message(
            f"✅ <b>Поиск завершён</b>\n\n"
            f"Обработано: {display_total}\n"
            f"✅ Найдено: {len(found_items)}"
        )
        if found_items:
            import io
            from aiogram.types import BufferedInputFile
            buf = io.BytesIO()
            buf.write("EMAIL|NICK|LINK|PRICE|PHOTO\n".encode("utf-8"))
            for it in found_items:
                line = f"{it.found_email}|{it.nickname}|{it.link or ''}|{it.price or ''}|{it.photo or ''}\n"
                buf.write(line.encode("utf-8"))
            buf.seek(0)
            fname = f"results_{self.user_id}_{int(time.time())}.txt"
            await self.bot.send_document(
                chat_id=self.chat_id,
                document=BufferedInputFile(buf.read(), filename=fname),
                caption=f"✅ Найдено {len(found_items)} почт.",
            )
            await self._ask_mailer_confirmation(found_items)

    async def _update_message(self, text: str) -> None:
        """
        FIX CRIT-01: Обновить прогресс-сообщение.
        Ранее был голый except: pass — теперь логируем все ошибки.
        TelegramRetryAfter: ждём и повторяем.
        TelegramAPIError: логируем warning (сообщение удалено/устарело — не критично).
        """
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
                parse_mode="HTML",
            )
        except TelegramRetryAfter as e:
            logger.warning(
                "Telegram RetryAfter %ss for user %s — waiting and retrying",
                e.retry_after, self.user_id
            )
            await asyncio.sleep(e.retry_after)
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=text,
                    parse_mode="HTML",
                )
            except TelegramAPIError as retry_exc:
                logger.warning("Retry after still failed (hunter): %s", retry_exc)
        except TelegramAPIError as e:
            # Сообщение удалено, устарело или бот заблокирован — не прерываем поиск
            logger.warning(
                "Cannot update progress message (user=%s): %s",
                self.user_id, e
            )

    async def _ask_mailer_confirmation(self, found_items: list) -> None:
        """FIX MED-08: Предлагаем запустить рассылку вместо автозапуска."""
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        # Сохраняем found_emails в состоянии через глобальный словарь
        from services.state import _pending_mailer_items
        _pending_mailer_items[self.user_id] = found_items

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"📤 Начать рассылку ({len(found_items)} адресов)",
                callback_data="mailer_start_confirm",
            ),
            InlineKeyboardButton(text="❌ Пропустить", callback_data="mailer_start_cancel"),
        ]])
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=(
                f"✅ <b>Поиск завершён!</b>\n\n"
                f"📧 Найдено адресов: <b>{len(found_items)}</b>\n\n"
                f"Запустить рассылку по найденным адресам?"
            ),
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    async def start_mailer_confirmed(self, found_items: list) -> None:
        """Вызывается только при явном нажатии кнопки подтверждения."""
        import random
        from database.models import Template
        from services.mailer import Mailer
        from services.state import _active_mailers

        async with async_session() as session:
            templates = list(await session.scalars(
                select(Template).where(
                    Template.user_id == self.user_id,
                    Template.type == "custom",
                )
            ))
            # ✅ MED-9: берём и текст, и кастомный HTML-шаблон если есть
            chosen_template = random.choice(templates) if templates else None
            template_text = (
                chosen_template.text if chosen_template
                else "@товар\n\nЦена: @цена\n\n{email}"
            )
            template_html = getattr(chosen_template, "html_template", "") if chosen_template else ""
            presets = list(await session.scalars(
                select(Template).where(
                    Template.user_id == self.user_id,
                    Template.type == "smart_preset",
                )
            ))
            if not presets:
                await self.bot.send_message(
                    self.chat_id,
                    "⚠️ <b>Рассылка не запущена</b>\n\n"
                    "Добавьте умный пресет: Меню → Умные пресеты 📚",
                    parse_mode="HTML",
                )
                return
            subject_text = random.choice(presets).text

        recipients = [it.found_email for it in found_items if it.found_email]
        if not recipients:
            return

        status_msg = await self.bot.send_message(self.chat_id, "⏳ Инициализация рассылки...")
        mailer = Mailer(
            user_id=self.user_id,
            chat_id=self.chat_id,
            message_id=status_msg.message_id,
            bot=self.bot,
        )
        _active_mailers[self.user_id] = mailer

        async def _run():
            from services.state import _active_mailers
            try:
                # ✅ MED-9: передаём и custom_html_template
                await mailer.run(recipients, template_text, subject_text,
                                 custom_html_template=template_html)
            finally:
                _active_mailers.pop(self.user_id, None)

        asyncio.create_task(_run())
