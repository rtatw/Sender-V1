"""database/models.py — ОБНОВЛЁННЫЙ

Изменения:
- Добавлены profile_id_* для команд (Aqua/Tsum/Nurrp/OPG)
- Старое поле profile_id оставлено для совместимости (будет использоваться как fallback)

Важно:
- Миграции нет. Для SQLite достаточно удалить БД или выполнить ALTER TABLE вручную.
"""

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Boolean,
    DateTime,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from services.crypto_service import crypto


class ItemStatus:
    PENDING = "pending"
    DONE = "done"


class ProxyStatus:
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


class Base(DeclarativeBase):
    pass


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    display_name = Column(String(256), default="")
    username = Column(String(128), default="")

    # Cascade delete: removing UserSettings removes all related data
    parsed_items = relationship(
        "ParsedItem",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(ParsedItem.user_id)",
        uselist=True,
    )
    email_accounts = relationship(
        "EmailAccount",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(EmailAccount.user_id)",
        uselist=True,
    )
    proxies = relationship(
        "Proxy",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(Proxy.user_id)",
        uselist=True,
    )
    templates = relationship(
        "Template",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(Template.user_id)",
        uselist=True,
    )
    subjects = relationship(
        "Subject",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(Subject.user_id)",
        uselist=True,
    )
    receive_emails = relationship(
        "ReceiveEmail",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(ReceiveEmail.user_id)",
        uselist=True,
    )
    incoming_messages = relationship(
        "IncomingMessage",
        cascade="all, delete-orphan",
        primaryjoin="UserSettings.user_id == foreign(IncomingMessage.user_id)",
        uselist=True,
    )

    _api_key = Column("api_key", String(256), default="")

    # Старый общий профиль (fallback)
    profile_id = Column(String(256), default="")

    # ✅ НОВОЕ: profileID на команду
    profile_id_aqua = Column(String(256), default="")
    profile_id_tsum = Column(String(256), default="")
    profile_id_nurrp = Column(String(256), default="")
    profile_id_opg = Column(String(256), default="")
    user_key_tsum = Column(String(256), default="")
    user_key_nurrp = Column(String(256), default="")

    timing_min = Column(Integer, default=5)
    timing_max = Column(Integer, default=15)
    domain_priority = Column(Text, default="gmail.com")

    spoofing_sender = Column(String(256), default="")
    spoofing_nick = Column(String(256), default="")
    spoofing_theme = Column(String(256), default="")
    text_theme = Column(Text, default="")

    control_block = Column(Boolean, default=True)
    active_command = Column(String(32), default="OPG")

    _api_key_deepseek = Column("api_key_deepseek", String(256), default="")
    _api_key_mailtester = Column("api_key_mailtester", String(256), default="")

    parser_lock = Column(Boolean, default=False)
    mailer_lock = Column(Boolean, default=False)
    # ✅ MED-33: timestamp когда lock установлен — для auto-reset при crash
    # Если lock=True, но last_lock_at старее 1 часа — считаем что процесс упал
    parser_lock_at = Column(DateTime, nullable=True)
    mailer_lock_at = Column(DateTime, nullable=True)

    receive_check_interval = Column(Integer, default=30)
    last_imap_uid = Column(Text, default="")

    card_enabled = Column(Boolean, default=False)
    offer_key = Column(String(64), default="")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @property
    def api_key(self):
        d = crypto.decrypt(self._api_key)
        return d if d is not None else self._api_key

    @api_key.setter
    def api_key(self, value):
        self._api_key = crypto.encrypt(str(value)) if value else ""

    @property
    def api_key_deepseek(self):
        d = crypto.decrypt(self._api_key_deepseek)
        return d if d is not None else self._api_key_deepseek

    @api_key_deepseek.setter
    def api_key_deepseek(self, value):
        self._api_key_deepseek = crypto.encrypt(str(value)) if value else ""

    @property
    def api_key_mailtester(self):
        d = crypto.decrypt(self._api_key_mailtester)
        return d if d is not None else self._api_key_mailtester

    @api_key_mailtester.setter
    def api_key_mailtester(self, value):
        self._api_key_mailtester = crypto.encrypt(str(value)) if value else ""


