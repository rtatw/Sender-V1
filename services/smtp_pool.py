"""
smtp_pool.py — SMTP Connection Pool

Поддерживает два режима:
  1. Прямое соединение (без прокси) — кешируется по (email|host|port).
     Переиспользуется до MAX_SENDS_PER_CONNECTION или IDLE_TIMEOUT.
  2. Через прокси — кешируется по (email|proxy_id|host|port).
     Ранее (HIGH-4) каждый _pool_send через прокси открывал новый TCP-туннель
     и делал SMTP handshake заново. Теперь — кеш живых прокси-соединений.
"""

import asyncio
import smtplib
import ssl
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Максимум писем через одно соединение до переподключения
MAX_SENDS_PER_CONNECTION = 80
# Максимум секунд держать соединение открытым без отправки
CONNECTION_IDLE_TIMEOUT = 120


class SMTPConnection:
    """Одно живое SMTP-соединение для одного аккаунта (прямое или через прокси)."""

    def __init__(self, email: str, password: str, host: str, port: int, use_ssl: bool,
                 proxy=None):
        """
        :param proxy: объект Proxy из БД или None для прямого соединения.
        """
        self.email = email
        self.password = password
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.proxy = proxy  # ✅ HIGH-4: прокси привязан к соединению

        self._smtp: Optional[smtplib.SMTP] = None
        self._sends = 0
        self._last_used = 0.0
        self._lock = asyncio.Lock()

    def _is_alive(self) -> bool:
        if self._smtp is None:
            return False
        if self._sends >= MAX_SENDS_PER_CONNECTION:
            return False
        if time.time() - self._last_used > CONNECTION_IDLE_TIMEOUT:
            return False
        try:
            status = self._smtp.noop()
            return status[0] == 250
        except Exception:
            return False

    def _connect(self):
        """Открыть новое SMTP-соединение (синхронно, вызывается из to_thread)."""
        ctx = ssl.create_default_context()

        if self.proxy is not None:
            # ✅ HIGH-4: соединение через прокси с переиспользованием
            from services.proxy_connection import _ProxySMTP, _ProxySMTPSSL, _wrap_ssl, _proxy_to_url
            from python_socks.sync import Proxy as SyncProxy

            url = _proxy_to_url(self.proxy)
            p = SyncProxy.from_url(url)
            raw = p.connect(dest_host=self.host, dest_port=self.port, timeout=20)
            try:
                if self.use_ssl:
                    sock = _wrap_ssl(raw, self.host, timeout=20)
                    smtp = _ProxySMTPSSL(sock, self.host, self.port, timeout=20)
                else:
                    raw.settimeout(20)
                    smtp = _ProxySMTP(raw, self.host, self.port, timeout=20)
                    smtp.ehlo()
                    if smtp.has_extn("STARTTLS"):
                        smtp.starttls(context=ctx)
                        smtp.ehlo()
            except Exception:
                try:
                    raw.close()
                except Exception:
                    pass
                raise
        elif self.use_ssl:
            smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=ctx)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=20)
            smtp.ehlo()
            if smtp.has_extn("STARTTLS"):
                smtp.starttls(context=ctx)
                smtp.ehlo()
        smtp.login(self.email, self.password)
        self._smtp = smtp
        self._sends = 0
        self._last_used = time.time()
        via = f" via proxy {self.proxy.host}:{self.proxy.port}" if self.proxy else ""
        logger.debug("SMTPPool: connected %s -> %s:%s%s", self.email, self.host, self.port, via)

    def _send_sync(self, msg) -> tuple[bool, str]:
        """Отправить через живое соединение (синхронно)."""
        try:
            if not self._is_alive():
                self._connect()
            self._smtp.send_message(msg)
            self._sends += 1
            self._last_used = time.time()
            return True, ""
        except smtplib.SMTPServerDisconnected:
            logger.warning("SMTPPool: disconnected for %s, reconnecting...", self.email)
            try:
                self._connect()
                self._smtp.send_message(msg)
                self._sends += 1
                self._last_used = time.time()
                return True, ""
            except Exception as e:
                self._smtp = None
                return False, str(e)
        except Exception as e:
            self._smtp = None
            return False, str(e)

    async def send(self, msg) -> tuple[bool, str]:
        async with self._lock:
            return await asyncio.to_thread(self._send_sync, msg)

    def close(self):
        if self._smtp:
            try:
                self._smtp.quit()
            except Exception:
                pass
            self._smtp = None


class SMTPPool:
    """
    Пул SMTP-соединений. Один пул на весь процесс.
    Ключ для прямых: email|host|port
    Ключ для прокси: email|proxy_id|host|port  (HIGH-4)
    """

    def __init__(self):
        self._pool: dict[str, SMTPConnection] = {}
        self._lock = asyncio.Lock()

    def _key(self, email: str, host: str, port: int, proxy=None) -> str:
        if proxy is not None and hasattr(proxy, "id"):
            return f"{email}|proxy{proxy.id}|{host}|{port}"
        return f"{email}|{host}|{port}"

    async def get_or_create(
        self,
        email: str,
        password: str,
        host: str,
        port: int,
        use_ssl: bool,
        proxy=None,
    ) -> SMTPConnection:
        key = self._key(email, host, port, proxy)
        async with self._lock:
            conn = self._pool.get(key)
            if conn is None:
                conn = SMTPConnection(email, password, host, port, use_ssl, proxy=proxy)
                self._pool[key] = conn
            return conn

    async def send(
        self,
        email: str,
        password: str,
        host: str,
        port: int,
        use_ssl: bool,
        msg,
        proxy=None,
    ) -> tuple[bool, str]:
        conn = await self.get_or_create(email, password, host, port, use_ssl, proxy=proxy)
        return await conn.send(msg)

    async def close_all(self):
        async with self._lock:
            for conn in self._pool.values():
                conn.close()
            self._pool.clear()
        logger.info("SMTPPool: all connections closed")

    def stats(self) -> str:
        if not self._pool:
            return "Пул пуст"
        lines = []
        for key, conn in self._pool.items():
            email = key.split("|")[0]
            via = " via proxy" if "proxy" in key else ""
            alive = conn._smtp is not None
            lines.append(
                f"{'🟢' if alive else '🔴'} {email}{via} "
                f"| писем: {conn._sends}/{MAX_SENDS_PER_CONNECTION}"
            )
        return "\n".join(lines)


# Глобальный синглтон
_pool_instance: Optional[SMTPPool] = None


def get_smtp_pool() -> SMTPPool:
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = SMTPPool()
    return _pool_instance
