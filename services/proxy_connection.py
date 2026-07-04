"""
proxy_connection.py — единый слой работы с прокси (HTTP / SOCKS5 / SOCKS4).

Изменения (аудит CRIT-2/CRIT-3/CRIT-10):
  ✅ Используем библиотеку python-socks (есть в requirements) вместо
     рукописного HTTP CONNECT. Поддерживаются SOCKS5, SOCKS4, HTTP CONNECT.
  ✅ Корректная обёртка готового сокета в smtplib.SMTP через subclass
     с переопределённым _get_socket (раньше установка s.sock = sock
     вручную вызывала SMTPServerDisconnected при login).
  ✅ То же самое для imaplib.IMAP4 и poplib.POP3.
  ✅ Логируем ошибки вместо bare except: pass.
  ✅ Поддержка proxy_type из БД (поле Proxy.proxy_type).
  ✅ Поддержка ротационных прокси (rotation_mode="rotating" — без привязки).
"""

import asyncio
import base64
import email as email_lib
import email.policy
import imaplib
import logging
import poplib
import smtplib
import socket
import ssl
from typing import Optional

from python_socks.async_.asyncio import Proxy as AsyncProxy
from python_socks import ProxyType
from sqlalchemy import select

from database.engine import async_session
from database.models import Proxy

logger = logging.getLogger(__name__)

# ─── Карта SMTP/IMAP/POP3/MX серверов по доменам ────────────────────────────

SMTP_SERVERS = {
    "gmail.com": "smtp.gmail.com", "googlemail.com": "smtp.gmail.com",
    "mail.ru": "smtp.mail.ru", "bk.ru": "smtp.mail.ru", "list.ru": "smtp.mail.ru",
    "inbox.ru": "smtp.mail.ru", "internet.ru": "smtp.mail.ru",
    "yandex.ru": "smtp.yandex.ru", "ya.ru": "smtp.yandex.ru", "yandex.com": "smtp.yandex.com",
    "gmx.de": "mail.gmx.net", "gmx.net": "mail.gmx.net", "gmx.at": "mail.gmx.net", "gmx.ch": "mail.gmx.net",
    "web.de": "smtp.web.de",
    "yahoo.com": "smtp.mail.yahoo.com", "yahoo.de": "smtp.mail.yahoo.com", "yahoo.co.uk": "smtp.mail.yahoo.com",
    "outlook.com": "smtp-mail.outlook.com", "hotmail.com": "smtp-mail.outlook.com",
    "live.com": "smtp-mail.outlook.com", "hotmail.de": "smtp-mail.outlook.com",
    "icloud.com": "smtp.mail.me.com", "me.com": "smtp.mail.me.com", "mac.com": "smtp.mail.me.com",
    "rambler.ru": "smtp.rambler.ru", "ro.ru": "smtp.rambler.ru", "lenta.ru": "smtp.rambler.ru",
    "myrambler.ru": "smtp.rambler.ru",
    "aol.com": "smtp.aol.com", "aim.com": "smtp.aol.com",
    "protonmail.com": "smtp.protonmail.ch", "proton.me": "smtp.protonmail.ch", "pm.me": "smtp.protonmail.ch",
    "zoho.com": "smtp.zoho.com", "zohomail.com": "smtp.zoho.com",
    "fastmail.com": "smtp.fastmail.com", "fastmail.fm": "smtp.fastmail.com",
    "tutanota.com": "smtp.tutanota.de", "tuta.io": "smtp.tutanota.de",
    "mail.com": "smtp.mail.com", "email.com": "smtp.mail.com",
    "posteo.de": "smtp.posteo.de", "posteo.net": "smtp.posteo.de",
    "freenet.de": "mx.freenet.de", "t-online.de": "securesmtp.t-online.de",
    "arcor.de": "mail.arcor.de", "1und1.de": "smtp.1und1.de", "strato.de": "smtp.strato.de",
    "qq.com": "smtp.qq.com", "foxmail.com": "smtp.qq.com",
    "163.com": "smtp.163.com", "126.com": "smtp.126.com", "sina.com": "smtp.sina.com",
    "seznam.cz": "smtp.seznam.cz",
    "onet.pl": "smtp.poczta.onet.pl", "wp.pl": "smtp.wp.pl", "o2.pl": "smtp.poczta.o2.pl",
    "interia.pl": "smtp.poczta.interia.pl", "op.pl": "smtp.poczta.op.pl",
}

