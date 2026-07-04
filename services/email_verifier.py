"""
email_verifier.py — единый интерфейс проверки существования email.

Поддерживает 3 метода (выбирается в GlobalSettings.email_verify_method):

1. **smtp_bypass** (ХОЛОДНЫЙ ПОДБОР, по умолчанию) — бесплатная локальная
   проверка через SMTP RCPT TO. Прямое подключение к MX-серверу получателя,
   команда RCPT TO. Без внешних API. Использует services.smtp_bypass_checker.
   Поддерживает прокси для обхода блокировок SMTP-портов.

2. **mailtester** — через mailtester.ninja API (нужны ключи, платно).
   Использует services.mailtester_ninja.

3. **both** — гибрид: сначала SMTP (быстро и бесплатно), если SMTP дал
   неопределённый результат (TIMEOUT/CATCHALL) — проверка mailtester'ом.

Все методы возвращают единый формат:
    {
        "exists": bool,           # True если mailbox существует
        "code": str,              # "ok" | "ko" | "catchall" | "timeout" | "error"
        "method": str,            # какой метод дал ответ
        "details": str,           # человекочитаемое описание
    }
"""
import logging
from typing import Optional
from sqlalchemy import select

from database.engine import async_session
from database.models import GlobalSettings, Proxy
from services.smtp_bypass_checker import SmtpBypassChecker, ProxyConfig

logger = logging.getLogger(__name__)

# Глобальный singleton checker
_smtp_checker: Optional[SmtpBypassChecker] = None


def _get_smtp_checker() -> SmtpBypassChecker:
    global _smtp_checker
    if _smtp_checker is None:
        _smtp_checker = SmtpBypassChecker()
    return _smtp_checker


async def _get_verify_method() -> str:
    """Читает метод проверки из GlobalSettings (по умолчанию 'smtp_bypass')."""
    try:
        async with async_session() as s:
            gs = await s.scalar(select(GlobalSettings).where(GlobalSettings.id == 1))
            if gs and gs.email_verify_method:
                return gs.email_verify_method
    except Exception as e:
        logger.warning("Failed to load email_verify_method: %s", e)
    return "smtp_bypass"


async def _load_proxies(user_id: int) -> list[ProxyConfig]:
    """Загружает живые прокси пользователя для SMTP-проверки."""
    try:
        async with async_session() as s:
            rows = list(await s.scalars(
                select(Proxy).where(
                    Proxy.user_id == user_id,
                    Proxy.is_active == True,
                    Proxy.status == "alive",
                )
            ))
        return [
            ProxyConfig(
                host=p.host, port=p.port,
                username=p.username or None,
                password=p.password or None,
            )
            for p in rows
        ]
    except Exception as e:
        logger.warning("Failed to load proxies for verifier: %s", e)
        return []


# ─── Публичный API ───────────────────────────────────────────────────────────

async def verify_email(email: str, user_id: int = 0,
                       metadata: dict = None) -> dict:
    """Проверяет существование email выбранным в настройках методом.

    :param email: email для проверки
    :param user_id: для загрузки прокси (если метод smtp_bypass)
    :param metadata: доп. инфа для SMTP-метода (nick, link, price, photo)
    :return: {"exists": bool, "code": str, "method": str, "details": str}
    """
    method = await _get_verify_method()

    if method == "mailtester":
        return await _verify_mailtester(email)
    elif method == "both":
        # Сначала SMTP, если не уверен — Mailtester
        result = await _verify_smtp_bypass(email, user_id, metadata)
        if result["code"] in ("ok", "ko"):
            return result
        # TIMEOUT/CATCHALL/error — fallback на mailtester
        mt_result = await _verify_mailtester(email)
        if mt_result["code"] in ("ok", "ko"):
            return mt_result
        # Оба метода не дали ответ — возвращаем SMTP-результат
        return result
    else:  # "smtp_bypass" (по умолчанию)
        return await _verify_smtp_bypass(email, user_id, metadata)


