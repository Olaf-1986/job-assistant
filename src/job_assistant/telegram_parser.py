from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .config import Preferences
from .role_relevance import title_prefilter_matches
from .telegram_client import TelegramMessage
from .utils import canonical_url, normalize_space

TITLE_LABEL_RE = re.compile(
    r"^\s*(?:[#*\-–—•\s]*)?(?:vacancy|job\s*title|position|role|вакансия|позиция|роль)\s*[:—-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
NUMBERED_TITLE_RE = re.compile(r"^\s*\d{1,2}[.)]\s+(.{3,160}?)\s*$")
COMPANY_RE = re.compile(r"^\s*(?:company|employer|компания|работодатель)\s*[:—-]\s*(.+?)\s*$", re.IGNORECASE)
LOCATION_RE = re.compile(
    r"^\s*(?:location|work\s*format|format|office|локация|местоположение|формат(?:\s+работы)?|офис)\s*[:—-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.IGNORECASE)
HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
VACANCY_MARKER_RE = re.compile(
    r"(?:#vacanc|#вакан|\bvacanc(?:y|ies)\b|\bjob opening\b|\bhiring\b|\bваканси[яи]\b)",
    re.IGNORECASE,
)
RESPONSIBILITY_RE = re.compile(r"\b(?:responsibilit|duties|tasks|обязанност|задач[аи])", re.IGNORECASE)
REQUIREMENTS_RE = re.compile(r"\b(?:requirements?|qualifications?|experience|требовани|опыт)\b", re.IGNORECASE)
SALARY_RE = re.compile(r"(?:\b(?:salary|compensation|зарплат[аы]|зп)\b|[$€₽₾]|\b(?:USD|EUR|RUB|GEL)\b)", re.IGNORECASE)
APPLICATION_RE = re.compile(
    r"\b(?:apply|application|send\s+(?:your\s+)?cv|contact|отклик|отправ(?:ить|ляйте)|резюме\s+(?:на|в))\b",
    re.IGNORECASE,
)
MULTIPLE_RE = re.compile(
    r"\b(?:multiple vacancies|several vacancies|vacancies|open roles|positions|несколько вакансий|вакансии|"
    r"открытые позиции)\s*[:—-]",
    re.IGNORECASE,
)
JOB_TITLE_WORD_RE = re.compile(
    r"\b(?:analyst|administrator|consultant|developer|engineer|manager|designer|recruiter|specialist|"
    r"аналитик|администратор|консультант|разработчик|инженер|менеджер|дизайнер|рекрутер|специалист)\b",
    re.IGNORECASE,
)

CANDIDATE_PATTERNS = (
    r"#резюме\b",
    r"#(?:cv|candidate|кандидат)\b",
    r"\bищу\s+(?:работу|проект|вакансию)\b",
    r"\bищет\s+(?:работу|проект)\b",
    r"\bopen\s+to\s+work\b",
    r"\bcandidate\s+profile\b",
    r"\bрезюме\s+кандидата\b",
    r"\bпрофиль\s+кандидата\b",
    r"(?:^|\n)\s*(?:candidate|кандидат)\s*[:—-]",
    r"(?:^|\n)\s*(?:desired position|желаемая должность|salary expectations?|зарплатные ожидания)\s*[:—-]",
)
NON_JOB_PATTERNS = (
    r"\b(?:webinar|вебинар|course|курс|workshop|воркшоп|conference|конференци|митап|meetup)\b",
    r"\b(?:career advice|карьерн(?:ый|ые) совет|новости рынка|дайджест новостей)\b",
    r"\b(?:register now|регистрация на мероприятие)\b",
)


@dataclass(frozen=True)
class ParsedTelegramVacancy:
    record: dict[str, object]
    target_role_match: bool


@dataclass
class TelegramParseResult:
    message: TelegramMessage
    vacancy_like: bool
    candidates: list[ParsedTelegramVacancy] = field(default_factory=list)
    rejection_reason: str | None = None
    failure_reason: str | None = None

    @property
    def target_records(self) -> list[dict[str, object]]:
        return [candidate.record for candidate in self.candidates if candidate.target_role_match]


def parse_telegram_message(message: TelegramMessage, preferences: Preferences) -> TelegramParseResult:
    text = _clean_text(message.text)
    if not text:
        return TelegramParseResult(message=message, vacancy_like=False, rejection_reason="empty_message")
    lowered = text.casefold()
    if _matches_any(lowered, CANDIDATE_PATTERNS):
        return TelegramParseResult(message=message, vacancy_like=False, rejection_reason="candidate_or_resume")
    opening = "\n".join(text.splitlines()[:5]).casefold()
    if _matches_any(opening, NON_JOB_PATTERNS) or (
        _matches_any(lowered, NON_JOB_PATTERNS) and not VACANCY_MARKER_RE.search(text)
    ):
        return TelegramParseResult(message=message, vacancy_like=False, rejection_reason="news_advertising_or_event")

    signals = _message_signals(text)
    vacancy_like = bool(signals["vacancy_marker"] or (signals["title"] and sum(signals.values()) >= 3))
    if not vacancy_like:
        return TelegramParseResult(message=message, vacancy_like=False, rejection_reason="not_vacancy_like")

    sections, ambiguous = _split_sections(text)
    if ambiguous:
        return TelegramParseResult(
            message=message,
            vacancy_like=True,
            failure_reason="multiple vacancies could not be split reliably",
        )

    parsed: list[ParsedTelegramVacancy] = []
    failures: list[str] = []
    for vacancy_index, (title_hint, section_text) in enumerate(sections):
        candidate, failure = _parse_section(
            message,
            preferences,
            section_text,
            vacancy_index=vacancy_index,
            title_hint=title_hint,
        )
        if candidate is not None:
            parsed.append(candidate)
        elif failure:
            failures.append(f"vacancy_index={vacancy_index}: {failure}")
    return TelegramParseResult(
        message=message,
        vacancy_like=True,
        candidates=parsed,
        failure_reason="; ".join(failures) or None,
    )


def _parse_section(
    message: TelegramMessage,
    preferences: Preferences,
    section_text: str,
    vacancy_index: int,
    title_hint: str | None,
) -> tuple[ParsedTelegramVacancy | None, str | None]:
    title = _extract_title(section_text, title_hint)
    if not title:
        return None, "missing identifiable vacancy title"
    signals = _message_signals(section_text)
    substantive = len(re.sub(r"\s+", "", section_text)) >= 80 and (
        signals["responsibilities"]
        or signals["requirements"]
        or sum(signals[key] for key in ("company", "location", "salary", "application")) >= 2
    )
    if not substantive:
        return None, "message lacks a substantive vacancy description"

    company = _extract_labeled(section_text, COMPANY_RE)
    location = _extract_location(section_text)
    requirements = _extract_requirements(section_text)
    urls = _extract_urls(section_text, message.entity_urls)
    apply_url = _choose_apply_url(section_text, urls, message.message_url)
    salary_min, salary_max, salary_currency = _extract_salary(section_text)
    work_mode = _extract_work_mode(section_text)
    remote_eligibility = _remote_eligibility(section_text, work_mode, location)
    role_keywords = [*preferences.role_relevance.relevant_title_keywords, *preferences.queries.low_priority]
    target_match = title_prefilter_matches(title, role_keywords)
    record: dict[str, object] = {
        "__source": "telegram",
        "source": "telegram",
        "channel_username": message.channel_username,
        "channel_id": message.channel_id,
        "message_id": message.message_id,
        "message_url": message.message_url,
        "vacancy_index": vacancy_index,
        "published_at": message.published_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "forwarded_from": message.forwarded_from,
        "raw_text": section_text,
        "apply_url": apply_url,
        "title": title,
        "company": company,
        "location": location,
        "requirements_text": requirements,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "work_mode": work_mode,
        "international_remote_eligibility": remote_eligibility,
        "requires_manual_location_review": work_mode is None,
    }
    return ParsedTelegramVacancy(record=record, target_role_match=target_match), None


def _split_sections(text: str) -> tuple[list[tuple[str | None, str]], bool]:
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        title_match = TITLE_LABEL_RE.match(line)
        if title_match:
            headings.append((index, _clean_title(title_match.group(1))))
            continue
        numbered = NUMBERED_TITLE_RE.match(line)
        if numbered and JOB_TITLE_WORD_RE.search(numbered.group(1)):
            headings.append((index, _clean_title(numbered.group(1))))
    if len(headings) >= 2:
        preamble = "\n".join(lines[: headings[0][0]]).strip()
        sections: list[tuple[str | None, str]] = []
        for position, (start, title) in enumerate(headings):
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            body = "\n".join(lines[start:end]).strip()
            combined = "\n".join(part for part in (preamble, body) if part)
            sections.append((title, combined))
        return sections, False
    if MULTIPLE_RE.search(text):
        return [], True
    title_hint = headings[0][1] if headings else None
    return [(title_hint, text)], False


def _extract_title(text: str, hint: str | None) -> str | None:
    if hint:
        return _clean_title(hint)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        match = TITLE_LABEL_RE.match(line)
        if match:
            return _clean_title(match.group(1))
    for line in lines[:5]:
        cleaned = _clean_title(line)
        if JOB_TITLE_WORD_RE.search(cleaned) and not COMPANY_RE.match(line) and not LOCATION_RE.match(line):
            return cleaned
    return None


def _clean_title(value: str) -> str:
    value = re.sub(r"^(?:#(?:vacancy|вакансия)\s*)+", "", value.strip(), flags=re.IGNORECASE)
    value = value.strip(" \t#*•📌💼🔥🚀–—-")
    value = re.split(r"\s+[|/]\s+", value, maxsplit=1)[0]
    return normalize_space(value)[:200]


def _extract_labeled(text: str, pattern: re.Pattern[str]) -> str | None:
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            value = normalize_space(match.group(1)).strip(" #*•")
            return value[:200] if value else None
    return None


def _extract_location(text: str) -> str | None:
    labeled = _extract_labeled(text, LOCATION_RE)
    if labeled:
        return labeled
    for line in text.splitlines():
        if re.search(r"\b(?:remote|hybrid|onsite|on-site|Tbilisi|Тбилиси|удал[её]нно|гибрид)\b", line, re.IGNORECASE):
            return normalize_space(line)[:240]
    return None


def _extract_requirements(text: str) -> str | None:
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if REQUIREMENTS_RE.search(line)), None)
    if start is None:
        return None
    selected: list[str] = []
    for line in lines[start:]:
        if selected and re.match(
            r"^\s*(?:responsibilities|duties|about|location|salary|apply|обязанности|о компании|локация|"
            r"зарплата|отклик)\s*[:—-]?\s*$",
            line,
            re.IGNORECASE,
        ):
            break
        selected.append(line)
    result = "\n".join(selected).strip()
    return result or None


