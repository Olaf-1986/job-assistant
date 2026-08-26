from __future__ import annotations

import re

CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
GERMAN_RE = re.compile(
    r"\b(und|oder|mit|für|nicht|deutsch|deutschkenntnisse|kenntnisse|erfahrung|aufgaben|anforderungen|"
    r"bewerbung|kunden|unternehmen|entwicklung|m/w/d)\b|[äöüß]",
    re.IGNORECASE,
)
LATIN_RE = re.compile(r"[A-Za-z]")
LINKEDIN_ENGLISH_MARKERS = (
    "requirements",
    "responsibilities",
    "experience",
    "skills",
    "required",
    "analysis",
    "role",
    "position",
    "with",
    "and",
    "the",
    "your",
)
LINKEDIN_OTHER_LANGUAGE_MARKERS = {
    "es": ("requisitos", "experiencia", "responsabilidades", "habilidades", "trabajo", "empresa"),
    "fr": ("exigences", "expérience", "responsabilités", "compétences", "travail", "entreprise"),
    "it": ("requisiti", "esperienza", "responsabilità", "competenze", "lavoro", "azienda"),
    "pt": ("requisitos", "experiência", "responsabilidades", "competências", "trabalho", "empresa"),
    "nl": ("vereisten", "ervaring", "verantwoordelijkheden", "vaardigheden", "werk", "bedrijf"),
    "pl": ("wymagania", "doświadczenie", "obowiązki", "umiejętności", "praca", "firma"),
}


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


def detect_linkedin_content_language(text: str) -> str:
    """Return a conservative language label for LinkedIn job-content policy."""
    detected = detect_language(text)
    if detected in {"ru", "de"}:
        return detected

    lowered = text.casefold()
    english_score = _language_marker_score(lowered, LINKEDIN_ENGLISH_MARKERS)
    other_scores = {
        language: _language_marker_score(lowered, markers)
        for language, markers in LINKEDIN_OTHER_LANGUAGE_MARKERS.items()
    }
    other_language, other_score = max(other_scores.items(), key=lambda item: item[1], default=("unknown", 0))
    if other_score >= 2 and other_score > english_score:
        return other_language
    if english_score >= 2 or (english_score >= 1 and "requirements" in lowered):
        return "en"
    return "unknown"


def _language_marker_score(text: str, markers: tuple[str, ...]) -> int:
    return sum(bool(re.search(rf"(?<![\w-]){re.escape(marker.casefold())}(?![\w-])", text)) for marker in markers)


def has_explicit_german_requirement(text: str) -> bool:
    lowered = text.lower()
    patterns = [
        r"\bdeutsch(?:kenntnisse)?\b",
        r"\bgerman\s+(?:required|language|required language)\b",
        r"\bc1\s+deutsch\b",
        r"\bflie(?:ß|ss)end\s+deutsch\b",
    ]
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)
