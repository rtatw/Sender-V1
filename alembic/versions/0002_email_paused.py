"""add is_paused to email_accounts

Revision ID: 0002_email_paused
Revises: 0001_audit_fixes
Create Date: 2026-07-04

Добавляет поле is_paused в таблицу email_accounts — нужно для нового
меню "Почты" с паузой аккаунтов (не участвует в рассылке, но не удалён).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_email_paused"
down_revision: Union[str, None] = "0001_audit_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table, col):
        if table not in inspector.get_table_names():
            return False
        cols = [c["name"] for c in inspector.get_columns(table)]
        return col in cols

    if _has_column("email_accounts", "is_paused"):
        return

    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.add_column(sa.Column("is_paused", sa.Boolean(),
                                       server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.drop_column("is_paused")
