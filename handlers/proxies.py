import asyncio
import logging
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
import aiohttp

from database.models import Proxy
from keyboards.settings_inline import (
    get_proxies_menu_keyboard,
    get_proxies_list_keyboard,
    get_cancel_keyboard,
    MESSAGES,
)
from states.forms import ProxyAddState, ProxyEditState

logger = logging.getLogger(__name__)
proxies_router = Router(name="proxies")

PROXY_TEST_URL = "http://ip-api.com/json"
PROXY_TEST_TIMEOUT = aiohttp.ClientTimeout(total=5)

ADD_HELP = (
    "🌐 <b>Добавление прокси</b>\n\n"
    "Эти прокси используются для SMTP/IMAP и поиска email.\n\n"
    "<b>Поддерживаемые форматы:</b>\n"
    "<code>socks5://host:port</code>  — SOCKS5 (рекомендуется для SMTP)\n"
    "<code>socks5://login:password@host:port</code>\n"
    "<code>http://host:port</code>  — HTTP CONNECT\n"
    "<code>http://login:password@host:port</code>\n"
    "<code>host:port</code>  — SOCKS5 без авторизации (по умолчанию)\n"
    "<code>host:port:login:password</code>  — SOCKS5 с авторизацией\n\n"
    "<b>Рекомендация:</b> для email-рассылок SOCKS5 лучше — не детектится Gmail/Outlook,\n"
    "не инспектирует трафик, не ломает STARTTLS."
)


def _parse_proxy_line(line: str) -> dict | None:
    """Парсит строку прокси. Поддерживает URL-формат с указанием типа и старый формат.

    Возвращает dict с полями: host, port, username, password, proxy_type.
    По умолчанию proxy_type = "socks5" (лучше для SMTP).
    """
    line = line.strip()
    if not line:
        return None
    host = port = username = password = ""
    proxy_type = "socks5"

    # URL-формат: socks5://user:pass@host:port или http://host:port
    if "://" in line:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(line)
            scheme = parsed.scheme.lower()
            if scheme in ("socks5", "socks4", "http"):
                proxy_type = scheme
            else:
                return None
            host = parsed.hostname or ""
            port = parsed.port or 0
            username = parsed.username or ""
            password = parsed.password or ""
        except Exception:
            return None
    elif "@" in line:
        at_idx = line.index("@")
        left, right = line[:at_idx], line[at_idx + 1:]
        r_parts, l_parts = right.split(":"), left.split(":")
        if len(r_parts) >= 2 and r_parts[1].isdigit():
            host = r_parts[0]; port = int(r_parts[1])
            username = l_parts[0] if len(l_parts) >= 1 else ""
            password = l_parts[1] if len(l_parts) >= 2 else ""
        elif len(l_parts) >= 2 and l_parts[1].isdigit():
            host = l_parts[0]; port = int(l_parts[1])
            username = r_parts[0] if len(r_parts) >= 1 else ""
            password = r_parts[1] if len(r_parts) >= 2 else ""
        else:
            return None
    else:
        parts = line.split(":")
        if len(parts) == 2:
            host, port_str = parts; port = int(port_str) if port_str.isdigit() else 0
        elif len(parts) == 4:
            host, port_str, username, password = parts; port = int(port_str) if port_str.isdigit() else 0
        elif len(parts) == 3:
            host, port_str, username = parts; port = int(port_str) if port_str.isdigit() else 0
        else:
            return None
    if not host or port <= 0 or port > 65535:
        return None
    if not re.match(r"^[\w.\-]+$", host):
        return None
    return {"host": host, "port": port, "username": username,
            "password": password, "proxy_type": proxy_type}


async def _check_single_proxy(proxy: Proxy) -> bool:
    """Проверяет прокси (socks5/http/socks4) через python-socks → connection к ip-api.com."""
    from python_socks.async_.asyncio import Proxy as AsyncProxy
    ptype = (proxy.proxy_type or "socks5").lower()
    if proxy.username and proxy.password:
        url = f"{ptype}://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
    elif proxy.username:
        url = f"{ptype}://{proxy.username}@{proxy.host}:{proxy.port}"
    else:
        url = f"{ptype}://{proxy.host}:{proxy.port}"
    try:
        p = AsyncProxy.from_url(url)
        # Пробуем установить соединение через прокси до HTTP-сервера
        sock = await p.connect(dest_host="ip-api.com", dest_port=80, timeout=10)
        try:
            sock.close()
        except Exception:
            pass
        return True
    except Exception as e:
        logger.debug("Proxy check failed for %s:%d (%s): %s",
                     proxy.host, proxy.port, ptype, e)
        return False


