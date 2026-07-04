"""
loma_proxy.py — импорт прокси из списка @LomaProxyBot.

@LomaProxyBot — Telegram-бот, который продаёт резидентные SOCKS5-прокси.
Прокси выдаются прямо в чат в одном из форматов:

    host:port:login:password
    login:password@host:port
    socks5://login:password@host:port
    socks5://host:port
    host:port            (для backconnect gate без авторизации)

Этот модуль НЕ использует REST API (у Loma нет публичного REST API) —
он парсит список, который пользователь копирует из @LomaProxyBot и
вставляет в нашего бота одним сообщением.

Поддерживаемые форматы строки (одна строка = один прокси):
    1. socks5://login:password@host:port       ← рекомендуемый
    2. http://login:password@host:port
    3. socks5://host:port                      ← без авторизации
    4. login:password@host:port                ← авто-определение типа
    5. host:port:login:password                ← авто-определение типа
    6. host:port                               ← SOCKS5 без авторизации
    7. host:port:login:password:country        ← Loma-расширенный формат
       (страна отбрасывается, не используется)
"""

import logging
import re
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import select
from database.engine import async_session
from database.models import Proxy

logger = logging.getLogger(__name__)


def parse_loma_proxy_line(line: str) -> Optional[dict]:
    """Парсит ОДНУ строку прокси из @LomaProxyBot.

    Возвращает dict {host, port, username, password, proxy_type, rotation_mode}
    или None если строка не распознана.

    Авто-определение типа:
      - Если есть scheme (socks5://, http://) — используем его.
      - Если нет — по умолчанию "socks5" (Loma продаёт в основном SOCKS5).
    """
    line = line.strip()
    if not line:
        return None

    # Удаляем лишние пробелы и комментарии
    if "#" in line:
        line = line.split("#")[0].strip()
    if not line:
        return None

    host = port = username = password = ""
    proxy_type = "socks5"  # по умолчанию (Loma в основном SOCKS5)

    # Формат 1-3: URL с scheme
    if "://" in line:
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

    # Формат 4: login:password@host:port
    elif "@" in line:
        at_idx = line.index("@")
        left, right = line[:at_idx], line[at_idx + 1:]
        r_parts, l_parts = right.split(":"), left.split(":")
        if len(r_parts) >= 2 and r_parts[1].isdigit():
            host = r_parts[0]
            port = int(r_parts[1])
            username = l_parts[0] if len(l_parts) >= 1 else ""
            password = l_parts[1] if len(l_parts) >= 2 else ""
        elif len(l_parts) >= 2 and l_parts[1].isdigit():
            host = l_parts[0]
            port = int(l_parts[1])
            username = r_parts[0] if len(r_parts) >= 1 else ""
            password = r_parts[1] if len(r_parts) >= 2 else ""
        else:
            return None

    # Формат 5-7: host:port[:login:password[:extra]]
    else:
        parts = line.split(":")
        if len(parts) == 2:
            host, port_str = parts
            port = int(port_str) if port_str.isdigit() else 0
        elif len(parts) == 4:
            # host:port:login:password
            host, port_str, username, password = parts
            port = int(port_str) if port_str.isdigit() else 0
        elif len(parts) == 5:
            # host:port:login:password:country (Loma-расширенный)
            host, port_str, username, password, _country = parts
            port = int(port_str) if port_str.isdigit() else 0
        elif len(parts) == 3:
            # host:port:login (без пароля)
            host, port_str, username = parts
            port = int(port_str) if port_str.isdigit() else 0
        else:
            return None

    # Валидация
    if not host or port <= 0 or port > 65535:
        return None
    if not re.match(r"^[\w.\-]+$", host):
        return None

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "proxy_type": proxy_type,
        "rotation_mode": "sticky",  # по умолчанию; rotating ставится отдельной кнопкой
    }


def parse_loma_proxy_list(text: str) -> tuple[list[dict], int]:
    """Парсит многострочный список прокси из @LomaProxyBot.

    :return: (parsed_list, failed_count)
    """
    parsed_list = []
    failed = 0
    for line in text.splitlines():
        result = parse_loma_proxy_line(line)
        if result:
            parsed_list.append(result)
        else:
            if line.strip():
                failed += 1
    return parsed_list, failed


async def import_proxies_to_db(
    user_id: int,
    parsed_list: list[dict],
    rotation_mode: str = "sticky",
) -> tuple[int, int]:
    """Импортирует список распарсенных прокси в БД.

    :return: (added, duplicates) — duplicates = уже существующие в БД
    """
    added = 0
    duplicates = 0
    async with async_session() as s:
        for p in parsed_list:
            # Проверяем, нет ли уже такого прокси (по host:port)
            existing = await s.scalar(
                select(Proxy).where(
                    Proxy.user_id == user_id,
                    Proxy.host == p["host"],
                    Proxy.port == p["port"],
                )
            )
            if existing:
                duplicates += 1
                # Если существующий — обновляем пароль/тип на случай если изменились
                existing.username = p["username"]
                existing.password = p["password"]
                existing.proxy_type = p["proxy_type"]
                existing.rotation_mode = rotation_mode
                continue

            s.add(Proxy(
                user_id=user_id,
                host=p["host"],
                port=p["port"],
                username=p["username"],
                password=p["password"],
                proxy_type=p["proxy_type"],
                rotation_mode=rotation_mode,
                status="unknown",
            ))
            added += 1
        await s.commit()

    logger.info("Loma import: %d added, %d duplicates (user %d)",
                added, duplicates, user_id)
    return added, duplicates


async def add_rotating_gateway(
    user_id: int,
    username: str,
    password: str,
    proxy_type: str = "socks5",
    host: str = "gate.lomaproxy.com",
    port: int = 7777,
) -> int:
    """Добавляет rotating backconnect-прокси (gate.lomaproxy.com:7777).

    У Loma rotating-доступ обычно через backconnect gate — один host:port,
    но каждый запрос с нового IP. Формат username:
        "user"                       — rotating (каждый запрос новый IP)
        "user-country-de-session-XYZ" — sticky session с привязкой IP
        "user-country-de"            — rotating с привязкой к стране

    :return: 1 если добавлено, 0 если уже есть
    """
    async with async_session() as s:
        existing = await s.scalar(
            select(Proxy).where(
                Proxy.user_id == user_id,
                Proxy.host == host,
                Proxy.port == port,
            )
        )
        if existing:
            # Обновляем креды если изменились
            existing.username = username
            existing.password = password
            existing.proxy_type = proxy_type
            existing.rotation_mode = "rotating"
            await s.commit()
            return 0

        s.add(Proxy(
            user_id=user_id,
            host=host,
            port=port,
            username=username,
            password=password,
            proxy_type=proxy_type,
            rotation_mode="rotating",
            status="unknown",
        ))
        await s.commit()
        return 1