IMAP_SERVERS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "mail.ru": "imap.mail.ru", "bk.ru": "imap.mail.ru", "list.ru": "imap.mail.ru",
    "inbox.ru": "imap.mail.ru",
    "yandex.ru": "imap.yandex.ru", "ya.ru": "imap.yandex.ru", "yandex.com": "imap.yandex.com",
    "gmx.de": "imap.gmx.net", "gmx.net": "imap.gmx.net",
    "web.de": "imap.web.de",
    "yahoo.com": "imap.mail.yahoo.com", "yahoo.de": "imap.mail.yahoo.com",
    "outlook.com": "imap-mail.outlook.com", "hotmail.com": "imap-mail.outlook.com",
    "live.com": "imap-mail.outlook.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "rambler.ru": "imap.rambler.ru",
    "aol.com": "imap.aol.com",
    "protonmail.com": "imap.protonmail.ch", "proton.me": "imap.protonmail.ch",
    "zoho.com": "imap.zoho.com",
    "fastmail.com": "imap.fastmail.com",
    "mail.com": "imap.mail.com",
    "posteo.de": "imap.posteo.de",
    "t-online.de": "secureimap.t-online.de",
    "qq.com": "imap.qq.com", "163.com": "imap.163.com", "126.com": "imap.126.com",
    "seznam.cz": "imap.seznam.cz",
    "onet.pl": "imap.poczta.onet.pl", "wp.pl": "imap.wp.pl", "o2.pl": "imap.poczta.o2.pl",
    "interia.pl": "imap.poczta.interia.pl", "op.pl": "imap.poczta.op.pl",
}

POP3_SERVERS = {
    "gmail.com": "pop.gmail.com", "googlemail.com": "pop.gmail.com",
    "mail.ru": "pop.mail.ru", "bk.ru": "pop.mail.ru", "list.ru": "pop.mail.ru",
    "inbox.ru": "pop.mail.ru",
    "yandex.ru": "pop.yandex.ru", "ya.ru": "pop.yandex.ru", "yandex.com": "pop.yandex.com",
    "gmx.de": "pop.gmx.net", "gmx.net": "pop.gmx.net", "gmx.at": "pop.gmx.net",
    "web.de": "pop3.web.de",
    "yahoo.com": "pop.mail.yahoo.com", "yahoo.de": "pop.mail.yahoo.com",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "icloud.com": "pop.mail.me.com", "me.com": "pop.mail.me.com",
    "rambler.ru": "pop.rambler.ru",
    "aol.com": "pop.aol.com",
    "protonmail.com": "pop.protonmail.ch", "proton.me": "pop.protonmail.ch",
    "zoho.com": "pop.zoho.com",
    "fastmail.com": "pop.fastmail.com",
    "mail.com": "pop.mail.com",
    "posteo.de": "posteo.de",
    "t-online.de": "securepop.t-online.de",
    "qq.com": "pop.qq.com", "163.com": "pop.163.com", "126.com": "pop.126.com",
    "seznam.cz": "pop3.seznam.cz",
    "onet.pl": "pop3.poczta.onet.pl", "wp.pl": "pop3.wp.pl", "o2.pl": "pop3.poczta.o2.pl",
    "interia.pl": "pop3.poczta.interia.pl", "op.pl": "pop3.poczta.op.pl",
}

MX_HOSTS = {
    "gmail.com": ["gmail-smtp-in.l.google.com", "alt1.gmail-smtp-in.l.google.com"],
    "mail.ru": ["mxs.mail.ru"], "yandex.ru": ["mx.yandex.ru"],
    "gmx.de": ["mx01.gmx.net", "mx00.gmx.net"], "web.de": ["mx-ha03.web.de", "mx-ha01.web.de"],
    "yahoo.com": ["mta5.am0.yahoodns.net"], "outlook.com": ["outlook-com.olc.protection.outlook.com"],
    "aol.com": ["mailin-01.mx.aol.com"], "icloud.com": ["mx01.mail.icloud.com"],
    "protonmail.com": ["mail.protonmail.ch"], "zoho.com": ["mx.zoho.com"],
    "op.pl": ["mx.poczta.op.pl"], "wp.pl": ["mx.wp.pl"], "o2.pl": ["mx.poczta.o2.pl"],
}

