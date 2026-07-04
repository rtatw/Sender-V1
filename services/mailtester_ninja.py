import logging
import asyncio
import httpx

logger = logging.getLogger(__name__)

MAILTESTER_API = "https://happy.mailtester.ninja/ninja"

_last_call = 0.0
_lock = asyncio.Lock()


async def verify_email(email: str) -> dict:
    global _last_call
    from database.engine import async_session
    from database.models import MailtesterKey
    from sqlalchemy import select

    async with async_session() as s:
        keys = list(await s.scalars(
            select(MailtesterKey).where(MailtesterKey.is_active == True, MailtesterKey.is_valid == True)
        ))

    if not keys:
        logger.warning("No active mailtester keys in DB")
        return {"code": "error", "message": "No keys configured"}

    for key_obj in keys:
        api_key = key_obj.key  # plaintext key (store encrypted later if needed)

        async with _lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - _last_call
            if elapsed < 0.9:
                await asyncio.sleep(0.9 - elapsed)
            _last_call = asyncio.get_event_loop().time()

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(MAILTESTER_API, params={"email": email, "key": api_key})
                if r.status_code == 200:
                    data = r.json()
                    code = data.get("code", "error")
                    if code in ("ok", "ko", "mb"):
                        return data
                    if code == "--":
                        logger.warning("Mailtester key %s... invalid, skipping", api_key[:12])
                        continue
                elif r.status_code == 429:
                    logger.warning("Mailtester rate limited on key %s...", api_key[:12])
                    continue
                else:
                    logger.warning("Mailtester HTTP %s for key %s...", r.status_code, api_key[:12])
                    continue
        except Exception as e:
            logger.warning("Mailtester error for key %s...: %s", api_key[:12], e)
            continue

    return {"code": "error", "message": "All keys failed"}
