"""audit fixes — add proxy_type, ProxyBinding, EmailHealth, html_template, lock timestamps

Revision ID: 0001_audit_fixes
Revises:
Create Date: 2026-07-04

Это первая миграция после аудита. Применяет ВСЕ изменения из аудиторского
отчёта:
- Proxy: proxy_type, rotation_mode, last_checked_at, fail_count
- UserSettings: parser_lock_at, mailer_lock_at (TTL для watchdog)
- Template: html_template (кастомный HTML-шаблон)
- GlobalSettings: новые зашифрованные колонки _api_key_*, _profile_id_*,
  _user_key_*, daily_limit (старые api_key_* колонки удаляются)
- Новые таблицы: proxy_bindings, email_health

ВАЖНО: эта миграция ДЕСТРУКТИВНА для таблицы global_settings — старые
(незашифрованные) значения api_key_* будут потеряны. Перед применением
сохраните ключи вручную и введите их заново через /admin.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_audit_fixes"
down_revision: Union[str, None] = "0000_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ✅ Эта миграция только для СУЩЕСТВУЮЩИХ БД (созданных до аудита).
    # Если 0000_initial уже создал таблицы с новой схемой — все batch_alter_table
    # операции будут no-op или упадут с "column already exists", что мы
    # молча игнорируем через try/except в каждой операции.

    # ─── proxies: новые колонки ──────────────────────────────────────────────
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_column(table, col):
        cols = [c["name"] for c in inspector.get_columns(table)]
        return col in cols

    def _has_table(table):
        return table in inspector.get_table_names()

    if _has_table("proxies"):
        with op.batch_alter_table("proxies") as batch_op:
            if not _has_column("proxies", "proxy_type"):
                batch_op.add_column(sa.Column("proxy_type", sa.String(16),
                                              server_default="socks5", nullable=False))
            if not _has_column("proxies", "rotation_mode"):
                batch_op.add_column(sa.Column("rotation_mode", sa.String(16),
                                              server_default="sticky", nullable=False))
            if not _has_column("proxies", "last_checked_at"):
                batch_op.add_column(sa.Column("last_checked_at", sa.DateTime(), nullable=True))
            if not _has_column("proxies", "fail_count"):
                batch_op.add_column(sa.Column("fail_count", sa.Integer(),
                                              server_default="0", nullable=False))

    # ─── user_settings: TTL timestamps для lock ──────────────────────────────
    if _has_table("user_settings"):
        with op.batch_alter_table("user_settings") as batch_op:
            if not _has_column("user_settings", "parser_lock_at"):
                batch_op.add_column(sa.Column("parser_lock_at", sa.DateTime(), nullable=True))
            if not _has_column("user_settings", "mailer_lock_at"):
                batch_op.add_column(sa.Column("mailer_lock_at", sa.DateTime(), nullable=True))

    # ─── templates: кастомный HTML ───────────────────────────────────────────
    if _has_table("templates"):
        with op.batch_alter_table("templates") as batch_op:
            if not _has_column("templates", "html_template"):
                batch_op.add_column(sa.Column("html_template", sa.Text(), server_default=""))

    # ─── global_settings: новые колонки (старые api_key_* удаляем если есть) ─
    if _has_table("global_settings"):
        with op.batch_alter_table("global_settings") as batch_op:
            if not _has_column("global_settings", "_api_key_ninjas"):
                batch_op.add_column(sa.Column("_api_key_ninjas", sa.String(256), server_default=""))
            if not _has_column("global_settings", "_api_key_deepseek"):
                batch_op.add_column(sa.Column("_api_key_deepseek", sa.String(256), server_default=""))
            if not _has_column("global_settings", "_api_key_mailtester"):
                batch_op.add_column(sa.Column("_api_key_mailtester", sa.String(256), server_default=""))
            if not _has_column("global_settings", "daily_limit"):
                batch_op.add_column(sa.Column("daily_limit", sa.Integer(),
                                              server_default="0", nullable=False))
            if not _has_column("global_settings", "_profile_id_tsum"):
                batch_op.add_column(sa.Column("_profile_id_tsum", sa.String(256), server_default=""))
            if not _has_column("global_settings", "_profile_id_nurrp"):
                batch_op.add_column(sa.Column("_profile_id_nurrp", sa.String(256), server_default=""))
            if not _has_column("global_settings", "_user_key_tsum"):
                batch_op.add_column(sa.Column("_user_key_tsum", sa.String(256), server_default=""))
            if not _has_column("global_settings", "_user_key_nurrp"):
                batch_op.add_column(sa.Column("_user_key_nurrp", sa.String(256), server_default=""))
            # Удаляем старые (незашифрованные) колонки если они есть
            if _has_column("global_settings", "api_key_ninjas"):
                batch_op.drop_column("api_key_ninjas")
            if _has_column("global_settings", "api_key_deepseek"):
                batch_op.drop_column("api_key_deepseek")
            if _has_column("global_settings", "api_key_mailtester"):
                batch_op.drop_column("api_key_mailtester")

    # ─── Новые таблицы (создаём только если их ещё нет) ──────────────────────
    if not _has_table("proxy_bindings"):
        op.create_table(
            "proxy_bindings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("email", sa.String(256), nullable=False),
            sa.Column("proxy_id", sa.Integer(), nullable=False),
            sa.Column("bound_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("user_id", "email", name="uq_proxy_binding_user_email"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_proxy_bindings_user_id", "proxy_bindings", ["user_id"])
        op.create_index("ix_proxy_bindings_proxy_id", "proxy_bindings", ["proxy_id"])

    if not _has_table("email_health"):
        op.create_table(
            "email_health",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("email", sa.String(256), nullable=False),
            sa.Column("sends_today", sa.Integer(), server_default="0", nullable=False),
            sa.Column("sends_this_hour", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_send_ts", sa.Integer(), server_default="0", nullable=False),
            sa.Column("hour_window_start", sa.Integer(), server_default="0", nullable=False),
            sa.Column("day_window_start", sa.Integer(), server_default="0", nullable=False),
            sa.Column("consecutive_errors", sa.Integer(), server_default="0", nullable=False),
            sa.Column("suspended_until", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at_ts", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "email", name="uq_email_health_user_email"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_email_health_user_id", "email_health", ["user_id"])


def downgrade() -> None:
    # Откатываем в обратном порядке
    op.drop_index("ix_email_health_user_id", table_name="email_health")
    op.drop_table("email_health")

    op.drop_index("ix_proxy_bindings_proxy_id", table_name="proxy_bindings")
    op.drop_index("ix_proxy_bindings_user_id", table_name="proxy_bindings")
    op.drop_table("proxy_bindings")

    with op.batch_alter_table("global_settings") as batch_op:
        batch_op.drop_column("_user_key_nurrp")
        batch_op.drop_column("_user_key_tsum")
        batch_op.drop_column("_profile_id_nurrp")
        batch_op.drop_column("_profile_id_tsum")
        batch_op.drop_column("daily_limit")
        batch_op.drop_column("_api_key_mailtester")
        batch_op.drop_column("_api_key_deepseek")
        batch_op.drop_column("_api_key_ninjas")

    with op.batch_alter_table("templates") as batch_op:
        batch_op.drop_column("html_template")

    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.drop_column("mailer_lock_at")
        batch_op.drop_column("parser_lock_at")

    with op.batch_alter_table("proxies") as batch_op:
        batch_op.drop_column("fail_count")
        batch_op.drop_column("last_checked_at")
        batch_op.drop_column("rotation_mode")
        batch_op.drop_column("proxy_type")
