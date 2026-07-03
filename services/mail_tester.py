"""
mail_tester.py — ОБНОВЛЁННЫЙ
Изменения:
  ✅ Расширен список спам-триггеров (немецкий/русский/польский)
  ✅ Улучшена система скоринга
  ✅ Проверка HTML-контента на подозрительные паттерны
  ✅ Добавлена проверка соотношения текст/ссылки
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

SPAM_TRIGGER_WORDS = {
    # English
    "free", "click here", "act now", "limited time", "exclusive offer",
    "congratulations", "winner", "prize", "guaranteed", "cash", "bonus",
    "urgent", "immediate action", "order now", "don't delete", "amazing",
    "100%", "satisfaction guaranteed", "no obligation", "risk-free",
    "buy direct", "get paid", "earn extra cash", "work from home",
    "amazing offer", "special promotion", "lowest price", "best price",
    "credit card", "social security", "bank transfer", "wire transfer",
    "money back", "call now", "toll free", "apply now", "subscribe now",
    "dear friend", "dear customer", "dear winner", "open now", "read now",
    # German
    "kostenlos", "klicken sie hier", "sofort handeln", "begrenzte zeit",
    "exklusives angebot", "gewinner", "preis", "garantiert", "bargeld",
    "dringend", "sofortige aktion", "jetzt bestellen", "sonderangebot",
    "niedrigster preis", "kreditkarte", "bankuberweisung",
    "geld zuruck", "jetzt anrufen", "jetzt bewerben",
    "kostenfrei", "gratis", "rabatt", "aktion", "angebot",
    "sparen", "sale", "prozente", "ermassigt", "reduziert",
    "exklusiv", "nur heute", "befristet", "solange der vorrat reicht",
    "keine zahlung", "risikolos", "zufriedenheit garantiert",
    "jetzt kaufen", "sofort zugreifen", "nicht verpassen",
    "gewinnen", "preiswert", "billig", "schnappchen",
    # Russian
    "бесплатно", "нажмите здесь", "срочно", "ограниченное время",
    "выигрыш", "победитель", "гарантировано", "бонус",
    "немедленно", "специальное предложение",
    # Polish
    "za darmo", "kliknij tutaj", "pilne", "ograniczona oferta",
    "zwyciezca", "nagroda", "gwarantowane", "bonus",
}

# ✅ НОВОЕ: подозрительные паттерны в HTML
SUSPICIOUS_HTML_PATTERNS = [
    r"display\s*:\s*none",       # скрытый текст
    r"font-size\s*:\s*[01]px",   # микротекст
    r"color\s*:\s*#fff\w*.*background\s*:\s*#fff",  # белый на белом
    r"<iframe",                   # встроенные фреймы
    r"<script",                   # скрипты в письме
    r"javascript:",               # JS-ссылки
    r"onload\s*=",                # JS-события
    r"onclick\s*=",               # JS-клики
]

SPAM_SCORE_THRESHOLD = 5


def check_spam_content(text: str) -> list[dict]:
    """Check text for spam trigger words and patterns."""
    issues = []
    lower = text.lower()

    # Check trigger words
    for word in SPAM_TRIGGER_WORDS:
        if word in lower:
            issues.append({"type": "trigger_word", "word": word})

    # Too many links
    link_count = len(re.findall(r'https?://[^\s]+', text))
    text_len = len(text) if len(text) > 0 else 1
    link_density = link_count / (text_len / 100)
    if link_density > 5:
        issues.append({"type": "link_density", "links_per_100chars": round(link_density, 1)})

    # Too many uppercase words
    words = text.split()
    upper_count = sum(1 for w in words if w.isupper() and len(w) > 2)
    if words and upper_count / len(words) > 0.3:
        issues.append({"type": "excessive_caps", "caps_ratio": round(upper_count / len(words), 2)})

    # Excessive punctuation
    punct_count = text.count("!") + text.count("?")
    if punct_count > 5:
        issues.append({"type": "excessive_punctuation", "count": punct_count})

    # ✅ НОВОЕ: проверка HTML-паттернов
    for pattern in SUSPICIOUS_HTML_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append({"type": "suspicious_html", "pattern": pattern[:30]})

    # ✅ НОВОЕ: слишком короткий текст (подозрительно)
    if len(text.strip()) < 20 and link_count > 0:
        issues.append({"type": "short_text_with_links", "text_len": len(text.strip())})

    return issues


def calculate_spam_score(text: str) -> int:
    """Calculate a spam score (0-100)."""
    issues = check_spam_content(text)
    score = 0
    for issue in issues:
        if issue["type"] == "trigger_word":
            score += 5
        elif issue["type"] == "link_density":
            score += 15 if issue["links_per_100chars"] > 10 else 8
        elif issue["type"] == "excessive_caps":
            score += 10
        elif issue["type"] == "excessive_punctuation":
            score += 5
        elif issue["type"] == "suspicious_html":
            score += 20
        elif issue["type"] == "short_text_with_links":
            score += 15
    return min(score, 100)


@dataclass
class AccountReputation:
    email: str
    total_sends: int = 0
    successful_sends: int = 0
    failed_sends: int = 0
    bounced: int = 0
    spam_complaints: int = 0
    consecutive_errors: int = 0
    last_error_type: str = ""
    spam_score_sum: float = 0.0
    warmup_complete: bool = False
    created_at: float = field(default_factory=time.time)
    last_warning_sent: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_sends == 0:
            return 1.0
        return self.successful_sends / self.total_sends

    @property
    def spam_risk(self) -> str:
        score = 0
        if self.total_sends > 10:
            score += (1 - self.success_rate) * 50
        if self.spam_score_sum > 0 and self.total_sends > 0:
            score += (self.spam_score_sum / self.total_sends) * 2
        if self.bounced > 3:
            score += 20
        if self.consecutive_errors > 3:
            score += 15
        if score < 20:
            return "low"
        elif score < 50:
            return "medium"
        else:
            return "high"

    def record_send_result(self, success: bool, error_type: str = "", spam_score: int = 0):
        self.total_sends += 1
        self.spam_score_sum += spam_score
        if success:
            self.successful_sends += 1
            self.consecutive_errors = 0
        else:
            self.failed_sends += 1
            self.consecutive_errors += 1
            self.last_error_type = error_type
            if "bounce" in error_type.lower() or "rejected" in error_type.lower():
                self.bounced += 1

    def needs_warning(self) -> bool:
        if self.spam_risk == "high" and time.time() - self.last_warning_sent > 3600:
            self.last_warning_sent = time.time()
            return True
        return False


_reputation_registry: dict[str, AccountReputation] = {}


def get_reputation(email: str) -> AccountReputation:
    if email not in _reputation_registry:
        _reputation_registry[email] = AccountReputation(email=email)
    return _reputation_registry[email]


def get_high_risk_accounts() -> list[AccountReputation]:
    return [a for a in _reputation_registry.values() if a.spam_risk == "high"]


def get_reputation_report() -> str:
    """✅ НОВОЕ: полный отчёт о репутации всех аккаунтов."""
    if not _reputation_registry:
        return "Нет данных о репутации."
    lines = []
    for email, rep in _reputation_registry.items():
        risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(rep.spam_risk, "⚪")
        lines.append(
            f"{risk_emoji} {email} | "
            f"отпр: {rep.total_sends} | "
            f"усп: {rep.success_rate:.0%} | "
            f"bounce: {rep.bounced} | "
            f"риск: {rep.spam_risk}"
        )
    return "\n".join(lines)
