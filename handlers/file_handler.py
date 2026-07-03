import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import (
    UserSettings, ParsedItem, Template, EmailAccount,
    Proxy, IncomingMessage, ItemStatus
)
from keyboards.settings_inline import MESSAGES
from services.email_hunter import EmailHunter, _make_variations
from services.state import get_parser_lock  # FIX CRIT-04

logger = logging.getLogger(__name__)

file_router = Router(name="file_handler")
_debug_users: set[int] = set()

from database.repository import get_or_create_settings, get_pending_item_count


def _try_fix_json(text: str) -> tuple:
    """
    Попытка восстановить повреждённый JSON.
    Возвращает (данные, was_repaired: bool).
    FIX MED-03: возвращаем флаг ремонта, чтобы показать пользователю предупреждение.
    """
    import json as _json
    import re as _re

    # Попытка 1: прямой парсинг
    try:
        return _json.loads(text), False
    except _json.JSONDecodeError:
        pass

    # Попытка 2: очистка управляющих символов (безопасно)
    cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    try:
        return _json.loads(cleaned), False
    except _json.JSONDecodeError:
        pass

    # Попытка 3: добавление закрывающих скобок (может создать неполные данные)
    stripped = text.rstrip()
    for suffix in ["\n ]}", "\n]}", "]}", "]}"]:
        try:
            result = _json.loads(stripped + suffix)
            logger.warning("JSON repaired by appending closing brackets — data may be incomplete")
            return result, True  # True = данные восстановлены частично
        except _json.JSONDecodeError:
            pass

    return None, False


@file_router.message(Command("debug"))
async def cmd_debug(message: Message):
    from config import ADMIN_ID
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Нет доступа.")
        return
    uid = message.from_user.id
    if uid in _debug_users:
        _debug_users.discard(uid)
        await message.answer("🔍 <b>Режим диагностики: ВЫКЛ</b>", parse_mode="HTML")
    else:
        _debug_users.add(uid)
        await message.answer(
            "🔍 <b>Режим диагностики: ВКЛ</b>\n\n"
            "При загрузке JSON бот покажет:\n"
            "— Структуру первых 3 элементов\n"
            "— Причины пропуска элементов",
            parse_mode="HTML",
        )


@file_router.message(F.document)
async def handle_document(message: Message, session: AsyncSession, state: FSMContext, bot):
    doc = message.document
    if not doc.file_name:
        await message.answer("❌ Файл не распознан.")
        return

    # FIX MED-07: Проверяем размер до загрузки (5 MB лимит)
    MAX_FILE_SIZE = 5 * 1024 * 1024
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.answer(
            f"❌ Файл слишком большой ({doc.file_size // 1024 // 1024} MB).\n"
            f"Максимальный размер: {MAX_FILE_SIZE // 1024 // 1024} MB."
        )
        return

    settings = await get_or_create_settings(session, message.from_user.id)

    # FIX CRIT-04: Атомарная проверка + установка parser_lock через asyncio.Lock.
    # Раньше: check и set были разделены во времени → два файла могли запустить
    # два Hunter'а одновременно для одного пользователя.
    parser_lock = get_parser_lock(message.from_user.id)
    async with parser_lock:
        await session.refresh(settings)  # актуальное состояние из БД
        if settings.parser_lock:
            await message.answer(MESSAGES["parser_lock"])
            return
        settings.parser_lock = True
        await session.commit()  # фиксируем lock до выхода из критической секции

    if doc.file_name.endswith(".json"):
        await _handle_json_file(message, doc, session, settings, bot)
    elif doc.file_name.endswith(".txt"):
        await _handle_txt(message, doc, session, settings, bot)
    else:
        settings.parser_lock = False
        await session.commit()
        await message.answer("❌ Поддерживаются только файлы .txt и .json")


async def _handle_json_file(
    message: Message, doc, session: AsyncSession, settings: UserSettings, bot
) -> None:
    file = await message.bot.get_file(doc.file_id)
    data = await message.bot.download_file(file.file_path)
    raw = data.read().decode("utf-8", errors="replace")
    await _handle_json_content(message, session, settings, raw, bot)


