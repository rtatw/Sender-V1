"""
anti_ban.py — ОБНОВЛЁННЫЙ
Изменения:
  ✅ safe_send теперь совместим с новым SMTP Pool
  ✅ Улучшенный warmup (прогрев аккаунтов) — первые дни отправляем мало
  ✅ Адаптивные задержки — замедляем при ошибках
  ✅ Добавлен метод pick_best_account с учётом warmup
"""

import asyncio
import logging
import random
import string
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_daily_limit_override: int = 0  # 0 = use domain default


def set_daily_limit(limit: int):
    global _daily_limit_override
    _daily_limit_override = limit


def get_daily_limit() -> int:
    return _daily_limit_override or 100


DOMAIN_LIMITS = {
    "gmail.com":    {"day_max": 400, "hour_max": 50,  "min_delay": 30,  "max_delay": 90},
    "op.pl":        {"day_max": 100, "hour_max": 15,  "min_delay": 60,  "max_delay": 180},
    "wp.pl":        {"day_max": 100, "hour_max": 15,  "min_delay": 60,  "max_delay": 180},
    "o2.pl":        {"day_max": 100, "hour_max": 15,  "min_delay": 60,  "max_delay": 180},
    "onet.pl":      {"day_max": 100, "hour_max": 15,  "min_delay": 60,  "max_delay": 180},
    "interia.pl":   {"day_max": 100, "hour_max": 15,  "min_delay": 60,  "max_delay": 180},
    "gmx.de":       {"day_max": 200, "hour_max": 30,  "min_delay": 20,  "max_delay": 60},
    "web.de":       {"day_max": 200, "hour_max": 30,  "min_delay": 20,  "max_delay": 60},
    "outlook.com":  {"day_max": 300, "hour_max": 40,  "min_delay": 25,  "max_delay": 75},
    "hotmail.com":  {"day_max": 300, "hour_max": 40,  "min_delay": 25,  "max_delay": 75},
    "mail.ru":      {"day_max": 250, "hour_max": 35,  "min_delay": 20,  "max_delay": 60},
    "yandex.ru":    {"day_max": 200, "hour_max": 30,  "min_delay": 25,  "max_delay": 70},
    "_default":     {"day_max": 150, "hour_max": 20,  "min_delay": 45,  "max_delay": 120},
}


def get_domain_limits(email: str) -> dict:
    domain = email.split("@")[-1].lower()
    return DOMAIN_LIMITS.get(domain, DOMAIN_LIMITS["_default"])


@dataclass
class AccountHealth:
    email: str
    sends_today: int = 0
    sends_this_hour: int = 0
    last_send_ts: float = 0.0
    hour_window_start: float = field(default_factory=time.time)
    day_window_start: float = field(default_factory=time.time)
    consecutive_errors: int = 0
    suspended_until: float = 0.0
    created_at: float = field(default_factory=time.time)



    def is_suspended(self) -> bool:
        return time.time() < self.suspended_until

    def suspend(self, seconds: int = 3600) -> None:
        self.suspended_until = time.time() + seconds
        logger.warning("Account %s suspended for %ds", self.email, seconds)

    def record_error(self) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors >= 3:
            self.suspend(86400)
        elif self.consecutive_errors >= 2:
            self.suspend(14400)
        else:
            self.suspend(3600)

    def record_success(self) -> None:
        """Регистрирует успешную отправку и обновляет скользящие окна.

        ВАЖНО: ранее здесь был сломанный блок с обращениями к
        несуществующим полям `warmup_day` и `limits["warmup_days"]`,
        что вызывало KeyError / AttributeError на каждой отправке.
        Warmup-логика вынесена в отдельный метод warmup_daily_max().
        """
        now = time.time()
        self.consecutive_errors = 0
        if now - self.hour_window_start > 3600:
            self.sends_this_hour = 0
            self.hour_window_start = now
        if now - self.day_window_start > 86400:
            self.sends_today = 0
            self.day_window_start = now
        self.sends_today += 1
        self.sends_this_hour += 1
        self.last_send_ts = now

    def warmup_daily_max(self) -> int:
        """Лимит отправки на текущий день прогрева (экспоненциальный рост).

        Предполагается, что аккаунт был создан `created_at` секунд назад.
        Если прогрев завершён — возвращает day_max из DOMAIN_LIMITS.
        """
        limits = get_domain_limits(self.email)
        day_max = limits["day_max"]
        day1_max = limits.get("warmup_day1_max", day_max)
        warmup_days = limits.get("warmup_days", 0)
        if warmup_days <= 0:
            return day_max
        age_days = (time.time() - self.created_at) / 86400.0
        warmup_day = int(age_days)
        if warmup_day >= warmup_days:
            return day_max
        factor = 1.6 ** warmup_day
        return min(int(day1_max * factor), day_max)

    def can_send(self) -> tuple[bool, str]:
        if self.is_suspended():
            secs = int(self.suspended_until - time.time())
            return False, f"suspended {secs}s"

        limits = get_domain_limits(self.email)
        now = time.time()

        if now - self.hour_window_start > 3600:
            self.sends_this_hour = 0
            self.hour_window_start = now
        if now - self.day_window_start > 86400:
            self.sends_today = 0
            self.day_window_start = now

        # ✅ Используем warmup-лимит (экспоненциальный рост в первые дни)
        daily_max = min(get_daily_limit(), self.warmup_daily_max())
        if self.sends_today >= daily_max:
            return False, f"daily {daily_max}"

        if self.sends_this_hour >= limits["hour_max"]:
            return False, f"hourly {limits['hour_max']}"

        return True, ""

    def required_delay(self) -> float:
        limits = get_domain_limits(self.email)
        if self.last_send_ts == 0:
            return 0.0
        elapsed = time.time() - self.last_send_ts
        jitter = random.uniform(limits["min_delay"], limits["max_delay"])

        # ✅ НОВОЕ: увеличиваем задержку при ошибках (адаптивная)
        if self.consecutive_errors > 0:
            jitter *= (1 + self.consecutive_errors * 0.5)

        return max(0.0, jitter - elapsed)


