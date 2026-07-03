from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Optional


def get_settings_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = [
        ("Шаблоны", "settings_templates"),
        ("Тема письма ✍️", "settings_subjects"),
        ("Умные пресеты 📚", "settings_smart_presets"),
        ("Почты 📩", "settings_emails"),
        ("Loma Proxy 🖥️", "settings_proxies"),
        ("Profile 👤", "settings_profile"),
        ("Команда 🎮", "settings_command"),
        ("❌ Скрыть", "settings_hide"),
    ]
    for text, cb in buttons:
        builder.button(text=text, callback_data=cb)

    builder.adjust(2, 2, 2, 1, 2, 1, 1)

    return builder.as_markup()


def get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    admin_buttons = [
        ("Domains ❤️", "settings_domains"),
        ("Card", "settings_card"),
        ("Спуфинг 🟢", "settings_spoofing"),
        ("Подмена ника", "settings_nick"),
        ("Тайминги ⏳", "settings_timings"),
        ("Лимиты рассылки", "settings_mail_limits"),
        ("🔑 Mailtester Keys", "settings_mailtester_keys"),
        ("🤖 DeepSeek Key", "settings_deepseek"),
    ]
    for text, cb in admin_buttons:
        builder.button(text=text, callback_data=cb)
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(2, 2, 2, 1, 1, 1)
    return builder.as_markup()


def get_back_settings_keyboard(extra_button: Optional[tuple[str, str]] = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if extra_button:
        builder.button(text=extra_button[0], callback_data=extra_button[1])
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_cancel_keyboard(callback_data: str = "cancel_action") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=callback_data)
    return builder.as_markup()


# --- Domains ---

