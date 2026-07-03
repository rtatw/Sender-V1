"""Alembic environment configuration for Sender-V1.

Читает DATABASE_URL из .env (через config.py), конвертирует async-драйвер
в sync-драйвер для Alembic (Alembic работает синхронно).
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Добавляем корень проекта в sys.path
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Импортируем все модели, чтобы Alembic видел их в metadata
from database.models import Base  # noqa: E402
target_metadata = Base.metadata

# Конвертируем DATABASE_URL (async) в sync-формат для Alembic
from config import settings as app_settings  # noqa: E402

DB_URL = app_settings.DATABASE_URL
if DB_URL.startswith("postgresql+asyncpg://"):
    SYNC_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
elif DB_URL.startswith("sqlite+aiosqlite:///"):
    SYNC_URL = DB_URL.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
else:
    SYNC_URL = DB_URL

config.set_main_option("sqlalchemy.url", SYNC_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
