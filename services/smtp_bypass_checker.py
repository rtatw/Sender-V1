import asyncio
import random
import time
import logging
import re
import socket
import smtplib
import aiohttp
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

TIMEOUT_SMTP = 8
TIMEOUT_HTTP = 6
EHLO_DOMAINS = ["mail.google.com","smtp.outlook.com","mail.yahoo.com","relay.amazon.com","mx.mailchimp.com","outbound.sendgrid.net"]

ROLE_PREFIXES = {"admin","support","info","mail","contact","webmaster","noreply","no-reply","sales","help","office","postmaster","hostmaster","abuse","security","billing","spam","root","newsletter","marketing","jobs","hr","pr","feedback","service","team","hello","hey","donotreply","bounce","mailer","daemon"}

DISPOSABLE_DOMAINS = {"mailinator.com","guerrillamail.com","10minutemail.com","tempmail.com","throwaway.email","yopmail.com","sharklasers.com","trashmail.com","temp-mail.org","fakeinbox.com","dispostable.com","mailnator.com","mailexpire.com","spamgourmet.com","spambox.us","tempmail.net","tempemail.net","tempmailo.com","emailondeck.com","tempinbox.com","maildrop.cc","getairmail.com","trashmail.me","spamfree24.org","discard.email","spam4.me","filzmail.com","trbvm.com","zetmail.com"}

KNOWN_CATCHALL = {"yahoo.com","yahoo.de","yahoo.co.uk","yahoo.fr","aol.com","aim.com"}

MX_FALLBACK = {
    "gmail.com": [(10,"gmail-smtp-in.l.google.com"),(20,"alt1.gmail-smtp-in.l.google.com")],
    "mail.ru": [(10,"mxs.mail.ru")], "yandex.ru": [(10,"mx.yandex.ru")],
    "rambler.ru": [(10,"mx.rambler.ru")], "gmx.de": [(10,"mx01.gmx.net"),(20,"mx00.gmx.net")],
    "web.de": [(10,"mx-ha03.web.de"),(20,"mx-ha01.web.de")],
    "outlook.com": [(10,"outlook-com.olc.protection.outlook.com")],
    "hotmail.com": [(10,"hotmail-com.olc.protection.outlook.com")],
    "live.com": [(10,"live-com.olc.protection.outlook.com")],
    "icloud.com": [(10,"mx01.mail.icloud.com"),(20,"mx02.mail.icloud.com")],
    "aol.com": [(10,"mailin-01.mx.aol.com")],
    "protonmail.com": [(10,"mail.protonmail.ch")],
    "zoho.com": [(10,"mx.zoho.com")],
    "fastmail.com": [(10,"in1-smtp.messagingengine.com")],
    "op.pl": [(10,"mx.poczta.op.pl")],
    "wp.pl": [(10,"mx.wp.pl")],
    "o2.pl": [(10,"mx.poczta.o2.pl")],
}

@dataclass
class ProxyConfig:
    host: str; port: int; username: str = None; password: str = None

@dataclass
class CheckResult:
    email: str; status: str = "UNKNOWN"; score: int = 0
    details: list = field(default_factory=list); domain: str = ""
    nick: str = ""; link: str = ""; price: str = ""; photo: str = ""

