import aiohttp
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_aiohttp_session: Optional[aiohttp.ClientSession] = None
_httpx_client: Optional[httpx.AsyncClient] = None


async def get_aiohttp() -> aiohttp.ClientSession:
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        _aiohttp_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0"},
        )
        logger.debug("Created shared aiohttp.ClientSession")
    return _aiohttp_session


async def get_httpx() -> httpx.AsyncClient:
    global _httpx_client
    if _httpx_client is None or _httpx_client.is_closed:
        _httpx_client = httpx.AsyncClient(timeout=30)
        logger.debug("Created shared httpx.AsyncClient")
    return _httpx_client


async def close_http() -> None:
    global _aiohttp_session, _httpx_client
    if _aiohttp_session and not _aiohttp_session.closed:
        await _aiohttp_session.close()
        _aiohttp_session = None
    if _httpx_client and not _httpx_client.is_closed:
        await _httpx_client.aclose()
        _httpx_client = None
    logger.info("HTTP clients closed")
