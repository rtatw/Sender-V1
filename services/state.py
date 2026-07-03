from typing import Optional
import asyncio
import time
from collections import OrderedDict


class GlobalState:
    bot: Optional[object] = None


global_state = GlobalState()

_active_hunters: dict[int, object] = {}
_active_mailers: dict[int, object] = {}

# FIX MED-08: хранилище found_items в ожидании подтверждения рассылки
_pending_mailer_items: dict[int, list] = {}

# Per-user asyncio locks для атомарной проверки mailer_lock / parser_lock.
# ВАЖНО (MED-32): раньше словари росли бесконечно (один Lock на каждого
# пользователя за всё время работы бота). Теперь — LRU-кеш на 1024 записи:
# при переполнении вытесняем самые старые неиспользуемые Lock-и. Lock с
# активным waiters никогда не вытесняется.
_LOCK_CACHE_SIZE = 1024


class _LRULocks:
    """OrderedDict-based LRU-кеш для per-user asyncio.Lock.

    Lock, у которого есть активные waiters, не вытесняется (иначе
    ждущий корутин повиснет навсегда).
    """
    def __init__(self, max_size: int = _LOCK_CACHE_SIZE):
        self._locks: OrderedDict[int, asyncio.Lock] = OrderedDict()
        self._max = max_size

    def get(self, user_id: int) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        else:
            # Передвигаем в конец (наиболее используемый)
            self._locks.move_to_end(user_id)
        self._evict()
        return lock

    def _evict(self):
        # Вытесняем старые, пока не уложимся в лимит
        while len(self._locks) > self._max:
            uid, lock = next(iter(self._locks.items()))
            # Не вытесняем lock с активными waiters (занят или ждут)
            if lock.locked() or lock._waiters and any(not w.done() for w in lock._waiters):
                # Передвигаем в конец и пропускаем — попробуем следующий
                self._locks.move_to_end(uid)
                # Защита от бесконечного цикла: если все заняты — выходим
                if all(
                    l.locked() or (l._waiters and any(not w.done() for w in l._waiters))
                    for l in self._locks.values()
                ):
                    break
                continue
            del self._locks[uid]

    def clear(self):
        self._locks.clear()


_mailer_run_locks = _LRULocks()
_parser_run_locks = _LRULocks()


def get_mailer_lock(user_id: int) -> asyncio.Lock:
    return _mailer_run_locks.get(user_id)


def get_parser_lock(user_id: int) -> asyncio.Lock:
    return _parser_run_locks.get(user_id)