class SmtpBypassChecker:
    def __init__(self):
        self._mx_cache = {}; self._mx_cache_time = {}
        self._catchall_cache = {}; self._proxy_idx = 0

    async def verify(self, email: str, proxies: List[ProxyConfig] = None, metadata: dict = None) -> CheckResult:
        result = CheckResult(email=email)
        if metadata:
            for k in ("nick","link","price","photo"):
                setattr(result, k, metadata.get(k, ""))
        if not re.match(r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$", email):
            result.status, result.score = "SYNTAX_ERROR", 0; return result
        domain = email.split("@")[-1].lower(); result.domain = domain; result.score += 10
        if domain in DISPOSABLE_DOMAINS:
            result.status, result.score = "DISPOSABLE", 5; result.details.append("Disposable"); return result
        local = email.split("@")[0].lower().replace(".","").replace("_","").replace("-","")
        if local in ROLE_PREFIXES:
            result.status, result.score = "ROLE", 20; result.details.append("Role account"); return result
        result.score += 10
        mx_list = await self._resolve_mx(domain, proxies)
        if not mx_list:
            result.status, result.score = "INVALID", 30; result.details.append("No MX"); return result
        result.score += 15
        if domain in KNOWN_CATCHALL:
            result.status, result.score = "CATCHALL", 50; result.details.append("Known catch-all"); return result
        if not proxies:
            smtp_result, is_catchall = await self._smtp_check_direct(email, domain, mx_list)
            if is_catchall:
                result.status, result.score = "CATCHALL", 50; result.details.append("Catch-all"); return result
            if smtp_result is True:
                result.status, result.score = "VALID", 100; result.details.append("Mailbox confirmed"); return result
            if smtp_result is False:
                result.status, result.score = "INVALID", 70; result.details.append("Mailbox not found"); return result
        if proxies:
            proxy = self._pick_proxy(proxies)
            r2, c2 = await self._smtp_check_via_proxy(email, domain, mx_list, proxy)
            if c2: result.status, result.score = "CATCHALL", 50; result.details.append("Catch-all via proxy"); return result
            if r2 is True: result.status, result.score = "VALID", 95; result.details.append("Mailbox via proxy"); return result
            if r2 is False: result.status, result.score = "INVALID", 65; result.details.append("Not found via proxy"); return result
        g = await self._gravatar_check(email)
        if g: result.status, result.score = "VALID", 75; result.details.append("Gravatar found"); return result
        result.status, result.score = "TIMEOUT", 35; result.details.append("All methods failed"); return result

    async def _resolve_mx(self, domain: str, proxies=None) -> List[Tuple[int, str]]:
        now = time.time()
        if domain in self._mx_cache and (now - self._mx_cache_time.get(domain,0)) < 3600:
            return self._mx_cache[domain]
        records = await self._dns_system(domain)
        if not records and proxies:
            # Retry DoH with backoff on failure (rate limits are aggressive)
            for attempt in range(3):
                records = await self._dns_doh(domain, self._pick_proxy(proxies))
                if records:
                    break
                await asyncio.sleep(1 + attempt * 2)
        if not records:
            records = await self._dns_doh(domain, None)
        if not records:
            records = MX_FALLBACK.get(domain, [(10, f"mail.{domain}")])
        self._mx_cache[domain] = records; self._mx_cache_time[domain] = now
        return records

    async def _dns_system(self, domain: str) -> List[Tuple[int, str]]:
        try:
            import dns.resolver
            answers = await asyncio.to_thread(dns.resolver.resolve, domain, "MX")
            return sorted([(r.preference, str(r.exchange).rstrip(".")) for r in answers], key=lambda x: x[0])
        except: pass
        try:
            import dns.resolver
            r = dns.resolver.Resolver(); r.nameservers = ["8.8.8.8","1.1.1.1"]
            answers = await asyncio.to_thread(r.resolve, domain, "MX")
            return sorted([(r.preference, str(r.exchange).rstrip(".")) for r in answers], key=lambda x: x[0])
        except: return []

    async def _dns_doh(self, domain: str, proxy=None) -> List[Tuple[int, str]]:
        from services.http_client import get_aiohttp
        urls = [f"https://dns.google/resolve?name={domain}&type=MX", f"https://cloudflare-dns.com/dns-query?name={domain}&type=MX"]
        pu = f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}" if proxy and proxy.username else (f"http://{proxy.host}:{proxy.port}" if proxy else None)
        sess = await get_aiohttp()
        for url in urls:
            try:
                async with sess.get(url, headers={"Accept":"application/dns-json"}, proxy=pu, ssl=False, timeout=aiohttp.ClientTimeout(total=TIMEOUT_HTTP)) as resp:
                    if resp.status != 200: continue
                    data = await resp.json(content_type=None)
                    records = []
                    for ans in data.get("Answer", []):
                        if ans.get("type") == 15:
                            parts = ans.get("data", "").split()
                            if len(parts) == 2:
                                try: records.append((int(parts[0]), parts[1].rstrip(".")))
                                except: pass
                    if records: return sorted(records, key=lambda x: x[0])
            except: continue
        return []

    async def _smtp_check_direct(self, email: str, domain: str, mx_list: List[Tuple[int, str]]) -> Tuple[Optional[bool], bool]:
        def _run():
            ehlo = random.choice(EHLO_DOMAINS)
            for _, mx in mx_list[:3]:
                try:
                    srv = smtplib.SMTP(mx, 25, timeout=TIMEOUT_SMTP)
                    srv.ehlo(ehlo); srv.mail("verify@"+random.choice(EHLO_DOMAINS))
                    fc,_ = srv.rcpt(f"xvz{random.randint(100000,999999)}@{domain}")
                    if fc == 250:
                        try: srv.quit()
                        except: pass; return None, True
                    srv.rset(); srv.mail("verify@"+random.choice(EHLO_DOMAINS))
                    code,_ = srv.rcpt(email)
                    try: srv.quit()
                    except: pass
                    if code == 250: return True, False
                    if 500 <= code < 560: return False, False
                    return None, False
                except (ConnectionRefusedError, socket.timeout, TimeoutError, OSError): continue
                except smtplib.SMTPResponseException as e:
                    if 500 <= e.smtp_code < 560: return False, False
                    continue
                except: continue
            return None, False
        return await asyncio.to_thread(_run)

    async def _smtp_check_via_proxy(self, email: str, domain: str, mx_list: List[Tuple[int, str]], proxy: ProxyConfig) -> Tuple[Optional[bool], bool]:
        import base64
        ehlo = random.choice(EHLO_DOMAINS)
        auth = base64.b64encode(f"{proxy.username}:{proxy.password}".encode()).decode() if proxy.username and proxy.password else None
        for _, mx in mx_list[:2]:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(proxy.host, proxy.port),
                    timeout=TIMEOUT_SMTP
                )
                req = f"CONNECT {mx}:25 HTTP/1.0\r\nHost: {mx}:25\r\n"
                if auth:
                    req += f"Proxy-Authorization: Basic {auth}\r\n"
                req += "\r\n"
                writer.write(req.encode())
                await writer.drain()
                resp = b""
                while b"\r\n\r\n" not in resp:
                    c = await asyncio.wait_for(reader.read(4096), timeout=TIMEOUT_SMTP)
                    if not c:
                        break
                    resp += c
                if b"200" not in resp.split(b"\r\n")[0]:
                    writer.close(); continue

                async def _smtp_cmd(cmd: str) -> tuple[int, str]:
                    writer.write(f"{cmd}\r\n".encode())
                    await writer.drain()
                    code = 0; last = ""
                    while True:
                        line = await asyncio.wait_for(reader.readline(), timeout=TIMEOUT_SMTP)
                        line = line.decode("utf-8", errors="replace").strip()
                        if not line: continue
                        code = int(line[:3]) if line[:3].isdigit() else 0
                        last = line[3:].strip()
                        if len(line) >= 4 and line[3] == ' ': break
                    return code, last

                await _smtp_cmd(f"EHLO {ehlo}")
                await _smtp_cmd(f"MAIL FROM:<verify@{ehlo}>")
                fc, _ = await _smtp_cmd(f"RCPT TO:<xvz{random.randint(100000,999999)}@{domain}>")
                if fc == 250:
                    writer.close(); return None, True
                await _smtp_cmd("RSET")
                await _smtp_cmd(f"MAIL FROM:<verify@{ehlo}>")
                code, _ = await _smtp_cmd(f"RCPT TO:<{email}>")
                writer.close()
                if code == 250: return True, False
                if 500 <= code < 560: return False, False
                return None, False
            except: continue
        return None, False

    async def _gravatar_check(self, email: str) -> bool:
        import hashlib
        from services.http_client import get_aiohttp
        try:
            sess = await get_aiohttp()
            async with sess.get(f"https://www.gravatar.com/{hashlib.md5(email.lower().strip().encode()).hexdigest()}.json", ssl=False, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return r.status == 200
        except: return False

    def _pick_proxy(self, proxies=None):
        if not proxies: return None
        p = proxies[self._proxy_idx%len(proxies)]; self._proxy_idx+=1; return p