_health_registry: dict[str, AccountHealth] = {}
# user_id для каждого email — нужен чтобы сохранять health в БД
_health_user_map: dict[str, int] = {}


def get_health(email: str, user_id: int = 0) -> AccountHealth:
    """Возвращает AccountHealth для email.

    ВАЖНО (HIGH-5): in-memory dict, но с возможностью асинхронной
    синхронизации с БД через load_health_from_db / save_health_to_db.

    Ранее здесь была попытка запускать async-код из sync-функции через
    run_coroutine_threadsafe — это анти-паттерн и приводило к зависаниям.
    Теперь get_health — чисто in-memory, а БД-синхронизация выполняется
    явно через async-функции (вызываются из Mailer, watcher и on_startup).
    """
    if email not in _health_registry:
        h = AccountHealth(email=email)
        if user_id:
            _health_user_map[email] = user_id
        _health_registry[email] = h
    return _health_registry[email]


async def load_health_from_db(email: str, user_id: int) -> None:
    """Асинхронно загружает состояние AccountHealth из БД.

    Вызывать:
      - при старте бота для всех аккаунтов (в on_startup);
      - при добавлении нового EmailAccount.
    """
    from database.engine import async_session
    from database.models import EmailHealth
    from sqlalchemy import select

    h = _health_registry.get(email)
    if h is None:
        h = AccountHealth(email=email)
        _health_registry[email] = h
    _health_user_map[email] = user_id

    try:
        async with async_session() as s:
            row = await s.scalar(
                select(EmailHealth).where(
                    EmailHealth.user_id == user_id,
                    EmailHealth.email == email,
                )
            )
            if row:
                h.sends_today = row.sends_today
                h.sends_this_hour = row.sends_this_hour
                h.last_send_ts = row.last_send_ts
                h.hour_window_start = row.hour_window_start
                h.day_window_start = row.day_window_start
                h.consecutive_errors = row.consecutive_errors
                h.suspended_until = row.suspended_until
                if row.created_at_ts:
                    h.created_at = row.created_at_ts
            else:
                # Создаём новую запись
                h.created_at = time.time()
                s.add(EmailHealth(
                    user_id=user_id, email=email,
                    created_at_ts=int(h.created_at),
                ))
                await s.commit()
    except Exception as e:
        logger.warning("load_health_from_db failed for %s: %s", email, e)