def get_domains_keyboard(priority: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить домен", callback_data="domains_add")
    if priority:
        builder.button(text="✏️ Изменить приоритет", callback_data="domains_edit")
    builder.button(text="🗑️ Удалить домен", callback_data="domains_delete")
    builder.button(text="🔄 Сбросить", callback_data="domains_reset")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_domains_delete_keyboard(domains: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for d in domains:
        builder.button(text=f"🗑️ {d}", callback_data=f"domains_del_{d}")
    builder.button(text="🔙 Назад", callback_data="settings_domains")
    builder.adjust(1)
    return builder.as_markup()


def get_domains_edit_keyboard(domains: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, d in enumerate(domains):
        builder.button(text=f"{i+1}. {d}", callback_data=f"domains_order_{i}")
    builder.button(text="🔙 Назад", callback_data="settings_domains")
    builder.adjust(2)
    return builder.as_markup()


# --- Templates ---

def get_templates_menu_keyboard(templates_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить пресет", callback_data="template_add")
    if templates_count > 0:
        builder.button(text="✏️ Изменить пресет", callback_data="template_edit")
        builder.button(text="🗑️ Удалить пресет", callback_data="template_delete")
        builder.button(text="🗑️ Удалить все", callback_data="template_delete_all")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_templates_list_keyboard(templates: list, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, tpl in enumerate(templates):
        builder.button(text=f"{i+1}. {tpl.name[:20]}", callback_data=f"{action}_{tpl.id}")
    builder.button(text="🔙 Назад", callback_data="settings_templates")
    builder.adjust(1)
    return builder.as_markup()


# --- Subjects ---

def get_subjects_menu_keyboard(subjects_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить тему", callback_data="subject_add")
    if subjects_count > 0:
        builder.button(text="✏️ Изменить тему", callback_data="subject_edit")
        builder.button(text="🗑️ Удалить тему", callback_data="subject_delete")
        builder.button(text="🗑️ Удалить все", callback_data="subject_delete_all")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_subjects_list_keyboard(subjects: list, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, sub in enumerate(subjects):
        builder.button(text=f"{i+1}. {sub.subject[:30]}", callback_data=f"{action}_{sub.id}")
    builder.button(text="🔙 Назад", callback_data="settings_subjects")
    builder.adjust(1)
    return builder.as_markup()


# --- Smart Presets ---

def get_smart_presets_keyboard(presets: list, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(presets)
    total_pages = max(1, (total + per_page - 1) // per_page)

    start = page * per_page
    end = start + per_page
    page_items = presets[start:end]

    for p in page_items:
        builder.button(text=p.name[:30], callback_data=f"preset_view_{p.id}")

    if total > per_page:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"presets_page_{page-1}"))
        nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="presets_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"presets_page_{page+1}"))
        builder.row(*nav_row)

    builder.button(text="➕ Добавить пресет", callback_data="smart_preset_add")
    if total > 0:
        builder.button(text="✏️ Изменить пресет", callback_data="smart_preset_edit")
        builder.button(text="🗑️ Удалить пресет", callback_data="smart_preset_delete")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_smart_presets_list_keyboard(presets: list, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, p in enumerate(presets):
        builder.button(text=f"{i+1}. {p.name[:25]}", callback_data=f"{action}_{p.id}")
    builder.button(text="🔙 Назад", callback_data="settings_smart_presets")
    builder.adjust(1)
    return builder.as_markup()


# --- Proxies ---

def get_proxies_menu_keyboard(proxies_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить прокси", callback_data="proxy_add")
    if proxies_count > 0:
        builder.button(text="✏️ Изменить прокси", callback_data="proxy_edit")
        builder.button(text="✅ Проверить все", callback_data="proxy_check_all")
        builder.button(text="🗑️ Удалить прокси", callback_data="proxy_delete")
        builder.button(text="🗑️ Удалить все", callback_data="proxy_delete_all")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_proxies_list_keyboard(proxies: list, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, p in enumerate(proxies):
        status_icon = "🟢" if p.status == "alive" else ("🔴" if p.status == "dead" else "⚪")
        builder.button(text=f"{status_icon} {i+1}. {p.host}:{p.port}", callback_data=f"{action}_{p.id}")
    builder.button(text="🔙 Назад", callback_data="settings_proxies")
    builder.adjust(1)
    return builder.as_markup()


# --- Emails (Sending) ---

def get_emails_menu_keyboard(emails_count: int, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📧 Выбрать E-mail", callback_data="email_select")
    if emails_count > 0:
        builder.button(text="➕ Добавить E-mail", callback_data="email_add_menu")
        builder.button(text="🧪 Тест E-mail", callback_data="email_test_all")
        builder.button(text="🗑️ Удалить все", callback_data="email_delete_all")
    else:
        builder.button(text="➕ Добавить E-mail", callback_data="email_add_menu")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_email_add_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Одно имя", callback_data="email_add_single")
    builder.button(text="📋 Список", callback_data="email_add_list")
    builder.button(text="🔙 Назад", callback_data="settings_emails")
    builder.adjust(1)
    return builder.as_markup()


def get_emails_list_keyboard(emails: list, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(emails)
    total_pages = max(1, (total + per_page - 1) // per_page)

    start = page * per_page
    end = start + per_page
    page_items = emails[start:end]

    for i, e in enumerate(page_items):
        status_icon = "✅" if e.is_valid else "❌"
        display = e.display_name or e.email.split("@")[0]
        builder.button(text=f"{status_icon} {start+i+1}. {display[:25]}", callback_data=f"email_detail_{e.id}")

    if total > per_page:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"emails_page_{page-1}"))
        nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="emails_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"emails_page_{page+1}"))
        builder.row(*nav_row)

    builder.button(text="🔙 Назад", callback_data="settings_emails")
    builder.adjust(1)
    return builder.as_markup()


def get_email_detail_keyboard(email_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧪 Тест E-mail", callback_data=f"email_test_{email_id}")
    builder.button(text="🗑️ Удалить E-mail", callback_data=f"email_delete_{email_id}")
    builder.button(text="🔙 Назад", callback_data="email_select")
    builder.adjust(1)
    return builder.as_markup()


# --- Receive Emails ---

def get_receive_menu_keyboard(count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить E-mail", callback_data="receive_add")
    if count > 0:
        builder.button(text="📥 Проверить", callback_data="receive_check")
        builder.button(text="🗑️ Удалить E-mail", callback_data="receive_delete")
        builder.button(text="🗑️ Удалить все", callback_data="receive_delete_all")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_receive_list_keyboard(emails: list, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, e in enumerate(emails):
        builder.button(text=f"{i+1}. {e.email[:30]}", callback_data=f"{action}_{e.id}")
    builder.button(text="🔙 Назад", callback_data="settings_receive")
    builder.adjust(1)
    return builder.as_markup()


# --- Timings ---

def get_timings_keyboard(timing_min: int, timing_max: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить интервал", callback_data="timing_edit")
    builder.button(text="🔄 Сбросить интервал", callback_data="timing_reset")
    builder.button(text="📥 Интервал проверки входящих", callback_data="receive_interval_set")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


# --- Command ---

def get_command_keyboard(current_command: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    commands = ["Aqua", "Tsum", "Nurrp", "OPG"]
    for cmd in commands:
        prefix = "✅ " if cmd == current_command else ""
        builder.button(text=f"{prefix}{cmd}", callback_data=f"command_set_{cmd}")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


# --- Spoofing sub-menu ---

def get_spoofing_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Установить имя отправителя", callback_data="spoofing_set")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_nick_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Установить ник", callback_data="nick_set")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_sub_theme_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Установить тему подмены", callback_data="subtheme_set")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


def get_text_theme_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Установить текст темы", callback_data="texttheme_set")
    builder.button(text="🔙 Назад", callback_data="back_settings")
    builder.adjust(1)
    return builder.as_markup()


# --- Messaging ---

MESSAGES = {
    "settings_header": "⚙️ <b>Настройки</b>\n\nВыберите нужный раздел:",
    "domains_current": "💌 <b>Домены для поиска</b>\n\nСписок: <code>{priority}</code>",
    "domains_edit_prompt": "Введите домены через запятую в порядке приоритета:\n\nНапример: <code>gmail.com,mail.ru,gmx.de</code>",
    "domains_add_prompt": "Введите домен для добавления:\n\nНапример: <code>yandex.ru</code>",
    "domains_added": "✅ Домен <b>{domain}</b> добавлен.",
    "domains_deleted": "✅ Домен <b>{domain}</b> удалён.",
    "domains_invalid": "❌ Введите корректный домен (например: gmail.com).",
    "domains_updated": "✅ Приоритет обновлён: <code>{priority}</code>",
    "domains_duplicate": "❌ Домен уже в списке.",
    "templates_menu": "🗂️ <b>Управление шаблонами</b>\n\nВсего шаблонов: {count}",
    "templates_list": "📋 <b>Список шаблонов:</b>\n\n{templates}",
    "template_add_name": "Введите название шаблона (до 32 символов, A-Z):",
    "template_add_text": "Введите текст шаблона (до 1024 символов):",
    "template_saved": "✅ Шаблон <b>{name}</b> сохранён.",
    "template_deleted": "✅ Шаблон удалён.",
    "template_all_deleted": "✅ Все шаблоны удалены.",
    "template_name_invalid": "❌ Название должно быть до 32 символов, только буквы, цифры и пробелы.",
    "template_text_invalid": "❌ Текст должен быть до 1024 символов.",
    "subjects_menu": "✍️ <b>Темы писем</b>\n\nВсего тем: {count}",
    "subject_add": "Введите текст темы письма (до 200 символов):",
    "subject_saved": "✅ Тема сохранена.",
    "subject_deleted": "✅ Тема удалена.",
    "subject_all_deleted": "✅ Все темы удалены.",
    "proxies_menu": "💻 <b>Loma Proxy</b>\n\nВсего прокси: {count}",
    "proxy_add": "Введите прокси в формате:\n<code>host:port:user:pass</code>\n\nМожно несколько, каждый с новой строки:",
    "proxy_added": "✅ Добавлено прокси: {count}",
    "proxy_deleted": "✅ Прокси удалён.",
    "proxy_all_deleted": "✅ Все прокси удалены.",
    "proxy_checking": "🔍 Проверка прокси... ({checked}/{total})",
    "proxy_check_done": "✅ Проверка завершена.\n🟢 Работает: {alive}\n🔴 Не работает: {dead}",
    "emails_menu": "📩 <b>Почты для рассылки</b>\n\nВсего аккаунтов: {count}",
    "email_add_name": "Введите отображаемое имя (например, Emma Gross):",
    "email_add_email": "Введите E-mail адрес:",
    "email_add_password": "Введите пароль (app-password):",
    "email_saved": "✅ E-mail сохранён.",
    "email_deleted": "✅ E-mail удалён.",
    "email_all_deleted": "✅ Все E-mail удалены.",
    "email_test_send": "Введите email получателя для тестового письма:",
    "email_test_sent": "✅ Тестовое письмо отправлено на {target}",
    "email_test_failed": "❌ Не удалось отправить тестовое письмо: {error}",
    "receive_menu": "📨 <b>Приём ответов</b>\n\nВсего: {count}",
    "receive_add": "Введите E-mail для приёма ответов:",
    "receive_saved": "✅ E-mail для приёма сохранён.",
    "receive_deleted": "✅ E-mail удалён.",
    "receive_all_deleted": "✅ Все E-mail удалены.",
    "timings_current": "⏳ <b>Тайминги</b>\n\nТекущий интервал: {min} — {max} секунд",
    "timing_edit": "Введите два числа через пробел (мин макс):\nНапример: <code>5 15</code>",
    "timing_updated": "✅ Интервал обновлён: {min} — {max} секунд",
    "timing_reset": "✅ Интервал сброшен: 5 — 15 секунд",
    "timing_invalid": "❌ Введите два целых числа через пробел.",
    "spoofing_set": "Введите имя отправителя (до 64 символов):",
    "spoofing_saved": "✅ Имя отправителя установлено: {name}",
    "nick_set": "Введите отображаемый ник (до 64 символов):",
    "nick_saved": "✅ Ник установлен: {nick}",
    "subtheme_set": "Введите тему для подмены (до 128 символов):",
    "subtheme_saved": "✅ Тема подмены установлена: {theme}",
    "texttheme_set": "Введите текст темы (до 1024 символов):",
    "texttheme_saved": "✅ Текст темы сохранён.",
    "control_toggled": "🟢 Контроль блокировок: {status}",
    "profile_set": "Введите Profile ID (UUID):",
    "profile_saved": "✅ Profile ID сохранён.",
    "key_set": "Введите API ключ:",
    "key_saved": "✅ API ключ сохранён.",
    "command_current": "🎭 <b>Команда</b>\n\nТекущая: <b>{command}</b>",
    "command_set": "✅ Команда изменена на: {command}",
    "giro_info": "📊 <b>Giro</b>\n\nСтатистика недоступна. Функция в разработке.",
    "cancel_action": "❌ Действие отменено.",
    "no_items": "ℹ️ Нет данных.",
    "parser_lock": "⏳ У вас уже идёт подбор. Дождитесь завершения.",
    "file_no_items": "❌ Файл не содержит подходящих данных.",
    "file_parsed_json": "📊 <b>Результат парсинга JSON:</b>\nНайдено элементов: {found}\nДобавлено новых: {added}",
    "file_parsed_txt": "📝 Начата обработка TXT файла.\nСтрок: {lines}\nЗапущен поиск email...",
    "progress_bar": "{bar} {percent}%",
}
