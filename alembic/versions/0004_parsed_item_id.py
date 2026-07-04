"""add item_id to parsed_items

Revision ID: 0004_parsed_item_id
Revises: 0003_email_verifier
Create Date: 2026-07-04

Добавляет поле item_id в parsed_items — нужно для дедупликации email
по объявлению (все вариации одного объявления имеют один item_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_parsed_item_id"
down_revision: Union[str, None] = "0003_email_verifier"
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

    if _has_column("parsed_items", "item_id"):
        return

    with op.batch_alter_table("parsed_items") as batch_op:
        batch_op.add_column(sa.Column("item_id", sa.String(64),
                                       server_default="", nullable=False))
    # Индекс для быстрого поиска по item_id
    op.create_index("ix_parsed_items_item_id", "parsed_items", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_parsed_items_item_id", table_name="parsed_items")
    with op.batch_alter_table("parsed_items") as batch_op:
        batch_op.drop_column("item_id")
