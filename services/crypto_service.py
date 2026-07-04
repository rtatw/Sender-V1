import os
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class CryptoService:
    """Encrypts/decrypts passwords and API keys.

    Ключ берётся ТОЛЬКО из переменной окружения ENCRYPTION_KEY.
    Ранее здесь был опасный fallback на локальный файл secret.key:
    при отсутствии env бот молча генерировал случайный ключ,
    шифровал им пароли, и после переноса/удаления файла все пароли
    в БД становились невосстановимы.
    """

    def __init__(self):
        key_env = os.getenv("ENCRYPTION_KEY")
        if not key_env:
            # Жёстко требуем ENCRYPTION_KEY. config.py тоже валидирует это,
            # но crypto импортируется раньше Settings — поэтому дублируем проверку.
            raise RuntimeError(
                "ENCRYPTION_KEY is not set. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
                "and add it to .env as ENCRYPTION_KEY=<key>"
            )
        try:
            self.key = key_env.encode()
            self.cipher = Fernet(self.key)
            logger.info("CryptoService initialized from ENCRYPTION_KEY env var")
        except Exception as e:
            raise RuntimeError(f"Invalid ENCRYPTION_KEY in env: {e}") from e

    def encrypt(self, text: str) -> str:
        if not text:
            return None
        return self.cipher.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        if not token:
            return None
        try:
            return self.cipher.decrypt(token.encode()).decode()
        except InvalidToken:
            logger.error("Failed to decrypt (wrong key or old unencrypted data)")
            return None


crypto = CryptoService()
