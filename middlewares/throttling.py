import time
import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

logger = logging.getLogger(__name__)

# FIX HIGH-04: TTL для записей — словарь _last не будет расти бесконечно
_TTL_SECONDS = 300   # чистим записи старше 5 минут
_CLEANUP_EVERY = 500  # каждые N апдейтов


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.5):
        self.rate = rate
        self._last: dict[int, float] = {}
        self._counter = 0

    def _cleanup_stale(self) -> None:
        cutoff = time.time() - _TTL_SECONDS
        stale = [uid for uid, ts in self._last.items() if ts < cutoff]
        for uid in stale:
            del self._last[uid]
        if stale:
            logger.debug("ThrottlingMiddleware: removed %d stale entries", len(stale))

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        uid = None
        if isinstance(event, Message):
            uid = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            uid = event.from_user.id if event.from_user else None

        if uid:
            now = time.time()
            if now - self._last.get(uid, 0.0) < self.rate:
                logger.debug("Throttled user %d (rate=%.1f)", uid, self.rate)
                return
            self._last[uid] = now

            self._counter += 1
            if self._counter >= _CLEANUP_EVERY:
                self._counter = 0
                self._cleanup_stale()

        return await handler(event, data)
