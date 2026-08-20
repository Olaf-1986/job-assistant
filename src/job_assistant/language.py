from __future__ import annotations

import re

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
GERMAN_RE = re.compile(
    r"\b(und|oder|mit|für|nicht|deutsch|deutschkenntnisse|kenntnisse|erfahrung|aufgaben|anforderungen|"
    r"bewerbung|kunden|unternehmen|entwicklung|m/w/d)\b|[äöüß]",
    re.IGNORECASE,
)
LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    """Replaceable deterministic language heuristic for en/ru/de vacancies."""
    cyrillic_count = len(CYRILLIC_RE.findall(text))
    latin_count = len(LATIN_RE.findall(text))
    total_letters = cyrillic_count + latin_count
    if cyrillic_count >= 20 and total_letters and cyrillic_count / total_letters >= 0.2:
        return "ru"
    if GERMAN_RE.search(text) and cyrillic_count == 0:
        return "de"
    return "en"


def has_explicit_german_requirement(text: str) -> bool:
    lowered = text.lower()
    patterns = [
        r"\bdeutsch(?:kenntnisse)?\b",
        r"\bgerman\s+(?:required|language|required language)\b",
        r"\bc1\s+deutsch\b",
        r"\bflie(?:ß|ss)end\s+deutsch\b",
    ]
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)
