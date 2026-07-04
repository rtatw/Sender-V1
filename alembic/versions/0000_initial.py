"""initial schema — создать все таблицы с нуля (для свежей БД)

Revision ID: 0000_initial
Revises:
Create Date: 2026-07-04

Это INITIAL migration — создаёт все таблицы с нуля, используя
SQLAlchemy metadata. Используется для свежей БД.

Если у вас уже есть БД со старой схемой (до аудита) — НЕ применяйте
эту миграцию. Используйте 0001_audit_fixes поверх существующей БД.
Для этого отметьте текущее состояние как применённое:
    alembic stamp 0001_audit_fixes
Затем продолжайте работать с миграциями.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Base  # noqa: E402

revision: str = "0000_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Создаём все таблицы из metadata (включая все поля из аудита)
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