SMTP_FALLBACKS = {"op.pl": ["smtp.poczta.op.pl", "smtp.op.pl"], "onet.pl": ["smtp.poczta.onet.pl", "smtp.onet.pl"], "wp.pl": ["smtp.wp.pl", "poczta.wp.pl"], "o2.pl": ["smtp.poczta.o2.pl", "smtp.o2.pl"], "interia.pl": ["smtp.poczta.interia.pl", "smtp.interia.pl"]}
IMAP_FALLBACKS = {"op.pl": ["imap.poczta.op.pl", "imap.op.pl"], "onet.pl": ["imap.poczta.onet.pl", "imap.onet.pl"], "wp.pl": ["imap.wp.pl", "imap.poczta.wp.pl"], "o2.pl": ["imap.poczta.o2.pl", "imap.o2.pl"], "interia.pl": ["imap.poczta.interia.pl", "imap.interia.pl"]}
POP3_FALLBACKS = {"op.pl": ["pop3.poczta.op.pl", "pop3.op.pl"], "onet.pl": ["pop3.poczta.onet.pl", "pop3.onet.pl"], "wp.pl": ["pop3.wp.pl"], "o2.pl": ["pop3.poczta.o2.pl"], "interia.pl": ["pop3.poczta.interia.pl"]}


def _get_smtp_hosts(email):
    domain = email.split("@")[-1].lower()
    p = SMTP_SERVERS.get(domain, f"smtp.{domain}")
    f = SMTP_FALLBACKS.get(domain, [])
    return [p] + [h for h in f if h != p]


def _get_smtp_host(email):
    return _get_smtp_hosts(email)[0]


def _get_imap_hosts(email):
    domain = email.split("@")[-1].lower()
    p = IMAP_SERVERS.get(domain, f"imap.{domain}")
    f = IMAP_FALLBACKS.get(domain, [])
    return [p] + [h for h in f if h != p]


def _get_imap_host(email):
    return _get_imap_hosts(email)[0]


def _get_pop3_hosts(email):
    domain = email.split("@")[-1].lower()
    p = POP3_SERVERS.get(domain, f"pop3.{domain}")
    f = POP3_FALLBACKS.get(domain, [])
    return [p] + [h for h in f if h != p]


def _get_pop3_host(email):
    return _get_pop3_hosts(email)[0]


# ─── Получение прокси из БД ──────────────────────────────────────────────────

async def _get_proxy(user_id):
    """Возвращает первый живой прокси пользователя (для совместимости со старым API)."""
    try:
        async with async_session() as s:
            r = await s.scalars(
                select(Proxy).where(
                    Proxy.user_id == user_id,
                    Proxy.is_active == True,
                    Proxy.status == "alive",
                )
            )
            return r.first()
    except Exception as e:
        logger.warning("Failed to load proxy for user %s: %s", user_id, e)
        return None


# ─── Создание сокета через прокси (HTTP/SOCKS5/SOCKS4) ──────────────────────

_PROXY_TYPE_MAP = {
    "socks5": ProxyType.SOCKS5,
    "socks4": ProxyType.SOCKS4,
    "http":   ProxyType.HTTP,
}


def _proxy_to_url(proxy: Proxy) -> str:
    """Формирует URL для python-socks на основе Proxy из БД."""
    ptype = (proxy.proxy_type or "socks5").lower()
    if ptype not in _PROXY_TYPE_MAP:
        raise ValueError(f"Unknown proxy_type: {ptype}")
    if proxy.username and proxy.password:
        auth = f"{proxy.username}:{proxy.password}@"
    elif proxy.username:
        auth = f"{proxy.username}@"
    else:
        auth = ""
    return f"{ptype}://{auth}{proxy.host}:{proxy.port}"