async def _verify_smtp_bypass(email: str, user_id: int,
                                metadata: dict = None) -> dict:
    """Холодный подбор через SMTP RCPT TO (бесплатно)."""
    proxies = await _load_proxies(user_id) if user_id else []
    checker = _get_smtp_checker()
    try:
        result = await checker.verify(email, proxies=proxies, metadata=metadata or {})
        # Маппим статус SmtpBypassChecker → наш формат
        status = result.status
        if status == "VALID":
            return {
                "exists": True,
                "code": "ok",
                "method": "smtp_bypass",
                "details": f"Mailbox confirmed (score={result.score}): "
                           + ", ".join(result.details),
            }
        elif status == "INVALID":
            return {
                "exists": False,
                "code": "ko",
                "method": "smtp_bypass",
                "details": f"Mailbox not found (score={result.score}): "
                           + ", ".join(result.details),
            }
        elif status == "CATCHALL":
            return {
                "exists": True,  # catch-all принимает любые адреса
                "code": "catchall",
                "method": "smtp_bypass",
                "details": f"Domain accepts all addresses (catch-all)",
            }
        elif status == "DISPOSABLE":
            return {
                "exists": False,
                "code": "ko",
                "method": "smtp_bypass",
                "details": "Disposable email domain",
            }
        elif status in ("SYNTAX_ERROR",):
            return {
                "exists": False,
                "code": "ko",
                "method": "smtp_bypass",
                "details": "Syntax error in email",
            }
        elif status in ("ROLE",):
            # Role account (admin@, info@, etc.) — существуют, но не персональные
            return {
                "exists": True,
                "code": "ok",
                "method": "smtp_bypass",
                "details": "Role account (admin/info/etc.)",
            }
        else:  # TIMEOUT, UNKNOWN
            return {
                "exists": False,
                "code": "timeout",
                "method": "smtp_bypass",
                "details": f"Cannot verify (status={status}, score={result.score})",
            }
    except Exception as e:
        logger.warning("smtp_bypass verify failed for %s: %s", email, e)
        return {
            "exists": False,
            "code": "error",
            "method": "smtp_bypass",
            "details": f"Error: {e}",
        }


async def _verify_mailtester(email: str) -> dict:
    """Проверка через mailtester.ninja API (платно)."""
    try:
        from services.mailtester_ninja import verify_email as mt_verify
        result = await mt_verify(email)
        code = result.get("code", "error")
        if code == "ok":
            return {
                "exists": True,
                "code": "ok",
                "method": "mailtester",
                "details": "Mailbox confirmed by mailtester.ninja",
            }
        elif code == "ko":
            return {
                "exists": False,
                "code": "ko",
                "method": "mailtester",
                "details": "Mailbox not found (mailtester.ninja)",
            }
        elif code == "mb":  # mailbox full / disabled — существует но не принимает
            return {
                "exists": True,
                "code": "ok",
                "method": "mailtester",
                "details": "Mailbox exists but full/disabled",
            }
        else:
            return {
                "exists": False,
                "code": "error",
                "method": "mailtester",
                "details": result.get("message", "Unknown mailtester response"),
            }
    except Exception as e:
        logger.warning("mailtester verify failed for %s: %s", email, e)
        return {
            "exists": False,
            "code": "error",
            "method": "mailtester",
            "details": f"Error: {e}",
        }


async def get_verifier_info() -> dict:
    """Возвращает текущий метод проверки и его описание (для UI)."""
    method = await _get_verify_method()
    descriptions = {
        "smtp_bypass": (
            "Холодный подбор (SMTP RCPT TO)",
            "Бесплатная локальная проверка. Прямое подключение к MX-серверу "
            "получателя, команда RCPT TO. Без внешних API. "
            "Требует прокси для обхода блокировок SMTP-портов (25)."
        ),
        "mailtester": (
            "Mailtester.ninja API",
            "Платная проверка через mailtester.ninja. Нужны API-ключи "
            "(добавляются в админке). Быстрее, но требует баланса."
        ),
        "both": (
            "Гибрид (SMTP + Mailtester fallback)",
            "Сначала холодный SMTP-подбор (быстро и бесплатно). "
            "Если SMTP дал неопределённый результат (TIMEOUT/CATCHALL) — "
            "проверка mailtester'ом. Самый точный, но дороже."
        ),
    }
    name, desc = descriptions.get(method, ("Unknown", ""))
    return {"method": method, "name": name, "description": desc}
