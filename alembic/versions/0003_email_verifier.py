"""add email_verify_method to global_settings

Revision ID: 0003_email_verifier
Revises: 0002_email_paused
Create Date: 2026-07-04

Добавляет поле email_verify_method в global_settings — выбор метода
проверки email при поиске (холодный подбор SMTP vs Mailtester API).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_email_verifier"
down_revision: Union[str, None] = "0002_email_paused"
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

    if _has_column("global_settings", "email_verify_method"):
        return

    with op.batch_alter_table("global_settings") as batch_op:
        batch_op.add_column(sa.Column("email_verify_method", sa.String(16),
                                       server_default="smtp_bypass", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("global_settings") as batch_op:
        batch_op.drop_column("email_verify_method")