def _extract_urls(text: str, entity_urls: Iterable[str]) -> list[str]:
    urls = [match.group(0).rstrip(".,);:!?}") for match in URL_RE.finditer(text)]
    for line in text.splitlines():
        if not APPLICATION_RE.search(line):
            continue
        urls.extend(f"https://t.me/{match.group(1)}" for match in HANDLE_RE.finditer(line))
    urls.extend(entity_urls)
    return list(dict.fromkeys(url for url in urls if url.startswith(("http://", "https://"))))


def _choose_apply_url(text: str, urls: list[str], message_url: str | None) -> str | None:
    canonical_message = canonical_url(message_url)
    application_urls = [
        match.group(0).rstrip(".,);:!?}")
        for line in text.splitlines()
        if APPLICATION_RE.search(line)
        for match in URL_RE.finditer(line)
    ]
    for url in dict.fromkeys([*application_urls, *urls]):
        canonical = canonical_url(url)
        if canonical and canonical != canonical_message:
            return canonical
    return None


def _extract_salary(text: str) -> tuple[float | None, float | None, str | None]:
    salary_line = next((line for line in text.splitlines() if SALARY_RE.search(line)), None)
    if not salary_line:
        return None, None, None
    currency_match = re.search(r"\b(USD|EUR|RUB|GEL)\b|([$€₽₾])", salary_line, re.IGNORECASE)
    currency_map = {"$": "USD", "€": "EUR", "₽": "RUB", "₾": "GEL"}
    currency_token = currency_match.group(1) or currency_match.group(2) if currency_match else None
    currency = currency_map.get(currency_token, currency_token.upper() if currency_token else None)
    numbers = []
    for raw_number in re.findall(r"\d[\d\s.,]*\d|\d", salary_line):
        compact = re.sub(r"[\s,]", "", raw_number)
        try:
            numbers.append(float(compact))
        except ValueError:
            continue
    if not numbers:
        return None, None, currency
    if len(numbers) == 1:
        return numbers[0], None, currency
    return min(numbers[:2]), max(numbers[:2]), currency


def _extract_work_mode(text: str) -> str | None:
    labeled_context = "\n".join(match.group(0) for line in text.splitlines() if (match := LOCATION_RE.match(line)))
    return _work_mode_from_text(labeled_context) or _work_mode_from_text(text)


def _work_mode_from_text(text: str) -> str | None:
    remote = re.search(
        r"\b(?:remote|remotely|work from home|удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\b",
        text,
        re.IGNORECASE,
    )
    remote_option = re.search(
        r"\b(?:remote|удал[её]нно)\s*(?:or|или|/)\s*(?:hybrid|onsite|on-site|гибрид|офис)\b|"
        r"\b(?:hybrid|onsite|on-site|гибрид|офис)\s*(?:or|или|/)\s*(?:remote|удал[её]нно)\b",
        text,
        re.IGNORECASE,
    )
    if remote and remote_option:
        return "remote"
    if re.search(r"\b(?:hybrid|гибрид)\b", text, re.IGNORECASE):
        return "hybrid"
    if re.search(r"\b(?:onsite|on-site|in[ -]office|office-based|офис)\b", text, re.IGNORECASE):
        return "onsite"
    if remote:
        return "remote"
    return None


def _remote_eligibility(text: str, work_mode: str | None, location: str | None) -> str | None:
    if work_mode != "remote":
        return None
    location_text = location or ""
    if re.search(
        r"\b(?:Tbilisi|Georgia|Тбилиси|Грузи[ия])\b",
        location_text,
        re.IGNORECASE,
    ):
        return "explicit_allowed_location"
    if re.search(
        r"\b(?:EU|EEA|EMEA|Europe|UK|USA?|CIS|Cyprus|Spain|Germany|Serbia|Armenia|Belarus|Russia|"
        r"Европа|СНГ|Кипр|Испания|Германия|Сербия|Армения|Беларусь|Россия|РФ|РБ)\b",
        location_text,
        re.IGNORECASE,
    ):
        return "explicit_restricted"
    if re.search(
        r"\b(?:only\s+(?:from|in)\s+Georgia|Georgia\s+only|только\s+(?:из|в)\s+Грузи[ия])\b",
        text,
        re.IGNORECASE,
    ):
        return "explicit_allowed_location"
    restricted_patterns = (
        # "only from Germany" / "только из России"
        r"\b(?:only|только)\s+(?:from|in|for|для|из|в)\s+[A-ZА-ЯЁ][\w .-]{1,40}",
        # Common compact Telegram wording: "EU only", "Europe only".
        r"\b(?:EU|EEA|EMEA|Europe|UK|USA?|CIS|Европа|СНГ|РФ|Россия)\s+only\b",
        r"\b(?:только)\s+(?:EU|EEA|EMEA|Europe|UK|USA?|CIS|Европа|СНГ|РФ|Россия)\b",
        r"\bremote(?:ly)?\s*(?:[-—,:()]|\s)*(?:within|in|from)\s+(?:the\s+)?"
        r"(?:EU|EEA|EMEA|Europe|UK|USA?|CIS)\b",
        r"\bremote(?:ly)?\s*[-—,:()]\s*(?:the\s+)?(?:EU|EEA|EMEA|Europe|UK|USA?|CIS)\b",
        r"\b(?:EU|EEA|EMEA|Europe|UK|USA?|CIS)\s*[-—,:()]?\s+remote\b",
        r"\b(?:удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\s*(?:по|из|в|для)?\s*"
        r"(?:РФ|РБ|Росси[ия]|Беларус[ьи])\b",
        r"\b(?:РФ|РБ|Росси[ия]|Беларус[ьи])(?:\s*[-/]\s*(?:РФ|РБ|Росси[ия]|Беларус[ьи]))?"
        r"\s*\(?\s*(?:удал[её]нн(?:о|ый|ая|ое|ые|ую)|удал[её]нка)\b",
        # Residency/location requirements without the word "only".
        r"\b(?:must|required\s+to|should)\s+be\s+(?:based|located|resident)\s+in\s+[A-Z][\w .-]{1,40}",
        r"\b(?:нужно|необходимо|долж(?:ен|на|ны))\s+(?:находиться|проживать|быть)\s+в\s+[А-ЯЁ][\w .-]{1,40}",
    )
    is_restricted = any(re.search(pattern, text, re.IGNORECASE) for pattern in restricted_patterns)
    if is_restricted:
        return "explicit_restricted"
    if re.search(
        r"\b(?:worldwide|anywhere|any country|all countries|по всему миру|из любой страны)\b|"
        r"\b(?:remote(?:ly)?|work|hire|hiring)\s+globally\b|\bglobal\s+remote\b",
        text,
        re.IGNORECASE,
    ):
        return "explicit_global"
    return None


def _message_signals(text: str) -> dict[str, bool]:
    first_lines = text.splitlines()[:5]
    location_signal = LOCATION_RE.search(text) or re.search(
        r"\b(?:remote|hybrid|onsite|Tbilisi|Тбилиси)\b", text, re.IGNORECASE
    )
    return {
        "vacancy_marker": bool(VACANCY_MARKER_RE.search(text)),
        "title": bool(TITLE_LABEL_RE.search(text) or any(JOB_TITLE_WORD_RE.search(line) for line in first_lines)),
        "company": bool(any(COMPANY_RE.match(line) for line in text.splitlines())),
        "responsibilities": bool(RESPONSIBILITY_RE.search(text)),
        "requirements": bool(REQUIREMENTS_RE.search(text)),
        "location": bool(location_signal),
        "salary": bool(SALARY_RE.search(text)),
        "application": bool(APPLICATION_RE.search(text) or URL_RE.search(text)),
    }


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _clean_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").splitlines()]
    return "\n".join(lines).strip()
