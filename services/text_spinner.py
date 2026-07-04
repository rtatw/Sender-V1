"""
text_spinner.py — Шаг 3: Уникализация текста письма
Слегка меняет тело каждого письма чтобы избежать детектирования
одинакового контента спам-фильтрами.

Методы:
  1. Spin-синтаксис: {вариант1|вариант2|вариант3} → случайный выбор
  2. Невидимые unicode-разделители (zero-width chars) — уникален для каждого письма
  3. Случайные пробелы/переносы в конце абзацев
  4. Синонимы приветствий и подписей
"""

import random
import re
import hashlib
from typing import Optional


# ─── Spin-синтаксис ──────────────────────────────────────────────────────────

SPIN_PATTERN = re.compile(r"\{([^{}]+)\}")


def spin(text: str) -> str:
    """
    Заменяет {вариант1|вариант2|вариант3} на случайный вариант.
    Можно вкладывать: {привет|{здравствуйте|добрый день}}
    """
    def _replace(m):
        options = m.group(1).split("|")
        return random.choice(options).strip()

    # До 10 проходов для вложенных спинов
    for _ in range(10):
        new_text = SPIN_PATTERN.sub(_replace, text)
        if new_text == text:
            break
        text = new_text
    return text


# ─── Zero-width уникализация ─────────────────────────────────────────────────
#
# ВАЖНО (MED-36/MED-37):
#   1. \ufeff (BOM) убран — он ломает отображение в Outlook.
#   2. Невидимые символы добавляются ТОЛЬКО в HTML-часть письма
#      (через &amp;#8203;), не в plain text. В plain text ZW chars
#      добавляют штраф SpamAssassin (правило ZERO_WIDTH) и ломают
#      копирование текста из письма.
ZW_CHARS = [
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u2060",  # WORD JOINER
]


def inject_invisible_signature(text: str, recipient: str) -> str:
    """Встраивает уникальную невидимую подпись на основе email получателя.

    ВАЖНО: применять только к HTML-контенту. Для plain text использовать нельзя —
    SpamAssassin штрафует за ZERO_WIDTH, а Outlook ломается от BOM.
    """
    h = hashlib.md5(recipient.encode()).hexdigest()[:8]
    signature = ""
    for char in h:
        idx = int(char, 16) % len(ZW_CHARS)
        signature += ZW_CHARS[idx]

    lines = text.split("\n")
    if len(lines) > 2:
        mid = len(lines) // 2
        lines[mid] = lines[mid] + signature
        return "\n".join(lines)
    return text + signature


# ─── Вариативность приветствий и подписей ───────────────────────────────────

GREETINGS = [
    "Добрый день",
    "Здравствуйте",
    "Привет",
    "Добрый день,",
    "Уважаемый(-ая),",
]

CLOSINGS = [
    "С уважением,",
    "Всего доброго,",
    "Спасибо за внимание,",
    "С наилучшими пожеланиями,",
    "До свидания,",
]

GREETING_TOKEN = "{GREETING}"
CLOSING_TOKEN = "{CLOSING}"


def replace_tokens(text: str) -> str:
    """Заменяет токены {GREETING} и {CLOSING} на случайные варианты."""
    text = text.replace(GREETING_TOKEN, random.choice(GREETINGS))
    text = text.replace(CLOSING_TOKEN, random.choice(CLOSINGS))
    return text


# ─── Случайные пробелы в конце строк ────────────────────────────────────────

def add_random_whitespace(text: str) -> str:
    """
    Добавляет случайное количество пробелов в конец некоторых строк.
    Делает fingerprint письма уникальным для спам-фильтров.
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        # ~30% строк получают дополнительный пробел
        if line.strip() and random.random() < 0.3:
            line = line + " " * random.randint(1, 3)
        result.append(line)
    return "\n".join(result)


# ─── Главная функция ─────────────────────────────────────────────────────────

def uniquify_text(text: str, recipient: str, use_invisible: bool = True) -> str:
    """Полная уникализация текста письма (для PLAIN TEXT части).

    ВАЖНО (MED-36): use_invisible здесь игнорируется для plain text —
    ZW chars добавляют штраф SpamAssassin и ломают копирование.
    Для HTML-части используйте отдельную функцию uniquify_html().
    """
    text = spin(text)
    text = replace_tokens(text)
    text = add_random_whitespace(text)
    # ZW chars в plain text не добавляем — см. комментарий выше.
    return text


def uniquify_html(html_text: str, recipient: str) -> str:
    """Уникализация HTML-части письма (можно использовать ZW chars)."""
    # Случайные пробелы и spin — безопасно для HTML
    html_text = spin(html_text)
    html_text = replace_tokens(html_text)
    html_text = inject_invisible_signature(html_text, recipient)
    return html_text


def uniquify_subject(subject: str, recipient: str) -> str:
    """Уникализация темы письма (без невидимых символов — они ломают тему)."""
    subject = spin(subject)
    subject = replace_tokens(subject)
    return subject
