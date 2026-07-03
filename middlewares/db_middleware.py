import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession
from database.engine import async_session

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with async_session() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                # FIX HIGH-06: автокоммит после успешной обработки.
                # Handlers могут делать session.commit() явно для промежуточных
                # сохранений — это не конфликтует: повторный commit() на чистой
                # сессии — безопасная no-op операция.
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
