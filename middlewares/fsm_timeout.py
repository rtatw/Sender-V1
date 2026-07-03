import time
import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)


class FSMTimeoutMiddleware(BaseMiddleware):
    def __init__(self, timeout: int = 600):
        self.timeout = timeout

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        state: FSMContext = data.get("state")
        if state:
            current = await state.get_state()
            if current:
                raw = await state.get_data()
                ts = raw.get("_fsm_ts", 0)
                if ts and time.time() - ts > self.timeout:
                    await state.clear()
                    uid = None
                    if isinstance(event, Message):
                        uid = event.from_user.id
                    elif isinstance(event, CallbackQuery):
                        uid = event.from_user.id
                    if uid:
                        logger.info("FSM timed out for user %d", uid)
                    return
                if not ts:
                    # Only set timer on first entry into state, not on every event
                    await state.update_data(_fsm_ts=time.time())
        return await handler(event, data)