async def _create_proxy_socket(proxy: Proxy, target_host: str, target_port: int, timeout: int = 15):
    """Создаёт сокет до target_host:target_port через прокси.

    Поддерживает SOCKS5, SOCKS4, HTTP CONNECT — через python-socks.
    Возвращает готовый socket.socket (с SSL на вызывающей стороне).
    """
    url = _proxy_to_url(proxy)
    p = AsyncProxy.from_url(url)
    sock = await p.connect(
        dest_host=target_host,
        dest_port=target_port,
        timeout=timeout,
    )
    return sock


def _wrap_ssl(raw_sock, server_hostname: str, timeout: int = 20):
    """Оборачивает готовый сокет в SSL (для SMTPS/IMAPS/POP3S)."""
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw_sock, server_hostname=server_hostname)
    sock.settimeout(timeout)
    return sock


# ─── Обёртки smtplib / imaplib / poplib с готовым сокетом ────────────────────

class _ProxySMTP(smtplib.SMTP):
    """smtplib.SMTP с уже готовым прокси-сокетом.

    Переопределяем _get_socket, чтобы connect() не создавал новый сокет,
    а использовал наш. Без этого вызов s.sock = sock + s.login падал с
    SMTPServerDisconnected (нет ehlo_resp, нет _smtp_state).
    """
    def __init__(self, pre_sock, host, port, timeout=30):
        self._pre_sock = pre_sock
        super().__init__(timeout=timeout)
        # Имитируем connect, но без открытия нового сокета
        self.connect(host, port)

    def _get_socket(self, host, port, timeout):
        return self._pre_sock


class _ProxySMTPSSL(smtplib.SMTP_SSL):
    """SMTP_SSL с готовым SSL-обёрнутым сокетом через прокси."""
    def __init__(self, pre_ssl_sock, host, port, timeout=30):
        self._pre_sock = pre_ssl_sock
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())

    def _get_socket(self, host, port, timeout):
        return self._pre_sock


class _ProxyIMAP4(imaplib.IMAP4):
    """imaplib.IMAP4 с готовым прокси-сокетом."""
    def __init__(self, pre_sock, host, port=993, timeout=30):
        self._pre_sock = pre_sock
        self._imap_timeout = timeout
        super().__init__(host, port)

    def open(self, host='', port=993, timeout=None):
        """Переопределённый open — использует готовый сокет вместо создания нового."""
        self.host = host
        self.port = port
        self.sock = self._pre_sock
        self.file = self.sock.makefile('rb')
        # Читаем welcome-строку сервера
        self._get_response()


class _ProxyPOP3(poplib.POP3):
    """poplib.POP3 с готовым прокси-сокетом."""
    def __init__(self, pre_sock, host, port=995, timeout=30):
        self._pre_sock = pre_sock
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        return self._pre_sock


# ─── SMTP ────────────────────────────────────────────────────────────────────

async def smtp_verify(email: str, password: str, user_id: int = 0) -> tuple[bool, str]:
    """Проверяет валидность учётки: пробует 465 (SSL) и 587 (STARTTLS), прямое и через прокси."""
    proxy = await _get_proxy(user_id) if user_id else None
    hosts = _get_smtp_hosts(email)
    errors = []

    for host in hosts:
        # Сначала пробуем прямое соединение (DNS проверяем через gethostbyname)
        try:
            await asyncio.to_thread(socket.gethostbyname, host)
            dns_ok = True
        except Exception:
            dns_ok = False
            errors.append(f"DNS:{host}")

        # Если DNS работает — пробуем напрямую
        if dns_ok:
            for port, use_ssl in [(465, True), (587, False)]:
                try:
                    ok, err = await asyncio.to_thread(
                        _smtp_connect_direct, host, port, use_ssl, email, password
                    )
                    if ok:
                        return True, ""
                except Exception as e:
                    errors.append(f"direct {host}:{port}:{str(e)[:80]}")

        # Если есть прокси — пробуем через него (и когда DNS не работает, и как fallback)
        if proxy:
            for port, use_ssl in [(465, True), (587, False)]:
                try:
                    ok, err = await asyncio.to_thread(
                        _smtp_connect_via_proxy, proxy, host, port, use_ssl, email, password
                    )
                    if ok:
                        return True, ""
                except Exception as e:
                    errors.append(f"proxy {host}:{port}:{str(e)[:80]}")

    return False, " | ".join(errors[-4:])


