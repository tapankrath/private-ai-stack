"""Redaction (secrets never stored) and PII tagging (raises sensitivity).

Secrets are REPLACED before anything is stored or indexed. PII is TAGGED (and raises the
sensitivity label) but kept, because the owner's own documents legitimately contain it.
Presidio can be plugged in for better PII recall; these regexes are the always-on baseline.
"""

from __future__ import annotations

import re

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----", re.S)),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("password_assignment", re.compile(
        r"(?i)\b(pass(?:word|wd|phrase)?|pwd|secret|api[_ -]?key|token)\b(\s*(?:[:=]|is)\s*)(\"[^\"]+\"|'[^']+'|\S+)")),
    ("connection_string_password", re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@\s]+)(@)")),
]

CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")),
    ("us_ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")),
]
# PII that makes content credentials-adjacent / financial -> Restricted
RESTRICTED_PII = {"credit_card", "us_ssn", "iban"}


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def redact_secrets(text: str) -> tuple[str, dict]:
    """Replace secrets with [REDACTED:type]. Returns (text, counts)."""
    counts: dict[str, int] = {}

    def bump(kind):
        counts[kind] = counts.get(kind, 0) + 1

    for kind, pat in SECRET_PATTERNS:
        if kind == "password_assignment":
            def rep(m, kind=kind):
                bump(kind)
                return f"{m.group(1)}{m.group(2)}[REDACTED:secret]"
        elif kind == "connection_string_password":
            def rep(m, kind=kind):
                bump(kind)
                return f"{m.group(1)}[REDACTED:secret]{m.group(3)}"
        else:
            def rep(m, kind=kind):
                bump(kind)
                return f"[REDACTED:{kind}]"
        text = pat.sub(rep, text)

    def card_rep(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            bump("credit_card")
            return "[REDACTED:credit_card]"
        return m.group(0)

    text = CARD.sub(card_rep, text)
    return text, counts


def pii_tags(text: str) -> list[str]:
    tags = {kind for kind, pat in PII_PATTERNS if pat.search(text)}
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            tags.add("credit_card")
    return sorted(tags)


def sensitivity_for(text: str, floor: int) -> tuple[int, list[str]]:
    """Strictest of the collection default and what the content implies. Never lowers."""
    tags = pii_tags(text)
    level = floor
    if set(tags) & RESTRICTED_PII:
        level = max(level, 3)
    elif tags:
        level = max(level, 2 if floor >= 1 else 1)
    return level, tags
