import re

# Quote markers in multiple languages
_QUOTE_MARKERS = [
    # German
    r"Am\s+\w+\s+\d+\s+um\s+\d+:\d+\s+schrieb\s+",
    r"Am\s+\d+\.\s+\w+\.\s+\d{4}\s+um\s+\d+:\d+\s+schrieb\s+",
    r"Am\s+\d+\.\s+\w+\s+\d{4}\s+schrieb\s+",
    r"Von:\s+",
    r"Gesendet:\s+",
    r"Betreff:\s+",
    r"An:\s+",
    # English
    r"On\s+\w+\s+\d+,\s+\d{4}\s+at\s+\d+:\d+\s+[AP]M\s+",
    r"On\s+\w+,\s+\w+\s+\d+,\s+\d{4}\s+at\s+\d+:\d+\s+[AP]M\s+",
    r"On\s+\d+\s+\w+\s+\d{4}\s+at\s+\d+:\d+,\s+",
    r"On\s+\d+/\d+/\d+\s+",
    r"From:\s+",
    r"Sent:\s+",
    r"To:\s+",
    r"Subject:\s+",
    # French
    r"Le\s+\d+\s+\w+\s+\d{4}\s+",
    r"Le\s+\w+\s+\d+\s+\d{4}\s+",
    # Russian
    r"\d+\s+\w+\s+\d{4}\s+г\.\s+",
    # Generic
    r"Original Message",
    r"-----\s*Original\s+Message\s*-----",
    r"-----\s*Forwarded\s+Message\s*-----",
    r"Reply above this line",
    r"Написано\s+",
    r"От:\s+",
    r"Отправлено:\s+",
    r"Кому:\s+",
    r"Тема:\s+",
]


def clean_email_body(text: str) -> tuple[str, str]:
    """
    Split email body into (new_reply, full_text).
    Returns the new part only for display, with full text preserved.
    """
    if not text:
        return "", ""

    # Find first quote marker
    first_match_pos = len(text)

    # Check for lines starting with >
    for i, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if stripped.startswith(">") and i > 0:
            pos = text.find(line)
            if pos < first_match_pos:
                first_match_pos = pos
            break

    # Check for quote markers
    for marker in _QUOTE_MARKERS:
        match = re.search(marker, text, re.IGNORECASE)
        if match and match.start() < first_match_pos:
            first_match_pos = match.start()

    if first_match_pos >= len(text):
        # No quote found — entire text is "new"
        return text.strip(), text.strip()

    new_part = text[:first_match_pos].strip()
    old_part = text[first_match_pos:].strip()

    if not new_part:
        # Only quotes — take first 100 chars of the quote
        return old_part[:100] + "...", text.strip()

    return new_part, text.strip()


def format_body_for_display(full_body: str) -> str:
    """Return clean body for Telegram display."""
    if not full_body:
        return "(пустое письмо)"
    clean, _ = clean_email_body(full_body)
    return clean