def _smtp_connect_direct(host, port, use_ssl, email, password):
    ctx = ssl.create_default_context()
    if use_ssl:
        s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
        s.ehlo()
        if s.has_extn("STARTTLS"):
            s.starttls(context=ctx)
            s.ehlo()
    try:
        s.login(email, password)
        return True, ""
    finally:
        try:
            s.quit()
        except Exception:
            pass


def _smtp_connect_via_proxy(proxy, host, port, use_ssl, email, password):
    """Синхронная обёртка: создаёт прокси-сокет синхронно (вызов asyncio.run внутри to_thread)."""
    # python-socks имеет sync API тоже
    from python_socks.sync import Proxy as SyncProxy

    url = _proxy_to_url(proxy)
    p = SyncProxy.from_url(url)
    raw = p.connect(dest_host=host, dest_port=port, timeout=20)
    try:
        if use_ssl:
            sock = _wrap_ssl(raw, host, timeout=20)
            s = _ProxySMTPSSL(sock, host, port, timeout=20)
        else:
            raw.settimeout(20)
            s = _ProxySMTP(raw, host, port, timeout=20)
            s.ehlo()
            if s.has_extn("STARTTLS"):
                ctx = ssl.create_default_context()
                s.starttls(context=ctx)
                s.ehlo()
        try:
            s.login(email, password)
            return True, ""
        finally:
            try:
                s.quit()
            except Exception:
                pass
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        raise


async def smtp_send_message(account_email, account_password, msg, user_id=0):
    """Отправляет MIME-сообщение через SMTP (прямое или через прокси)."""
    proxy = await _get_proxy(user_id) if user_id else None
    hosts = _get_smtp_hosts(account_email)
    errors = []

    for host in hosts:
        try:
            await asyncio.to_thread(socket.gethostbyname, host)
            dns_ok = True
        except Exception:
            dns_ok = False
            errors.append(f"DNS:{host}")

        if dns_ok:
            for port, use_ssl in [(465, True), (587, False)]:
                try:
                    ok, err = await asyncio.to_thread(
                        _smtp_send_direct, host, port, use_ssl, account_email, account_password, msg
                    )
                    if ok:
                        return True, ""
                except Exception as e:
                    errors.append(f"direct {host}:{port}:{str(e)[:80]}")

        if proxy:
            for port, use_ssl in [(465, True), (587, False)]:
                try:
                    ok, err = await asyncio.to_thread(
                        _smtp_send_via_proxy, proxy, host, port, use_ssl,
                        account_email, account_password, msg
                    )
                    if ok:
                        return True, ""
                except Exception as e:
                    errors.append(f"proxy {host}:{port}:{str(e)[:80]}")

    return False, " | ".join(errors[-3:])


def _smtp_send_direct(host, port, use_ssl, email, password, msg):
    ctx = ssl.create_default_context()
    if use_ssl:
        s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(host, port, timeout=20)
        s.ehlo()
        if s.has_extn("STARTTLS"):
            s.starttls(context=ctx)
            s.ehlo()
    try:
        s.login(email, password)
        s.send_message(msg)
        return True, ""
    finally:
        try:
            s.quit()
        except Exception:
            pass


def _smtp_send_via_proxy(proxy, host, port, use_ssl, email, password, msg):
    from python_socks.sync import Proxy as SyncProxy

    url = _proxy_to_url(proxy)
    p = SyncProxy.from_url(url)
    raw = p.connect(dest_host=host, dest_port=port, timeout=20)
    try:
        if use_ssl:
            sock = _wrap_ssl(raw, host, timeout=20)
            s = _ProxySMTPSSL(sock, host, port, timeout=20)
        else:
            raw.settimeout(20)
            s = _ProxySMTP(raw, host, port, timeout=20)
            s.ehlo()
            if s.has_extn("STARTTLS"):
                ctx = ssl.create_default_context()
                s.starttls(context=ctx)
                s.ehlo()
        try:
            s.login(email, password)
            s.send_message(msg)
            return True, ""
        finally:
            try:
                s.quit()
            except Exception:
                pass
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        raise


# ─── IMAP ────────────────────────────────────────────────────────────────────

