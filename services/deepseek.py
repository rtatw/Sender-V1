import logging
import aiohttp
from database.engine import async_session
from database.models import UserSettings
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def _get_deepseek_key(user_id: int) -> str:
    import os
    try:
        async with async_session() as session:
            settings = await session.scalar(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            if settings and settings.api_key_deepseek:
                return settings.api_key_deepseek
            # Fallback to global
            from database.models import GlobalSettings
            global_s = await session.scalar(
                select(GlobalSettings).where(GlobalSettings.id == 1)
            )
            if global_s and global_s.api_key_deepseek:
                return global_s.api_key_deepseek
    except Exception:
        pass
    return os.getenv("DEEPSEEK_API_KEY", "")


async def translate_text(text: str, user_id: int = 0, target_lang: str = "ru") -> str:
    if not text.strip():
        return text

    api_key = await _get_deepseek_key(user_id) if user_id else ""
    if not api_key:
        return f"[Перевод недоступен]\n\n{text}"

    try:
        from services.http_client import get_aiohttp
        session = await get_aiohttp()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": f"You are a translator. Translate the user message to {target_lang}. Return ONLY the translation, no explanations."},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                translated = data["choices"][0]["message"]["content"]
                return f"[Переведено]\n\n{translated}"
            elif resp.status == 401:
                logger.error("DeepSeek: Invalid API key")
                return f"[Ошибка: неверный API ключ]\n\n{text}"
            elif resp.status == 429:
                logger.warning("DeepSeek: Rate limited")
                return f"[Превышен лимит запросов]\n\n{text}"
            else:
                logger.warning("DeepSeek API %s: %s", resp.status, await resp.text())
                return f"[Ошибка API {resp.status}]\n\n{text}"
    except Exception as e:
        logger.warning("DeepSeek error: %s", e)
        return f"[Ошибка соединения]\n\n{text}"



