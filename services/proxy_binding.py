"""
proxy_binding.py — Шаг 2: Привязка прокси к аккаунту (с персистентным хранением).

Изменения (HIGH-12 / CRIT-11):
  ✅ Привязки хранятся в БД (ProxyBinding), а не в in-memory dict.
    Ранее md5(email) % len(proxies) ломался при добавлении/удалении прокси —
    все аккаунты получали новые IP и бан-лись почтовыми провайдерами.
  ✅ Поддержка rotation_mode: "sticky" (привязка) или "rotating" (каждый
    запрос с нового IP, привязка не нужна).
  ✅ Если привязанный прокси умер — автоматически пере-привязываемся к
    ближайшему живому, с записью в БД и логированием.
"""

import datetime
import logging
from typing import Optional

from sqlalchemy import select, update

from database.engine import async_session
from database.models import ProxyBinding, Proxy

logger = logging.getLogger(__name__)


class ProxyBinder:
    """Персистентная привязка email → proxy через таблицу proxy_bindings.

    В отличие от старой версии на md5-хеше, эта привязка:
      - не меняется при добавлении/удалении других прокси;
      - переживает рестарт бота;
      - явно логирует пере-привязки.
    """

    # In-memory кеш только для ускорения в пределах одной сессии рассылки.
    # Авторитет — БД.
    _cache: dict[tuple[int, str], int] = {}

    @classmethod
    async def bind(cls, user_id: int, email: str, proxies: list) -> Optional[Proxy]:
        """Вернуть прокси для данного email.

        - Если прокси нет — None (прямое соединение).
        - Если все прокси rotating — возвращаем случайный (привязка не нужна).
        - Если есть sticky-прокси — ищем привязку в БД, валидируем, возвращаем.
        - Если привязка отсутствует или прокси умер — создаём новую.
        """
        if not proxies:
            return None

        # Если среди прокси есть rotating-прокси и нет sticky — не привязываем
        sticky_proxies = [p for p in proxies if (p.rotation_mode or "sticky") == "sticky"]
        if not sticky_proxies:
            # Все rotating — возвращаем первый (или любой, IP меняется сам на стороне Loma)
            return proxies[0]

        # 1) Ищем существующую привязку в кеше
        cache_key = (user_id, email)
        cached_proxy_id = cls._cache.get(cache_key)

        # 2) Ищем в БД
        async with async_session() as s:
            binding = await s.scalar(
                select(ProxyBinding).where(
                    ProxyBinding.user_id == user_id,
                    ProxyBinding.email == email,
                )
            )

            candidate_id = cached_proxy_id or (binding.proxy_id if binding else None)
            if candidate_id:
                # Проверяем, жив ли ещё привязанный прокси
                proxy = await s.scalar(
                    select(Proxy).where(Proxy.id == candidate_id)
                )
                if proxy and proxy.is_active and proxy.status == "alive":
                    # Обновляем last_used_at
                    if binding:
                        binding.last_used_at = datetime.datetime.now()
                        await s.commit()
                    cls._cache[cache_key] = proxy.id
                    return proxy
                # Прокси умер или удалён — пере-привязываем
                logger.warning(
                    "ProxyBinder: proxy_id=%s для %s умер/удалён — пере-привязка",
                    candidate_id, email,
                )

            # 3) Создаём новую привязку: первый живой sticky-прокси
            for p in sticky_proxies:
                if p.is_active and p.status == "alive":
                    if binding:
                        binding.proxy_id = p.id
                        binding.last_used_at = datetime.datetime.now()
                    else:
                        binding = ProxyBinding(
                            user_id=user_id,
                            email=email,
                            proxy_id=p.id,
                            last_used_at=datetime.datetime.now(),
                        )
                        s.add(binding)
                    await s.commit()
                    cls._cache[cache_key] = p.id
                    logger.info("ProxyBinder: %s → %s:%s (id=%d, новая привязка)",
                                email, p.host, p.port, p.id)
                    return p

            # 4) Живых прокси нет — возвращаем первый sticky (пусть попытается, ошибку увидим)
            logger.warning("ProxyBinder: нет живых sticky-прокси для %s", email)
            return sticky_proxies[0]

    @classmethod
    async def rebind(cls, user_id: int, email: str, proxies: list) -> Optional[Proxy]:
        """Принудительно переназначить прокси (при ошибках)."""
        # Сбрасываем кеш
        cls._cache.pop((user_id, email), None)
        # Удаляем привязку из БД — bind() создаст новую
        async with async_session() as s:
            await s.execute(
                update(ProxyBinding).where(
                    ProxyBinding.user_id == user_id,
                    ProxyBinding.email == email,
                ).values(proxy_id=None) if False else
                # SQLAlchemy не умеет DELETE через update с None, делаем через select+delete
                select(ProxyBinding).where(
                    ProxyBinding.user_id == user_id,
                    ProxyBinding.email == email,
                )
            )
            # Удаляем привязку явно
            from sqlalchemy import delete as _delete
            await s.execute(
                _delete(ProxyBinding).where(
                    ProxyBinding.user_id == user_id,
                    ProxyBinding.email == email,
                )
            )
            await s.commit()
        return await cls.bind(user_id, email, proxies)

    @classmethod
    async def get_binding_report(cls, user_id: int, proxies: list) -> str:
        """Отчёт о текущих привязках пользователя."""
        async with async_session() as s:
            bindings = list(await s.scalars(
                select(ProxyBinding).where(ProxyBinding.user_id == user_id)
            ))
        if not bindings:
            return "Нет привязок"
        proxy_map = {p.id: p for p in proxies}
        lines = []
        for b in bindings:
            p = proxy_map.get(b.proxy_id)
            if p:
                lines.append(f"📧 {b.email} → 🌐 {p.host}:{p.port} ({p.proxy_type})")
            else:
                lines.append(f"📧 {b.email} → ⚠️ proxy_id={b.proxy_id} (не найден)")
        return "\n".join(lines)

    @classmethod
    def clear_cache(cls):
        cls._cache.clear()


# Глобальный синглтон-фасад (для обратной совместимости со старым API)
class _ProxyBinderFacade:
    """Фасад, чтобы старый код `get_proxy_binder().bind(email, proxies)`
    продолжал работать. Внутри вызывает classmethod ProxyBinder.

    ВАЖНО: новый API требует user_id. Старый код передавал только email.
    Поэтому фасад пытается вытащить user_id из прокси (это не идеально —
    лучше обновить вызовы на ProxyBinder.bind(user_id, email, proxies)).
    """
    async def bind(self, email: str, proxies: list) -> Optional[Proxy]:
        if not proxies:
            return None
        # Берём user_id из первого прокси (все прокси в списке принадлежат одному user_id)
        user_id = proxies[0].user_id
        return await ProxyBinder.bind(user_id, email, proxies)

    async def rebind(self, email: str, proxies: list) -> Optional[Proxy]:
        if not proxies:
            return None
        user_id = proxies[0].user_id
        return await ProxyBinder.rebind(user_id, email, proxies)

    async def get_binding_report(self, proxies: list) -> str:
        if not proxies:
            return "Нет прокси"
        user_id = proxies[0].user_id
        return await ProxyBinder.get_binding_report(user_id, proxies)

    def clear(self):
        ProxyBinder.clear_cache()


_binder_instance: Optional[_ProxyBinderFacade] = None


def get_proxy_binder() -> _ProxyBinderFacade:
    global _binder_instance
    if _binder_instance is None:
        _binder_instance = _ProxyBinderFacade()
    return _binder_instance