async def imap_verify(email: str, password: str, user_id: int = 0):
    proxy = await _get_proxy(user_id) if user_id else None
    hosts = _get_imap_hosts(email)
    errors = []

    for host in hosts:
        try:
            await asyncio.to_thread(socket.gethostbyname, host)
            dns_ok = True
        except Exception:
            dns_ok = False
            errors.append(f"DNS:{host}")

        if dns_ok:
            try:
                await asyncio.to_thread(_imap_connect_direct, host, email, password)
                return True, ""
            except Exception as e:
                errors.append(f"direct {host}:{str(e)[:80]}")

        if proxy:
            try:
                await asyncio.to_thread(_imap_connect_via_proxy, proxy, host, email, password)
                return True, ""
            except Exception as e:
                errors.append(f"proxy {host}:{str(e)[:80]}")

    return False, " | ".join(errors[-4:])


def _imap_connect_direct(host, email, password):
    ctx = ssl.create_default_context()
    c = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, timeout=15)
    try:
        c.login(email, password)
    finally:
        try:
            c.logout()
        except Exception:
            pass


def _imap_connect_via_proxy(proxy, host, email, password):
    from python_socks.sync import Proxy as SyncProxy
    url = _proxy_to_url(proxy)
    p = SyncProxy.from_url(url)
    raw = p.connect(dest_host=host, dest_port=993, timeout=15)
    try:
        sock = _wrap_ssl(raw, host, timeout=15)
        c = _ProxyIMAP4(sock, host, port=993, timeout=15)
        try:
            c.login(email, password)
        finally:
            try:
                c.logout()
            except Exception:
                pass
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        raise


# ─── POP3 ────────────────────────────────────────────────────────────────────

async def pop3_verify(email: str, password: str, user_id: int = 0):
    proxy = await _get_proxy(user_id) if user_id else None
    hosts = _get_pop3_hosts(email)
    errors = []

    for host in hosts:
        try:
            await asyncio.to_thread(socket.gethostbyname, host)
            dns_ok = True
        except Exception:
            dns_ok = False
            errors.append(f"DNS:{host}")

        if dns_ok:
            try:
                await asyncio.to_thread(_pop3_connect_direct, host, email, password)
                return True, ""
            except Exception as e:
                errors.append(f"direct {host}:{str(e)[:80]}")

        if proxy:
            try:
                await asyncio.to_thread(_pop3_connect_via_proxy, proxy, host, email, password)
                return True, ""
            except Exception as e:
                errors.append(f"proxy {host}:{str(e)[:80]}")

    return False, " | ".join(errors[-4:])


def _pop3_connect_direct(host, email, password):
    ctx = ssl.create_default_context()
    c = poplib.POP3_SSL(host, 995, context=ctx, timeout=15)
    try:
        c.user(email)
        c.pass_(password)
    finally:
        try:
            c.quit()
        except Exception:
            pass


def _pop3_connect_via_proxy(proxy, host, email, password):
    from python_socks.sync import Proxy as SyncProxy
    url = _proxy_to_url(proxy)
    p = SyncProxy.from_url(url)
    raw = p.connect(dest_host=host, dest_port=995, timeout=15)
    try:
        sock = _wrap_ssl(raw, host, timeout=15)
        c = _ProxyPOP3(sock, host, port=995, timeout=15)
        try:
            c.user(email)
            c.pass_(password)
        finally:
            try:
                c.quit()
            except Exception:
                pass
    except Exception:
        try:
            raw.close()
        except Exception:
            pass
        raise


# ─── Парсинг писем ──────────────────────────────────────────────────────────

def _decode_mime_header(value):
    if not value:
        return ""
    r = []
    for p, c in email_lib.header.decode_header(value):
        if isinstance(p, bytes):
            try:
                r.append(p.decode(c or "utf-8", errors="replace"))
            except Exception:
                r.append(p.decode("utf-8", errors="replace"))
        else:
            r.append(str(p))
    return "".join(r)