class ParsedItem(Base):
    __tablename__ = "parsed_items"
    __table_args__ = (
        UniqueConstraint("user_id", "nickname", name="uq_user_nickname"),
        Index("ix_parsed_user_status", "user_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    nickname = Column(String(256), nullable=False)
    person_name = Column(String(256), default="")
    title = Column(String(512), default="")
    photo = Column(Text, default="")
    link = Column(String(512), default="")
    price = Column(String(64), default="")
    location = Column(String(256), default="")
    # ✅ NEW: item_id из источника (kleinanzeigen.de, etc.) — для дедупликации
    # Все вариации одного и того же объявления имеют один item_id
    item_id = Column(String(64), default="", index=True)
    found_email = Column(String(256), default="")
    status = Column(String(32), default="pending")
    created_at = Column(DateTime, server_default=func.now())


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    email = Column(String(256), nullable=False)
    _password = Column("password", String(512), nullable=False)
    display_name = Column(String(128), default="")
    is_valid = Column(Boolean, default=True)
    # ✅ NEW: пауза для аккаунта (не участвует в рассылке, но не удалён)
    is_paused = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    @property
    def password(self):
        return crypto.decrypt(self._password)

    @password.setter
    def password(self, value):
        self._password = crypto.encrypt(str(value))


class Proxy(Base):
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    host = Column(String(256), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(256), default="")
    _password = Column("password", String(256), default="")
    # ✅ Тип прокси: "socks5" (по умолчанию, лучше для SMTP), "http" или "socks4"
    proxy_type = Column(String(16), default="socks5", nullable=False)
    # ✅ Режим ротации: "sticky" (привязка к аккаунту) или "rotating" (каждый запрос с нового IP)
    rotation_mode = Column(String(16), default="sticky", nullable=False)
    is_active = Column(Boolean, default=True)
    status = Column(String(32), default="unknown")
    last_checked_at = Column(DateTime, nullable=True)
    fail_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    @property
    def password(self):
        if not self._password:
            return ""
        return crypto.decrypt(self._password)

    @password.setter
    def password(self, value):
        if value:
            self._password = crypto.encrypt(str(value))
        else:
            self._password = ""


class ProxyBinding(Base):
    """Персистентная привязка email → proxy.

    Заменяет in-memory md5-привязку в proxy_binding.py, которая ломалась
    при любом изменении списка прокси (idx = md5(email) % len(proxies)
    давал разные индексы после добавления/удаления).
    """
    __tablename__ = "proxy_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_proxy_binding_user_email"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    email = Column(String(256), nullable=False)
    proxy_id = Column(Integer, nullable=False, index=True)
    bound_at = Column(DateTime, server_default=func.now())
    last_used_at = Column(DateTime, nullable=True)


class Template(Base):
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(32), nullable=False)
    text = Column(Text, nullable=False)
    # ✅ MED-9: кастомный HTML-шаблон для письма (если пусто — используется default)
    # Должен содержать маркер {{body}} — туда подставляется body_text.
    # Пример: '<div style="font-family:Arial">{{body}}</div><img src="logo.png">'
    html_template = Column(Text, default="")
    type = Column(String(32), default="custom")
    created_at = Column(DateTime, server_default=func.now())


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    subject = Column(String(512), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class ReceiveEmail(Base):
    __tablename__ = "receive_emails"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_receive_user_email"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    email = Column(String(256), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class IncomingMessage(Base):
    __tablename__ = "incoming_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    account_email = Column(String(256), nullable=False)
    from_email = Column(String(256), default="")
    subject = Column(String(512), default="")
    body = Column(Text, default="")
    imap_uid = Column(String(64), default="")
    telegram_msg_id = Column(Integer, default=0)
    is_bounce = Column(Boolean, default=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class GlobalSettings(Base):
    """Глобальные настройки, общие для всех пользователей бота.

    Админ задаёт эти значения через /admin-меню. Per-user UserSettings
    могут переопределять часть полей (см. services/team_config.resolve_profile_id).
    Все API-ключи шифруются через property *_plain.
    """
    __tablename__ = "global_settings"

    id = Column(Integer, primary_key=True, default=1)
    _api_key_ninjas = Column("api_key_ninjas", String(256), default="")
    _api_key_deepseek = Column("api_key_deepseek", String(256), default="")
    _api_key_mailtester = Column("api_key_mailtester", String(256), default="")

    # ✅ Глобальный дневной лимит отправки (0 = использовать доменный лимит по умолчанию 100)
    daily_limit = Column(Integer, default=0, nullable=False)

    # ✅ Глобальный интервал проверки входящих (сек). Админ задаёт — все используют.
    receive_check_interval = Column(Integer, default=30, nullable=False)

    # ✅ NEW: метод проверки email при поиске (холодный подбор vs Mailtester API)
    #   "smtp_bypass" — холодный подбор через SMTP RCPT TO (бесплатно, через прокси)
    #   "mailtester"  — через mailtester.ninja API (нужны ключи, платно)
    #   "both"        — сначала SMTP, если не уверен — проверка mailtester'ом
    email_verify_method = Column(String(16), default="smtp_bypass", nullable=False)

    # ✅ Глобальные Profile ID / User Keys для команд (goo.network)
    _profile_id_tsum = Column("profile_id_tsum", String(256), default="")
    _profile_id_nurrp = Column("profile_id_nurrp", String(256), default="")
    _user_key_tsum = Column("user_key_tsum", String(256), default="")
    _user_key_nurrp = Column("user_key_nurrp", String(256), default="")

    @property
    def api_key_ninjas_plain(self):
        d = crypto.decrypt(self._api_key_ninjas)
        return d if d is not None else self._api_key_ninjas

    @api_key_ninjas_plain.setter
    def api_key_ninjas_plain(self, value):
        self._api_key_ninjas = crypto.encrypt(str(value)) if value else ""

    @property
    def api_key_deepseek_plain(self):
        d = crypto.decrypt(self._api_key_deepseek)
        return d if d is not None else self._api_key_deepseek

    @api_key_deepseek_plain.setter
    def api_key_deepseek_plain(self, value):
        self._api_key_deepseek = crypto.encrypt(str(value)) if value else ""

    @property
    def api_key_mailtester_plain(self):
        d = crypto.decrypt(self._api_key_mailtester)
        return d if d is not None else self._api_key_mailtester

    @api_key_mailtester_plain.setter
    def api_key_mailtester_plain(self, value):
        self._api_key_mailtester = crypto.encrypt(str(value)) if value else ""

    @property
    def profile_id_tsum(self):
        d = crypto.decrypt(self._profile_id_tsum)
        return d if d is not None else self._profile_id_tsum

    @profile_id_tsum.setter
    def profile_id_tsum(self, value):
        self._profile_id_tsum = crypto.encrypt(str(value)) if value else ""

    @property
    def profile_id_nurrp(self):
        d = crypto.decrypt(self._profile_id_nurrp)
        return d if d is not None else self._profile_id_nurrp

    @profile_id_nurrp.setter
    def profile_id_nurrp(self, value):
        self._profile_id_nurrp = crypto.encrypt(str(value)) if value else ""

    @property
    def user_key_tsum(self):
        d = crypto.decrypt(self._user_key_tsum)
        return d if d is not None else self._user_key_tsum

    @user_key_tsum.setter
    def user_key_tsum(self, value):
        self._user_key_tsum = crypto.encrypt(str(value)) if value else ""

    @property
    def user_key_nurrp(self):
        d = crypto.decrypt(self._user_key_nurrp)
        return d if d is not None else self._user_key_nurrp

    @user_key_nurrp.setter
    def user_key_nurrp(self, value):
        self._user_key_nurrp = crypto.encrypt(str(value)) if value else ""


class MailtesterKey(Base):
    __tablename__ = "mailtester_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(256), nullable=False)
    is_active = Column(Boolean, default=True)
    last_checked = Column(DateTime)
    is_valid = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class EmailHealth(Base):
    """Персистентное состояние здоровья email-аккаунта (HIGH-5).

    Ранее _health_registry в anti_ban.py был in-memory dict — терялся
    при рестарте, бот думал что аккаунты «свежие» и сыпал по ним с
    полной скоростью, игнорируя дневные лимиты.
    Теперь — БД. При старте бота _health_registry загружается из этой
    таблицы. Уникальный ключ: user_id + email.
    """
    __tablename__ = "email_health"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_email_health_user_email"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    email = Column(String(256), nullable=False)

    # Счётчики
    sends_today = Column(Integer, default=0, nullable=False)
    sends_this_hour = Column(Integer, default=0, nullable=False)

    # Timestamps (Unix epoch, как в AccountHealth)
    last_send_ts = Column(Integer, default=0, nullable=False)
    hour_window_start = Column(Integer, default=0, nullable=False)
    day_window_start = Column(Integer, default=0, nullable=False)

    # Состояние
    consecutive_errors = Column(Integer, default=0, nullable=False)
    suspended_until = Column(Integer, default=0, nullable=False)
    created_at_ts = Column(Integer, nullable=False)  # когда аккаунт добавлен

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AdminRole(Base):
    """Таблица прав для администраторов и помощников."""
    __tablename__ = "admin_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, unique=True, nullable=False, index=True)
    role = Column(String(32), default="assistant", nullable=False)
    # JSON-строка с правами: {"clear_db": true, "mail_limits": false, ...}
    permissions = Column(Text, default="{}")
    created_at = Column(DateTime, server_default=func.now())


ADMIN_PERMISSIONS = {
    "manage_admins": "👥 Управление помощниками",
    "view_users": "👁️ Просмотр пользователей",
    "stats": "📊 Статистика",
    "clear_db": "🗑️ Очистка БД",
    "mail_limits": "📊 Лимиты рассылки",
    "email_verifier": "🔍 Метод проверки email",
    "mailtester_keys": "🔑 Mailtester ключи",
    "deepseek_key": "🤖 DeepSeek ключ",
    "receive_interval": "📥 Интервал входящих",
    "domains": "✉️ Domains",
    "card": "🃏 Card",
    "spoofing": "🎭 Спуфинг",
    "timings": "⏳ Тайминги",
    "view_logs": "📋 Просмотр логов",
    "restart_bot": "🔄 Перезапуск бота",
}


def get_default_assistant_permissions() -> str:
    import json
    return json.dumps({k: False for k in ADMIN_PERMISSIONS})