async def _handle_json_content(
    message: Message, session: AsyncSession, settings: UserSettings, raw: str, bot
) -> None:
    debug = message.from_user.id in _debug_users

    payload, was_repaired = _try_fix_json(raw)

    if payload is None:
        settings.parser_lock = False
        await session.commit()
        await message.answer(
            "❌ Невалидный JSON — файл повреждён или обрезан.\n"
            "Попробуй сохранить его заново и отправить ещё раз."
        )
        return

    # FIX MED-03: Предупреждаем пользователя о частичном восстановлении данных
    if was_repaired:
        await message.answer(
            "⚠️ <b>Файл был повреждён</b> — JSON восстановлен автоматически.\n"
            "Часть записей в конце файла могла быть потеряна.\n"
            "Рекомендуется загрузить оригинальный файл.",
            parse_mode="HTML",
        )

    items_list: list = []
    if isinstance(payload, list):
        items_list = payload
    elif isinstance(payload, dict):
        items_list = (
            payload.get("items")
            or payload.get("data")
            or payload.get("results")
            or []
        )
        if not items_list:
            for v in payload.values():
                if isinstance(v, list) and len(v) > 0:
                    items_list = v
                    break

    if debug and items_list:
        debug_lines = [f"🔍 <b>Структура JSON:</b> элементов={len(items_list)}"]
        for i, item in enumerate(items_list[:3]):
            if isinstance(item, dict):
                keys = list(item.keys())[:10]
                debug_lines.append(f" [{i}] Ключи: {keys}")
                for k in ["item_person_name", "person_name", "nickname", "name"]:
                    if k in item:
                        debug_lines.append(f"   {k} = '{str(item[k])[:50]}'")
        await message.answer("\n".join(debug_lines)[:2048], parse_mode="HTML")

    # Bulk-загрузка существующих никнеймов — один запрос вместо N (устраняет N+1)
    existing_nicks: set[str] = set(
        await session.scalars(
            select(ParsedItem.nickname).where(ParsedItem.user_id == message.from_user.id)
        )
    )

    found_names = added = skipped_no_name = skipped_not_dict = already_in_db = 0
    new_records = []

    for idx, item in enumerate(items_list):
        if not isinstance(item, dict):
            skipped_not_dict += 1
            continue

        person_name = (item.get("item_person_name") or "").strip()
        if not person_name:
            skipped_no_name += 1
            if debug and skipped_no_name <= 3:
                await message.answer(
                    f"⚠️ Элемент #{idx}: нет имени. Ключи: {list(item.keys())[:10]}"
                )
            continue

        found_names += 1
        title = item.get("item_title") or item.get("title", "")
        photo = item.get("item_photo") or item.get("photo", "")
        link = item.get("item_link") or item.get("link", "")
        price = str(item.get("item_price") or "")
        location = item.get("location", "")
        variations = _make_variations(person_name)
        valid_vars = [v for v in variations if len(v) >= 5 and v not in existing_nicks]

        if not valid_vars:
            already_in_db += 1
            continue

        added += 1
        for variation in valid_vars:
            existing_nicks.add(variation)
            new_records.append(ParsedItem(
                user_id=message.from_user.id,
                nickname=variation,
                person_name=person_name,
                title=title,
                photo=photo,
                link=link,
                price=price,
                location=location,
                status=ItemStatus.PENDING,
            ))

    if new_records:
        session.add_all(new_records)
        await session.commit()

    parts = [f"✅ Добавлено: {added}", f"Найдено в файле: {found_names}"]
    if already_in_db:
        parts.insert(1, f"Уже в БД: {already_in_db}")
    await message.answer(" | ".join(parts))

    if added == 0:
        await message.answer("ℹ️ Нет новых имён для поиска.")
        settings.parser_lock = False
        await session.commit()
        return

    from services.state import _active_hunters
    status_msg = await message.answer("⏳ Запуск Email Hunter...")
    hunter = EmailHunter(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        bot=message.bot,
    )
    _active_hunters[message.from_user.id] = hunter
    asyncio.create_task(_run_hunter_cleanup(hunter, message.from_user.id))


async def _handle_txt(
    message: Message, doc, session: AsyncSession, settings: UserSettings, bot
) -> None:
    file = await message.bot.get_file(doc.file_id)
    data = await message.bot.download_file(file.file_path)
    raw = data.read().decode("utf-8", errors="replace").strip()

    if not raw:
        settings.parser_lock = False
        await session.commit()
        await message.answer(MESSAGES["file_no_items"])
        return

    # Автодетект JSON в .txt файле
    if raw.startswith("{") or raw.startswith("["):
        import json as _json
        try:
            payload = _json.loads(raw)
            items_list = payload if isinstance(payload, list) else (
                payload.get("items") or payload.get("data") or payload.get("results") or []
            )
            if not items_list:
                for v in payload.values():
                    if isinstance(v, list):
                        items_list = v
                        break
            if items_list:
                await _handle_json_content(message, session, settings, raw, bot)
                return
        except Exception:
            pass

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines:
        settings.parser_lock = False
        await session.commit()
        await message.answer(MESSAGES["file_no_items"])
        return

    # FIX MED-04: Bulk-загрузка существующих ников — ОДИН SELECT вместо N.
    # В оригинале был N+1: session.scalar(...) в цикле на каждую строку файла.
    existing_nicks: set[str] = set(
        await session.scalars(
            select(ParsedItem.nickname).where(ParsedItem.user_id == message.from_user.id)
        )
    )

    import re as _re
    added = skipped_dup = 0
    new_records = []

    for line in lines:
        nick = line.strip('" \t\r\n')
        if _re.match(r'^[{}\[\][,]', nick) or _re.match(r'^[^a-zA-Z0-9]', nick):
            continue
        if nick in existing_nicks:
            skipped_dup += 1
        else:
            existing_nicks.add(nick)
            new_records.append(ParsedItem(
                user_id=message.from_user.id,
                nickname=nick,
                person_name=nick,
                status=ItemStatus.PENDING,
            ))
            added += 1

    if new_records:
        session.add_all(new_records)
        await session.commit()

    await message.answer(
        f"📝 <b>TXT</b>\n✅ Добавлено: {added}\n♻️ Дублей: {skipped_dup}",
        parse_mode="HTML",
    )

    from services.state import _active_hunters
    status_msg = await message.answer("⏳ Запуск Email Hunter...")
    hunter = EmailHunter(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        bot=bot,
    )
    _active_hunters[message.from_user.id] = hunter
    asyncio.create_task(_run_hunter_cleanup(hunter, message.from_user.id))


async def _run_hunter_cleanup(hunter, user_id: int):
    from services.state import _active_hunters
    try:
        await hunter.run()
    finally:
        _active_hunters.pop(user_id, None)


@file_router.message(F.text == "📂 Шаблоны")
async def show_templates_quick(message: Message, session: AsyncSession):
    templates = list(await session.scalars(
        select(Template).where(Template.user_id == message.from_user.id)
    ))
    if not templates:
        await message.answer(
            "ℹ️ У вас пока нет шаблонов. Добавьте их в меню Настроек → Шаблоны."
        )
        return
    lines = [
        f"<b>{i}. {t.name}</b>\n{t.text[:200]}{'...' if len(t.text) > 200 else ''}"
        for i, t in enumerate(templates, 1)
    ]
    await message.answer(
        "📂 <b>Ваши шаблоны:</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )
