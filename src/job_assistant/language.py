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
ALLOWED_REQUIRED_LANGUAGES = frozenset({"English", "Russian", "Spanish"})
LANGUAGE_NAME_PATTERNS = {
    "English": r"english|английск[\wё]*",
    "Russian": r"russian|русск[\wё]*",
    "Spanish": r"spanish|español|испанск[\wё]*",
    "Afrikaans": r"afrikaans|африкаанс",
    "Albanian": r"albanian|албанск[\wё]*",
    "Arabic": r"arabic|арабск[\wё]*",
    "Armenian": r"armenian|армянск[\wё]*",
    "Azerbaijani": r"azerbaijani|азербайджанск[\wё]*",
    "Belarusian": r"belarusian|белорусск[\wё]*",
    "Bengali": r"bengali|bangla|бенгальск[\wё]*",
    "Bosnian": r"bosnian|боснийск[\wё]*",
    "Bulgarian": r"bulgarian|болгарск[\wё]*",
    "Catalan": r"catalan|каталонск[\wё]*",
    "Chinese": r"chinese|mandarin|cantonese|китайск[\wё]*|mandarin chinese",
    "Croatian": r"croatian|хорватск[\wё]*",
    "Czech": r"czech|чешск[\wё]*",
    "Danish": r"danish|датск[\wё]*",
    "Dutch": r"dutch|nederlands|нидерландск[\wё]*|голландск[\wё]*",
    "Estonian": r"estonian|эстонск[\wё]*",
    "Finnish": r"finnish|финск[\wё]*",
    "French": r"french|français|французск[\wё]*",
    "Georgian": r"georgian|грузинск[\wё]*",
    "German": r"german|deutsch(?:kenntnisse)?|немецк[\wё]*",
    "Greek": r"greek|греческ[\wё]*",
    "Hebrew": r"hebrew|иврит",
    "Hindi": r"hindi|хинди",
    "Hungarian": r"hungarian|венгерск[\wё]*",
    "Icelandic": r"icelandic|исландск[\wё]*",
    "Indonesian": r"indonesian|индонезийск[\wё]*",
    "Italian": r"italian|italiano|итальянск[\wё]*",
    "Japanese": r"japanese|японск[\wё]*",
    "Kazakh": r"kazakh|казахск[\wё]*",
    "Korean": r"korean|корейск[\wё]*",
    "Latvian": r"latvian|латышск[\wё]*",
    "Lithuanian": r"lithuanian|литовск[\wё]*",
    "Macedonian": r"macedonian|македонск[\wё]*",
    "Malay": r"malay|малайск[\wё]*",
    "Marathi": r"marathi|маратхи",
    "Norwegian": r"norwegian|норвежск[\wё]*",
    "Persian": r"persian|farsi|персидск[\wё]*|фарси",
    "Punjabi": r"punjabi|пенджабск[\wё]*",
    "Polish": r"polish|polski|польск[\wё]*",
    "Portuguese": r"portuguese|português|португальск[\wё]*",
    "Romanian": r"romanian|румынск[\wё]*",
    "Serbian": r"serbian|сербск[\wё]*",
    "Slovak": r"slovak|словацк[\wё]*",
    "Slovenian": r"slovenian|словенск[\wё]*",
    "Swedish": r"swedish|шведск[\wё]*",
    "Tagalog": r"tagalog|filipino|тагальск[\wё]*",
    "Telugu": r"telugu|телугу",
    "Thai": r"thai|тайск[\wё]*",
    "Turkish": r"turkish|türkçe|турецк[\wё]*",
    "Ukrainian": r"ukrainian|украинск[\wё]*",
    "Urdu": r"urdu|урду",
    "Uzbek": r"uzbek|узбекск[\wё]*",
    "Vietnamese": r"vietnamese|вьетнамск[\wё]*",
}
_GENERIC_LANGUAGE_NAME_RE = re.compile(
    r"(?<![\w-])(?P<name>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'-]{1,30})\s+languages?\b",
    re.IGNORECASE,
)
_GENERIC_LANGUAGE_NON_NAMES = {
    "additional",
    "business",
    "computer",
    "foreign",
    "local",
    "markup",
    "modeling",
    "modelling",
    "native",
    "other",
    "programming",
    "query",
    "scripting",
    "second",
    "spoken",
    "technical",
    "written",
}
_REQUIRED_LANGUAGE_PREFIXES = (
    re.compile(
        r"(?:\b(?:command|knowledge|proficiency|fluency)\s+(?:of|in)|"
        r"\b(?:fluent|proficient|native)\s+(?:in\s+)?|"
        r"\bflie(?:ß|ss)end\s+|"
        r"\b(?:must|required\s+to|need\s+to)\s+(?:speak|read|write|know|use|communicate\s+in)|"
        r"\b(?:required|mandatory|essential)\s+(?:command|knowledge|proficiency|fluency)\s+(?:of|in))"
        r"\s+(?:the\s+)?[^.;:\n]{0,80}$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\bсвободн[\wё]*\s+владен[\wё]*|\bзнани[\wё]*|\bвладени[\wё]*)"
        r"\s+[^.;:\n]{0,60}$",
        re.IGNORECASE,
    ),
    re.compile(r"\b[abc][12]\s+(?:level\s+)?$", re.IGNORECASE),
)
_REQUIRED_LANGUAGE_SUFFIXES = (
    re.compile(
        r"^\s*(?:language|language\s+skills|skills|\u044fзык[\wё]*|\u044fзыком)?\s*"
        r"(?:is|are|\u044fвляется)?\s*"
        r"(?:required|mandatory|essential|a\s+must|erforderlich|\u043eбязател[\wё]*|\u0442ребуется)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:language\s+)?(?:proficiency|fluency|knowledge|command)?\s*"
        r"(?:at\s+)?(?:level\s+)?[abc][12]\b",
        re.IGNORECASE,
    ),
)


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


def explicit_unsupported_language_requirements(text: str) -> list[str]:
    """Return explicitly required languages outside the Russian/English/Spanish allowlist."""
    required: list[str] = []
    for language, language_pattern in LANGUAGE_NAME_PATTERNS.items():
        if language in ALLOWED_REQUIRED_LANGUAGES:
            continue
        for match in re.finditer(rf"(?<![\w-])(?:{language_pattern})(?![\w-])", text, re.IGNORECASE):
            if _is_explicit_language_requirement(text, match.start(), match.end()):
                required.append(language)
                break
    for match in _GENERIC_LANGUAGE_NAME_RE.finditer(text):
        candidate = match.group("name")
        if candidate.casefold() in _GENERIC_LANGUAGE_NON_NAMES:
            continue
        canonical = _canonical_language_name(candidate)
        if canonical in ALLOWED_REQUIRED_LANGUAGES or canonical in required:
            continue
        if _is_explicit_language_requirement(text, match.start(), match.end()):
            required.append(canonical)
    return required


def _canonical_language_name(name: str) -> str:
    for language, language_pattern in LANGUAGE_NAME_PATTERNS.items():
        if re.fullmatch(rf"(?:{language_pattern})", name, re.IGNORECASE):
            return language
    return name.capitalize()


def _is_explicit_language_requirement(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 120) : start]
    after = text[end : end + 80]
    return any(pattern.search(before) for pattern in _REQUIRED_LANGUAGE_PREFIXES) or any(
        pattern.search(after) for pattern in _REQUIRED_LANGUAGE_SUFFIXES
    )


def has_explicit_german_requirement(text: str) -> bool:
    """Backward-compatible predicate for callers that only need the German case."""
    return "German" in explicit_unsupported_language_requirements(text)