def _parse_email(raw_bytes):
    import re as _r
    msg = email_lib.message_from_bytes(raw_bytes, policy=email.policy.default)
    r = {
        "from": _decode_mime_header(msg.get("From", "")),
        "to": _decode_mime_header(msg.get("To", "")),
        "subject": _decode_mime_header(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
        "message_id": msg.get("Message-ID", ""),
        "body": "",
        "body_html": "",
    }
    bp = bh = ""
    if msg.is_multipart():
        for pt in msg.walk():
            ct = pt.get_content_type()
            cd = str(pt.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            pl = pt.get_payload(decode=True)
            if pl is None:
                continue
            cs = pt.get_content_charset() or "utf-8"
            try:
                t = pl.decode(cs, errors="replace")
            except Exception:
                t = pl.decode("utf-8", errors="replace")
            if ct == "text/plain" and not bp:
                bp = t
            elif ct == "text/html" and not bh:
                bh = t
    else:
        pl = msg.get_payload(decode=True)
        if pl:
            cs = msg.get_content_charset() or "utf-8"
            try:
                t = pl.decode(cs, errors="replace")
            except Exception:
                t = pl.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                bh = t
            else:
                bp = t
    r["body"] = bp or _r.sub(r"<[^>]+>", "", bh or "")
    r["body"] = _r.sub(r"\n\s*\n", "\n", r["body"]).strip()
    return r


async def imap_fetch_parsed(email, password, user_id=0, limit=10):
    proxy = await _get_proxy(user_id) if user_id else None
    hosts = _get_imap_hosts(email)
    for host in hosts:
        try:
            result = await asyncio.to_thread(_imap_fetch_one, host, email, password, proxy, limit)
            if result is not None:
                return result
        except Exception as e:
            logger.warning("IMAP fetch failed for %s on %s: %s", email, host, e)
    return []


def _imap_fetch_one(host, email, password, proxy, limit):
    ctx = ssl.create_default_context()
    conn = None
    direct_failed = False
    try:
        conn = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, timeout=15)
    except Exception as e:
        logger.debug("Direct IMAP to %s failed: %s — trying proxy", host, e)
        direct_failed = True

    if direct_failed:
        if not proxy:
            return []
        from python_socks.sync import Proxy as SyncProxy
        url = _proxy_to_url(proxy)
        p = SyncProxy.from_url(url)
        raw = p.connect(dest_host=host, dest_port=993, timeout=15)
        try:
            sock = _wrap_ssl(raw, host, timeout=15)
            conn = _ProxyIMAP4(sock, host, port=993, timeout=15)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            raise

    if conn is None:
        return []

    try:
        conn.login(email, password)
        conn.select("INBOX", readonly=True)
        st, msgs = conn.search(None, "ALL")
        r = []
        if st == "OK" and msgs and msgs[0]:
            for mid in reversed(msgs[0].split()[-limit:]):
                s2, data = conn.fetch(mid, "(RFC822)")
                if s2 == "OK" and data and data[0]:
                    rb = data[0][1]
                    if isinstance(rb, bytes):
                        p = _parse_email(rb)
                        p["imap_uid"] = mid.decode() if isinstance(mid, bytes) else str(mid)
                        r.append(p)
        conn.logout()
        return r
    except Exception as e:
        logger.warning("IMAP fetch (login/search) failed on %s: %s", host, e)
        try:
            conn.logout()
        except Exception:
            pass
        return []


async def pop3_fetch_parsed(email, password, user_id=0, limit=10):
    proxy = await _get_proxy(user_id) if user_id else None
    hosts = _get_pop3_hosts(email)
    for host in hosts:
        try:
            result = await asyncio.to_thread(_pop3_fetch_one, host, email, password, proxy, limit)
            if result is not None:
                return result
        except Exception as e:
            logger.warning("POP3 fetch failed for %s on %s: %s", email, host, e)
    return []


def _pop3_fetch_one(host, email, password, proxy, limit):
    ctx = ssl.create_default_context()
    conn = None
    direct_failed = False
    try:
        conn = poplib.POP3_SSL(host, 995, context=ctx, timeout=15)
    except Exception as e:
        logger.debug("Direct POP3 to %s failed: %s — trying proxy", host, e)
        direct_failed = True

    if direct_failed:
        if not proxy:
            return []
        from python_socks.sync import Proxy as SyncProxy
        url = _proxy_to_url(proxy)
        p = SyncProxy.from_url(url)
        raw = p.connect(dest_host=host, dest_port=995, timeout=15)
        try:
            sock = _wrap_ssl(raw, host, timeout=15)
            conn = _ProxyPOP3(sock, host, port=995, timeout=15)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            raise

    if conn is None:
        return []

    try:
        conn.user(email)
        conn.pass_(password)
        _, mails, _ = conn.list()
        r = []
        for n in reversed([m.decode().split()[0] for m in mails[-limit:]]):
            try:
                _, lines, _ = conn.retr(n)
                rb = b"\r\n".join(lines)
                p = _parse_email(rb)
                p["imap_uid"] = f"pop3_{n}"
                r.append(p)
            except Exception as e:
                logger.debug("POP3 retr %s failed: %s", n, e)
                continue
        conn.quit()
        return r
    except Exception as e:
        logger.warning("POP3 fetch (login/list) failed on %s: %s", host, e)
        try:
            conn.quit()
        except Exception:
            pass
        return []


async def imap_fetch_recent(email, password, user_id=0, limit=25):
    proxy = await _get_proxy(user_id) if user_id else None
    host = _get_imap_host(email)

    def _f():
        ctx = ssl.create_default_context()
        conn = None
        try:
            conn = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, timeout=15)
        except Exception as e:
            logger.debug("Direct IMAP-recent to %s failed: %s — trying proxy", host, e)
            if proxy:
                from python_socks.sync import Proxy as SyncProxy
                url = _proxy_to_url(proxy)
                p = SyncProxy.from_url(url)
                raw = p.connect(dest_host=host, dest_port=993, timeout=15)
                try:
                    sock = _wrap_ssl(raw, host, timeout=15)
                    conn = _ProxyIMAP4(sock, host, port=993, timeout=15)
                except Exception:
                    try:
                        raw.close()
                    except Exception:
                        pass
                    raise
        if not conn:
            return []
        try:
            conn.login(email, password)
            conn.select("INBOX", readonly=True)
            st, msgs = conn.search(None, "ALL")
            r = []
            if st == "OK" and msgs and msgs[0]:
                for mid in reversed(msgs[0].split()[-limit:]):
                    s2, data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                    if s2 == "OK" and data and data[0]:
                        r.append((data[0][1] or b"").decode("utf-8", errors="replace"))
            conn.logout()
            return r
        except Exception as e:
            logger.warning("IMAP fetch_recent inner failed on %s: %s", host, e)
            try:
                conn.logout()
            except Exception:
                pass
            return []

    try:
        return await asyncio.to_thread(_f)
    except Exception as e:
        logger.warning("IMAP fetch_recent failed %s: %s", email, e)
        return []


