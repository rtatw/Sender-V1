"""
loma_proxy.py — интеграция с Loma Proxy API для автозагрузки прокси.

Loma Proxy — провайдер резидентных и datacenter-прокси.
API документация: https://docs.lomaproxy.com/

Поддерживаемые операции:
  - get_proxies(api_key, proxy_type='socks5', country=None, limit=100)
  - import_proxies_to_db(user_id, api_key, proxy_type='socks5', ...)
  - check_balance(api_key)

Авторизация: Bearer token в заголовке Authorization.

Пример URL Loma rotating gateway (backconnect):
  socks5://user-session-abc:password@gate.lomaproxy.com:7777
Каждое соединение — новый IP. Для rotating-прокси rotation_mode='rotating'.
"""
import logging
from typing import Optional
import httpx
from sqlalchemy import select
from database.engine import async_session
from database.models import Proxy

logger = logging.getLogger(__name__)

LOMA_API_BASE = "https://api.lomaproxy.com/v1"
LOMA_GATE_HOST = "gate.lomaproxy.com"  # backconnect rotating gateway
LOMA_GATE_PORT = 7777


class LomaProxyService:
    """Сервис для работы с Loma Proxy API."""

    async def get_proxies(
        self,
        api_key: str,
        proxy_type: str = "socks5",
        country: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Получает список статических прокси через Loma API.

        :param api_key: API ключ Loma
        :param proxy_type: 'socks5', 'http', или 'socks4'
        :param country: код страны (None = любая)
        :param limit: сколько прокси вернуть
        :return: список dict с полями host, port, username, password, proxy_type
        """
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        params = {
            "type": proxy_type,
            "limit": limit,
        }
        if country:
            params["country"] = country

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    f"{LOMA_API_BASE}/proxies",
                    headers=headers,
                    params=params,
                )
                if resp.status_code != 200:
                    logger.error("Loma API error %d: %s", resp.status_code, resp.text[:200])
                    return []
                data = resp.json()
                # Формат ответа: {"data": [{"host":..., "port":..., "username":..., "password":...}]}
                items = data.get("data", []) or data.get("proxies", [])
                result = []
                for item in items:
                    result.append({
                        "host": item.get("host", ""),
                        "port": int(item.get("port", 0)),
                        "username": item.get("username", ""),
                        "password": item.get("password", ""),
                        "proxy_type": proxy_type,
                    })
                return result
            except Exception as e:
                logger.error("Loma API request failed: %s", e)
                return []

    async def import_static_to_db(
        self,
        user_id: int,
        api_key: str,
        proxy_type: str = "socks5",
        country: Optional[str] = None,
        limit: int = 100,
        rotation_mode: str = "sticky",
    ) -> tuple[int, int]:
        """Импортирует статические прокси из Loma в БД.

        :return: (added, failed)
        """
        proxies = await self.get_proxies(api_key, proxy_type, country, limit)
        if not proxies:
            return 0, 0

        added = 0
        failed = 0
        async with async_session() as s:
            for p in proxies:
                if not p["host"] or p["port"] <= 0:
                    failed += 1
                    continue
                # Проверяем, нет ли уже такого прокси
                existing = await s.scalar(
                    select(Proxy).where(
                        Proxy.user_id == user_id,
                        Proxy.host == p["host"],
                        Proxy.port == p["port"],
                    )
                )
                if existing:
                    failed += 1  # already exists
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
        logger.info("Loma import: %d added, %d failed (user %d)", added, failed, user_id)
        return added, failed

    async def import_rotating_to_db(
        self,
        user_id: int,
        username: str,
        password: str,
        proxy_type: str = "socks5",
        country: Optional[str] = None,
    ) -> int:
        """Добавляет rotating backconnect-прокси Loma (gate.lomaproxy.com:7777).

        :param username: логин Loma (с подстановкой страны/сессии по желанию)
        :param password: пароль Loma
        :return: 1 если добавлено, 0 если уже было

        Формат username у Loma для rotating-доступа:
          "user" — просто rotating (каждый запрос новый IP)
          "user-country-de-session-XYZ" — sticky session с привязкой IP
        """
        async with async_session() as s:
            existing = await s.scalar(
                select(Proxy).where(
                    Proxy.user_id == user_id,
                    Proxy.host == LOMA_GATE_HOST,
                    Proxy.port == LOMA_GATE_PORT,
                )
            )
            if existing:
                return 0
            s.add(Proxy(
                user_id=user_id,
                host=LOMA_GATE_HOST,
                port=LOMA_GATE_PORT,
                username=username,
                password=password,
                proxy_type=proxy_type,
                rotation_mode="rotating",  # ✅ для rotating backconnect
                status="unknown",
            ))
            await s.commit()
        return 1

    async def check_balance(self, api_key: str) -> tuple[bool, str]:
        """Проверяет баланс аккаунта Loma.

        :return: (success, message)
        """
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(f"{LOMA_API_BASE}/balance", headers=headers)
                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
                data = resp.json()
                balance = data.get("balance", "—")
                traffic = data.get("traffic_used", "—")
                return True, f"Баланс: {balance}, трафик: {traffic}"
            except Exception as e:
                return False, str(e)


# Глобальный синглтон
_loma_instance: Optional[LomaProxyService] = None


def get_loma_service() -> LomaProxyService:
    global _loma_instance
    if _loma_instance is None:
        _loma_instance = LomaProxyService()
    return _loma_instance