@proxies_router.callback_query(F.data == "settings_proxies")
async def proxies_menu(callback: CallbackQuery, session: AsyncSession):
    proxies = list(await session.scalars(select(Proxy).where(Proxy.user_id == callback.from_user.id).order_by(Proxy.created_at)))
    await callback.message.edit_text(
        f"🌐 <b>Loma Proxy</b>\n\nВсего: <b>{len(proxies)}</b>",
        parse_mode="HTML", reply_markup=get_proxies_menu_keyboard(len(proxies)))
    await callback.answer()


@proxies_router.callback_query(F.data == "proxy_add")
async def proxy_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProxyAddState.waiting_for_proxies)
    await callback.message.edit_text(ADD_HELP, parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@proxies_router.message(ProxyAddState.waiting_for_proxies, F.text)
async def process_proxy_add(message: Message, state: FSMContext, session: AsyncSession):
    lines = message.text.strip().splitlines()
    added = failed = 0
    for line in lines:
        parsed = _parse_proxy_line(line)
        if not parsed:
            failed += 1; continue
        session.add(Proxy(
            user_id=message.from_user.id,
            host=parsed["host"], port=parsed["port"],
            username=parsed["username"], password=parsed["password"],
            proxy_type=parsed.get("proxy_type", "socks5"),
            rotation_mode="sticky",  # по умолчанию sticky (привязка к аккаунту)
            status="unknown",
        ))
        added += 1
    await session.commit(); await state.clear()
    r = f"✅ Добавлено: <b>{added}</b>"
    if failed: r += f"\n❌ Не распознано: <b>{failed}</b>"
    await message.answer(r, parse_mode="HTML")
    proxies = list(await session.scalars(select(Proxy).where(Proxy.user_id == message.from_user.id).order_by(Proxy.created_at)))
    await message.answer(f"🌐 <b>Loma Proxy</b>\n\nВсего: <b>{len(proxies)}</b>", parse_mode="HTML",
                         reply_markup=get_proxies_menu_keyboard(len(proxies)))


@proxies_router.callback_query(F.data == "proxy_edit")
async def proxy_edit(callback: CallbackQuery, session: AsyncSession):
    proxies = list(await session.scalars(select(Proxy).where(Proxy.user_id == callback.from_user.id).order_by(Proxy.created_at)))
    if not proxies: await callback.answer(MESSAGES["no_items"], show_alert=True); return
    await callback.message.edit_text("Выберите прокси для изменения:", reply_markup=get_proxies_list_keyboard(proxies, "prx_edit"))
    await callback.answer()


@proxies_router.callback_query(F.data.startswith("prx_edit_"))
async def prx_edit_select(callback: CallbackQuery, state: FSMContext):
    await state.update_data(edit_prx_id=int(callback.data.split("_")[-1]))
    await state.set_state(ProxyEditState.waiting_for_new_data)
    await callback.message.edit_text(ADD_HELP, parse_mode="HTML", reply_markup=get_cancel_keyboard())
    await callback.answer()


@proxies_router.message(ProxyEditState.waiting_for_new_data, F.text)
async def process_proxy_edit(message: Message, state: FSMContext, session: AsyncSession):
    parsed = _parse_proxy_line(message.text.strip())
    if not parsed:
        await message.answer("❌ Не распознано.\n\n" + ADD_HELP, parse_mode="HTML"); return
    prx = await session.scalar(select(Proxy).where(Proxy.id == (await state.get_data())["edit_prx_id"]))
    if prx:
        prx.host = parsed["host"]
        prx.port = parsed["port"]
        prx.username = parsed["username"]
        prx.password = parsed["password"]
        prx.proxy_type = parsed.get("proxy_type", "socks5")
        prx.status = "unknown"
        await session.commit()
    await state.clear(); await message.answer("✅ Прокси обновлён.")


@proxies_router.callback_query(F.data == "proxy_check_all")
async def proxy_check_all(callback: CallbackQuery, session: AsyncSession):
    proxies = list(await session.scalars(select(Proxy).where(Proxy.user_id == callback.from_user.id).order_by(Proxy.created_at)))
    if not proxies: await callback.answer(MESSAGES["no_items"], show_alert=True); return
    await callback.answer("🔍 Проверка...")
    msg = await callback.message.answer(f"🔍 Проверка... 0/{len(proxies)}")

    async def _check(prx):
        ok = await _check_single_proxy(prx)
        return prx, ok

    results = await asyncio.gather(*[_check(p) for p in proxies])
    alive = dead = 0
    for prx, ok in results:
        prx.status = "alive" if ok else "dead"
        alive += ok; dead += not ok
    await session.commit()

    lines = [f"🌐 <b>Результат проверки:</b>\n"]
    for prx, ok in results:
        icon = "🟢" if ok else "🔴"
        ptype = (prx.proxy_type or "socks5").upper()
        lines.append(f"{icon} <code>{prx.host}:{prx.port}</code> ({ptype})")
    lines.append(f"\n✅ Работает: {alive}")
    lines.append(f"❌ Не работает: {dead}")

    kb_rows = [[InlineKeyboardButton(text="🔙 Назад", callback_data="settings_proxies")]]
    if dead > 0:
        kb_rows.insert(0, [InlineKeyboardButton(text="🗑️ Удалить нерабочие", callback_data="proxy_delete_dead")])

    await msg.edit_text("\n".join(lines), parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@proxies_router.callback_query(F.data == "proxy_delete_dead")
async def proxy_delete_dead(callback: CallbackQuery, session: AsyncSession):
    uid = callback.from_user.id
    dead = await session.scalars(
        select(Proxy).where(Proxy.user_id == uid, Proxy.status == "dead"))
    count = 0
    for prx in dead:
        await session.delete(prx)
        count += 1
    await session.commit()
    await callback.answer(f"🗑️ Удалено нерабочих: {count}", show_alert=True)
    proxies = list(await session.scalars(
        select(Proxy).where(Proxy.user_id == uid).order_by(Proxy.created_at)))
    await callback.message.edit_text(
        f"🌐 <b>Loma Proxy</b>\n\nВсего: <b>{len(proxies)}</b>",
        parse_mode="HTML", reply_markup=get_proxies_menu_keyboard(len(proxies)))


@proxies_router.callback_query(F.data == "proxy_delete")
async def proxy_delete(callback: CallbackQuery, session: AsyncSession):
    proxies = list(await session.scalars(select(Proxy).where(Proxy.user_id == callback.from_user.id).order_by(Proxy.created_at)))
    if not proxies: await callback.answer(MESSAGES["no_items"], show_alert=True); return
    await callback.message.edit_text("Выберите прокси для удаления:", reply_markup=get_proxies_list_keyboard(proxies, "prx_del"))
    await callback.answer()


@proxies_router.callback_query(F.data.startswith("prx_del_"))
async def prx_del_confirm(callback: CallbackQuery, session: AsyncSession):
    prx = await session.scalar(select(Proxy).where(Proxy.id == int(callback.data.split("_")[-1])))
    if prx: await session.delete(prx); await session.commit()
    proxies = list(await session.scalars(select(Proxy).where(Proxy.user_id == callback.from_user.id).order_by(Proxy.created_at)))
    await callback.message.edit_text(f"🌐 <b>Loma Proxy</b>\n\nВсего: <b>{len(proxies)}</b>",
                                     parse_mode="HTML", reply_markup=get_proxies_menu_keyboard(len(proxies)))
    await callback.answer(MESSAGES["proxy_deleted"])


@proxies_router.callback_query(F.data == "proxy_delete_all")
async def proxy_delete_all(callback: CallbackQuery, session: AsyncSession):
    await session.execute(delete(Proxy).where(Proxy.user_id == callback.from_user.id))
    await session.commit()
    await callback.message.edit_text("🌐 <b>Loma Proxy</b>\n\nВсего: <b>0</b>", parse_mode="HTML",
                                     reply_markup=get_proxies_menu_keyboard(0))
    await callback.answer(MESSAGES["proxy_all_deleted"])


# ─── Loma Proxy: импорт списка из @LomaProxyBot ──────────────────────────────

@proxies_router.callback_query(F.data == "proxy_loma_import")
async def loma_import_menu(callback: CallbackQuery, state: FSMContext):
    """Меню импорта прокси из списка @LomaProxyBot.

    Loma Proxy — Telegram-бот @LomaProxyBot, который выдаёт прокси прямо
    в чат. Пользователь копирует список и вставляет сюда одним сообщением.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await state.set_state(ProxyAddState.waiting_for_loma_api_key)
    await callback.message.edit_text(
        "🌐 <b>Импорт из @LomaProxyBot</b>\n\n"
        "1. Откройте <code>@LomaProxyBot</code> в Telegram\n"
        "2. Закажите прокси (Residential SOCKS5)\n"
        "3. Скопируйте список прокси\n"
        "4. Вставьте сюда одним сообщением\n\n"
        "<b>Поддерживаемые форматы строки:</b>\n"
        "<code>socks5://login:password@host:port</code>\n"
        "<code>login:password@host:port</code>\n"
        "<code>host:port:login:password</code>\n"
        "<code>host:port:login:password:country</code>\n"
        "<code>host:port</code> (без авторизации)\n\n"
        "<i>Можно несколько строк сразу. Дубликаты будут обновлены.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings"),
        ]]),
    )
    await callback.answer()


@proxies_router.message(ProxyAddState.waiting_for_loma_api_key, F.text)
async def loma_import_process(message: Message, state: FSMContext, session: AsyncSession):
    """Парсит список прокси из @LomaProxyBot и импортирует в БД."""
    from services.loma_proxy import parse_loma_proxy_list, import_proxies_to_db

    text = message.text
    # ✅ Удаляем сообщение с прокси из чата (в нём могут быть пароли)
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Cannot delete loma-import message: %s", e)

    parsed_list, failed = parse_loma_proxy_list(text)
    if not parsed_list:
        await message.answer(
            "❌ Не распознано ни одного прокси.\n"
            "Проверьте формат — см. подсказку выше.",
        )
        await state.clear()
        return

    status_msg = await message.answer(f"⏳ Импорт {len(parsed_list)} прокси...")
    await state.clear()

    added, duplicates = await import_proxies_to_db(
        user_id=message.from_user.id,
        parsed_list=parsed_list,
        rotation_mode="sticky",
    )

    await status_msg.edit_text(
        f"✅ <b>Импорт завершён</b>\n\n"
        f"🟢 Добавлено: <b>{added}</b>\n"
        f"♻️ Обновлено (дубликаты): <b>{duplicates}</b>\n"
        f"❌ Не распознано: <b>{failed}</b>",
        parse_mode="HTML",
    )

    # Обновляем список прокси
    proxies = list(await session.scalars(
        select(Proxy).where(Proxy.user_id == message.from_user.id).order_by(Proxy.created_at)
    ))
    await message.answer(
        f"🌐 <b>Loma Proxy</b>\n\nВсего: <b>{len(proxies)}</b>",
        parse_mode="HTML",
        reply_markup=get_proxies_menu_keyboard(len(proxies)),
    )


@proxies_router.callback_query(F.data == "proxy_loma_rotating")
async def loma_rotating_menu(callback: CallbackQuery, state: FSMContext):
    """Меню добавления rotating backconnect-прокси Loma (gate.lomaproxy.com:7777)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await state.set_state(ProxyAddState.waiting_for_loma_rotating_creds)
    await callback.message.edit_text(
        "🌐 <b>Loma Rotating Gateway</b>\n\n"
        "Добавляет один rotating-прокси <code>gate.lomaproxy.com:7777</code> — "
        "каждое соединение будет с нового IP.\n\n"
        "Формат: <code>username:password</code>\n\n"
        "<i>Username у Loma обычно имеет формат:</i>\n"
        "<code>user</code> — rotating (каждый запрос новый IP)\n"
        "<code>user-country-de</code> — rotating с привязкой к стране\n"
        "<code>user-country-de-session-XYZ</code> — sticky session с привязкой IP",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings"),
        ]]),
    )
    await callback.answer()


@proxies_router.message(ProxyAddState.waiting_for_loma_rotating_creds, F.text)
async def loma_rotating_process(message: Message, state: FSMContext, session: AsyncSession):
    from services.loma_proxy import add_rotating_gateway
    parts = message.text.strip().split(":")
    try:
        await message.delete()
    except Exception:
        pass
    if len(parts) != 2:
        await message.answer("❌ Формат: <code>username:password</code>", parse_mode="HTML")
        await state.clear()
        return

    username, password = parts
    added = await add_rotating_gateway(
        user_id=message.from_user.id,
        username=username,
        password=password,
        proxy_type="socks5",
    )
    await state.clear()

    if added:
        await message.answer("✅ Rotating-прокси Loma добавлен.")
    else:
        await message.answer("ℹ️ Rotating-прокси обновлён (креды изменены).")

    proxies = list(await session.scalars(
        select(Proxy).where(Proxy.user_id == message.from_user.id).order_by(Proxy.created_at)
    ))
    await message.answer(
        f"🌐 <b>Loma Proxy</b>\n\nВсего: <b>{len(proxies)}</b>",
        parse_mode="HTML",
        reply_markup=get_proxies_menu_keyboard(len(proxies)),
    )