# ─── SMTP hunter verify (проверка существования email через MX) ──────────────

async def smtp_hunter_verify(email, sender, user_id=0):
    import smtplib as _smtp_mod
    domain = email.split("@")[-1].lower()
    mx = MX_HOSTS.get(domain, [f"smtp.{domain}", f"mail.{domain}", domain])
    proxy = await _get_proxy(user_id) if user_id else None

    def _c(mx_host, use_proxy):
        try:
            if use_proxy and proxy:
                from python_socks.sync import Proxy as SyncProxy
                url = _proxy_to_url(proxy)
                p = SyncProxy.from_url(url)
                raw = p.connect(dest_host=mx_host, dest_port=25, timeout=12)
                try:
                    raw.settimeout(12)
                    s = _ProxySMTP(raw, mx_host, 25, timeout=12)
                    s.helo()
                    s.mail(sender)
                    cd, _ = s.rcpt(email)
                    try:
                        s.quit()
                    except Exception:
                        pass
                    return 250 <= cd < 300
                finally:
                    try:
                        raw.close()
                    except Exception:
                        pass
            else:
                s = _smtp_mod.SMTP(mx_host, 25, timeout=12)
                s.helo()
                s.mail(sender)
                cd, _ = s.rcpt(email)
                try:
                    s.quit()
                except Exception:
                    pass
                return 250 <= cd < 300
        except Exception as e:
            logger.debug("smtp_hunter_verify %s (proxy=%s) failed: %s", mx_host, use_proxy, e)
            return False

    for m in mx:
        if await asyncio.to_thread(_c, m, False):
            return True
        if proxy and await asyncio.to_thread(_c, m, True):
            return True
    return False