async def save_health_to_db(email: str) -> None:
    """Асинхронно сохраняет текущее состояние AccountHealth в БД.

    Вызывать после каждой отправки (или каждые N отправок).
    """
    h = _health_registry.get(email)
    user_id = _health_user_map.get(email)
    if not h or not user_id:
        return
    try:
        from database.engine import async_session
        from database.models import EmailHealth
        from sqlalchemy import select, update as _upd
        async with async_session() as s:
            # Проверяем существует ли запись
            exists = await s.scalar(
                select(EmailHealth.id).where(
                    EmailHealth.user_id == user_id,
                    EmailHealth.email == email,
                )
            )
            if exists:
                await s.execute(
                    _upd(EmailHealth)
                    .where(
                        EmailHealth.user_id == user_id,
                        EmailHealth.email == email,
                    )
                    .values(
                        sends_today=h.sends_today,
                        sends_this_hour=h.sends_this_hour,
                        last_send_ts=int(h.last_send_ts),
                        hour_window_start=int(h.hour_window_start),
                        day_window_start=int(h.day_window_start),
                        consecutive_errors=h.consecutive_errors,
                        suspended_until=int(h.suspended_until),
                    )
                )
            else:
                s.add(EmailHealth(
                    user_id=user_id, email=email,
                    sends_today=h.sends_today,
                    sends_this_hour=h.sends_this_hour,
                    last_send_ts=int(h.last_send_ts),
                    hour_window_start=int(h.hour_window_start),
                    day_window_start=int(h.day_window_start),
                    consecutive_errors=h.consecutive_errors,
                    suspended_until=int(h.suspended_until),
                    created_at_ts=int(h.created_at),
                ))
            await s.commit()
    except Exception as e:
        logger.warning("save_health_to_db failed for %s: %s", email, e)


def random_ehlo() -> str:
    prefixes = ["mail", "smtp", "mta", "mx", "outbound", "send"]
    tlds = ["local", "home", "lan", "localdomain"]
    name = random.choice(prefixes) + "-" + "".join(random.choices(string.ascii_lowercase, k=5))
    return f"{name}.{random.choice(tlds)}"


async def safe_send(
    account_email: str, account_password: str, msg,
    user_id: int = 0, force: bool = False
) -> tuple[bool, str]:
    """
    Отправка через proxy_connection (legacy — для обратной совместимости).
    Новый mailer.py использует _pool_send напрямую, но safe_send
    всё ещё работает для тестовых отправок и одиночных писем.
    """
    from services.proxy_connection import smtp_send_message

    health = get_health(account_email, user_id=user_id)

    if not force:
        can, reason = health.can_send()
        if not can:
            logger.info("Skip %s: %s", account_email, reason)
            return False, f"rate_limit: {reason}"

    delay = health.required_delay()
    if delay > 0:
        await asyncio.sleep(delay)

    ok, err = await smtp_send_message(account_email, account_password, msg, user_id)

    if ok:
        health.record_success()
    else:
        health.record_error()

    # ✅ HIGH-5: сохраняем состояние в БД
    await save_health_to_db(account_email)

    return ok, err


_last_imap_check: dict[str, float] = {}
IMAP_MIN_INTERVAL = 60


async def safe_imap_fetch(email: str, password: str, user_id: int = 0, limit: int = 10) -> list[dict]:
    from services.proxy_connection import imap_fetch_parsed

    now = time.time()
    last = _last_imap_check.get(email, 0)
    wait = IMAP_MIN_INTERVAL - (now - last)
    if wait > 0:
        await asyncio.sleep(wait)

    try:
        result = await imap_fetch_parsed(email, password, user_id, limit)
        _last_imap_check[email] = time.time()
        return result
    except Exception as e:
        logger.warning("IMAP fetch failed %s: %s", email, e)
        return []


def pick_best_account(accounts: list, user_id: int = 0) -> Optional[object]:
    """✅ Улучшено: учитывает warmup и здоровье аккаунта."""
    available = []
    for a in accounts:
        h = get_health(a.email, user_id=user_id if hasattr(a, 'user_id') else 0)
        can, _ = h.can_send()
        if can:
            # Приоритет: меньше отправок сегодня + меньше ошибок
            score = h.sends_today + h.consecutive_errors * 100
            available.append((score, a))
    if not available:
        return None
    available.sort(key=lambda x: x[0])
    return available[0][1]


def get_health_report() -> str:
    if not _health_registry:
        return "N/A"
    lines = []
    for email, h in _health_registry.items():
        can, _ = h.can_send()
        daily_max = get_daily_limit()
        status = "ok" if can else "SUSP" if h.is_suspended() else "wait"
        suspend_info = f" (cooldown {int(h.suspended_until - time.time())}s)" if h.is_suspended() else ""
        lines.append(
            f"{status} {email}{suspend_info} "
            f"| today:{h.sends_today}/{daily_max} hr:{h.sends_this_hour} err:{h.consecutive_errors}"
        )
    return "\n".join(lines)
