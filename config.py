from pydantic_settings import BaseSettings
from pydantic import field_validator
import sys


class Settings(BaseSettings):
    BOT_TOKEN: str
    # ✅ MED-29: SQLite по умолчанию для dev, но поддерживаем PostgreSQL для prod.
    # Примеры DATABASE_URL:
    #   SQLite:      sqlite+aiosqlite:///bot_data.db
    #   PostgreSQL:  postgresql+asyncpg://user:password@localhost:5432/sender
    DATABASE_URL: str = "sqlite+aiosqlite:///bot_data.db"
    LOG_LEVEL: str = "INFO"
    LOG_MAX_BYTES: int = 10485760
    LOG_BACKUP_COUNT: int = 3
    FSM_TIMEOUT_SECONDS: int = 600
    REDIS_URL: str = ""
    ADMIN_ID: int = 0
    GOO_TEAM_KEY: str = ""
    ENCRYPTION_KEY: str = ""
    # ✅ MED-30: флаг включения Alembic-миграций при старте (False = create_all)
    USE_ALEMBIC: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @field_validator("ENCRYPTION_KEY")
    @classmethod
    def encryption_key_must_be_set(cls, v: str) -> str:
        if not v:
            print(
                "\n[FATAL] ENCRYPTION_KEY не задан в .env!\n"
                "Без этого ключа зашифрованные пароли нельзя восстановить.\n"
                "Сгенерируйте ключ командой:\n"
                "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
                "и добавьте его в .env как ENCRYPTION_KEY=<ключ>\n",
                file=sys.stderr,
            )
            sys.exit(1)
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_db_url(cls, v: str) -> str:
        """✅ MED-29: проверяем и нормализуем DATABASE_URL."""
        if not v:
            return "sqlite+aiosqlite:///bot_data.db"
        v = v.strip()
        if v.startswith("postgresql://"):
            # Авто-конвертация в asyncpg-формат
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def is_postgres(self) -> bool:
        """✅ MED-29: True если используется PostgreSQL (а не SQLite)."""
        return self.DATABASE_URL.startswith(("postgresql", "postgres"))


settings = Settings()

# Admin user ID — loaded from .env via Settings
ADMIN_ID = settings.ADMIN_ID

