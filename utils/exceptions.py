"""
Custom exception classes and global error handler.
All business errors inherit from BotException.
"""
import logging
import traceback
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)


class BotException(Exception):
    def __init__(self, message: str = "Произошла техническая ошибка", user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or "⚠️ Произошла техническая ошибка, мы уже чиним."


class ServiceUnavailableError(BotException):
    def __init__(self, service_name: str, detail: str = "") -> None:
        msg = f"Service unavailable: {service_name}"
        if detail: msg += f" — {detail}"
        super().__init__(message=msg, user_message=f"⚠️ Сервис {service_name} временно недоступен.")
        self.service_name = service_name


class ValidationError(BotException):
    def __init__(self, message: str = "Некорректные данные") -> None:
        super().__init__(message=message, user_message=f"❌ {message}")


class DatabaseError(BotException):
    def __init__(self, detail: str = "") -> None:
        super().__init__(message=f"Database error: {detail}", user_message="⚠️ Ошибка базы данных.")


class RateLimitError(BotException):
    def __init__(self, service_name: str = "") -> None:
        super().__init__(message=f"Rate limit exceeded: {service_name}", user_message="⏳ Слишком много запросов.")


async def global_error_handler(event: ErrorEvent) -> None:
    exc = event.exception
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.critical("Unhandled exception:\n%s", tb)
    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(
                "⚠️ Техническая ошибка, мы уже чиним.", show_alert=True
            )
        elif event.update.message:
            await event.update.message.answer("⚠️ Произошла техническая ошибка, мы уже чиним.")
    except Exception:
        pass

