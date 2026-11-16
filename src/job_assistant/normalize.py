from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from .config import Preferences
from .language import detect_language, detect_linkedin_content_language
from .linkedin_content import _description_from_main_text, linkedin_description_for_language
from .linkedin_policy import LINKEDIN_CLOSED_BLOCKER_REASON, has_linkedin_no_longer_accepting_marker
from .location import classify_remote_eligibility, detect_allowed_city, detect_work_mode
from .models import NormalizedVacancy, RawRecord
from .utils import canonical_url, normalize_space, slugify_text, utc_now

LOGGER = logging.getLogger(__name__)
HEADHUNTER_TAG_FIELDS = ("key_skills", "skills", "professional_roles", "specializations", "tags")


# Recognize only country names, not arbitrary locations such as a city or an office.
_HIRING_COUNTRIES = {
    "Georgia": r"Georgia|Грузи(?:я|и|ю)",
    "Russia": r"Russia|Russian\s+Federation|Росси(?:я|и|ю)|Российской\s+Федерации|РФ",
    "Armenia": r"Armenia|Армени(?:я|и|ю)",
    "Belarus": r"Belarus|Беларус(?:ь|и)",
    "Germany": r"Germany|Германи(?:я|и|ю)",
    "France": r"France|Франци(?:я|и|ю)",
    "Spain": r"Spain|Испани(?:я|и|ю)",
    "Italy": r"Italy|Итали(?:я|и|ю)",
    "Netherlands": r"Netherlands|Нидерланд(?:ы|ах)",
    "Portugal": r"Portugal|Португали(?:я|и|ю)",
    "Poland": r"Poland|Польш(?:а|е|у)",
    "Cyprus": r"Cyprus|Кипр(?:а|е)?",
    "Serbia": r"Serbia|Серби(?:я|и|ю)",
    "Turkey": r"Turkey|Türkiye|Турци(?:я|и|ю)",
    "United Kingdom": r"United\s+Kingdom|UK|Великобритани(?:я|и|ю)",
    "United States": r"United\s+States|USA|США",
    "Canada": r"Canada|Канад(?:а|е|у)",
    "Australia": r"Australia|Австрали(?:я|и|ю)",
}
_HIRING_COUNTRY_NAMES = "|".join(f"(?:{names})" for names in _HIRING_COUNTRIES.values())
_EXPLICIT_HIRING_COUNTRY = re.compile(
    r"\b(?:"
    r"(?:we|the\s+employer|the\s+company)\s+(?:will\s+)?(?:hire|employ)\s+you\s+in|"
    r"(?:you|the\s+successful\s+candidate)\s+(?:will\s+be|are)\s+(?:hired|employed)\s+in|"
    r"(?:hiring|employment)\s+(?:will\s+be|is)\s+in|"
    r"(?:оформим|найм[её]м)\s+вас\s+в|вас\s+(?:оформят|наймут)\s+в|"
    r"вы\s+будете\s+(?:оформлены|трудоустроены)\s+в|"
    r"(?:оформление|трудоустройство)\s+(?:(?:будет|происходит)\s+)?в"
    rf")\s+(?:the\s+)?(?P<country>{_HIRING_COUNTRY_NAMES})\b"
    r"(?!\s+(?:or|and|или|либо|и|state)\b)",
    re.IGNORECASE,
)


def _explicit_hiring_country(text: str) -> str | None:
    """Extract only unambiguous, explicitly stated countries of employment."""
    countries = set()
    for match in _EXPLICIT_HIRING_COUNTRY.finditer(text):
        name = match.group("country")
        for country, pattern in _HIRING_COUNTRIES.items():
            if re.fullmatch(pattern, name, re.IGNORECASE):
                countries.add(country)
                break
    return next(iter(countries)) if len(countries) == 1 else None


def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
        li.append("\n")
    for tag_name in ["p", "div", "section", "h1", "h2", "h3", "h4"]:
        for tag in soup.find_all(tag_name):
            tag.insert_before("\n")
            tag.append("\n")
    lines = [normalize_space(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).replace("-\n", "- ") or None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = float(value) / 1000 if abs(float(value)) >= 10_000_000_000 else float(value)
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                result.append(str(item.get("name") or item.get("title") or item.get("slug") or item))
            elif item is not None:
                result.append(str(item))
        return result
    return [str(value)]


def headhunter_analysis_tags(record: RawRecord) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for field in HEADHUNTER_TAG_FIELDS:
        for value in as_list(record.get(field)):
            tag = normalize_space(value)
            key = tag.casefold()
            if not tag or key in seen:
                continue
            seen.add(key)
            tags.append(tag)
    return tags


def parse_optional_float(value: Any, field_name: str, warnings: list[str]) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        warnings.append(f"invalid numeric value for {field_name}: {value!r}")
        LOGGER.warning("Invalid numeric value for %s: %r", field_name, value)
        return None


def normalize_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    source = record.get("__source")
    if not isinstance(source, str) or not source:
        raise ValueError("Raw record is missing required __source")
    normalizer = _SOURCE_NORMALIZERS.get(source)
    if normalizer is None:
        raise ValueError(f"Raw record has unknown __source: {source!r}")
    vacancy = normalizer(record, query, preferences)
    if vacancy is not None:
        country = record.get("hiring_country")
        if isinstance(country, str) and country.strip():
            vacancy.hiring_country = country.strip()
        else:
            country_label = re.search(
                r"(?:hiring country|country of employment|employment country|страна найма|страна оформления)"
                r"\s*:\s*([^\n,.;]+)",
                "\n".join([vacancy.description_text or "", vacancy.requirements_text or ""]),
                re.IGNORECASE,
            )
            if country_label:
                vacancy.hiring_country = country_label[1].strip()
            else:
                vacancy.hiring_country = _explicit_hiring_country(
                    "\n".join([vacancy.description_text or "", vacancy.requirements_text or ""])
                )
    if vacancy is not None and source in {"manual", "linkedin", "telegram", "rvc"}:
        # Structured imports may supply salary data; free-text salary parsing is source-specific.
        for field in ("salary_min", "salary_max"):
            if field in record:
                setattr(vacancy, field, parse_optional_float(record[field], field, vacancy.warnings))
        for field in ("salary_currency", "salary_period"):
            if isinstance(record.get(field), str):
                setattr(vacancy, field, record[field])
        gross = record.get("salary_gross")
        basis = record.get("salary_basis")
        if isinstance(gross, bool):
            vacancy.salary_gross = gross
        elif isinstance(basis, str) and basis.casefold() in {"gross", "net"}:
            vacancy.salary_gross = basis.casefold() == "gross"
    return vacancy


def _base_vacancy(
    source: str,
    source_id: str | None,
    query: str,
    title: str,
    company: str | None,
    description_html: str | None,
    description_text: str | None,
    excerpt: str | None,
    location: str | None,
    url: str | None,
    preferences: Preferences,
    **extra: Any,
) -> NormalizedVacancy:
    warnings: list[str] = list(extra.pop("warnings", []))
    initial_blocker_reasons = sorted(set(extra.pop("initial_blocker_reasons", [])))
    text_for_detection = "\n".join([title, excerpt or "", description_text or "", location or ""])
    # Detect the description itself: titles, locations and declared metadata cannot make it English.
    extra.pop("detected_language", None)
    language = detect_language(
        linkedin_description_for_language(description_text or "") if source == "linkedin" else description_text or ""
    )
    if language not in preferences.languages.accepted:
        warnings.append(f"unaccepted detected language: {language}")
    canonical = canonical_url(url)
    fetched_at = utc_now()
    location_restrictions = [normalize_space(location)] if location else []
    location_text = "\n".join([*location_restrictions, text_for_detection])
    completeness = extra.pop(
        "description_completeness", "complete" if description_text and len(description_text) >= 200 else "incomplete"
    )
    if completeness == "incomplete":
        warnings.append("incomplete description requires manual review")
    configured_work_mode = extra.pop("work_mode", None)
    work_mode = configured_work_mode
    detected_city = None
    if not initial_blocker_reasons:
        work_mode = configured_work_mode or detect_work_mode(location_text, preferences)
        detected_city = detect_allowed_city(location_text, preferences)
    return NormalizedVacancy(
        source=source,
        sources=[source],
        source_id=source_id,
        source_ids={source: [source_id]} if source_id else {},
        source_url=canonical,
        source_urls=[canonical] if canonical else [],
        apply_url=extra.pop("apply_url", canonical),
        source_queries=[query],
        source_company_identifier=extra.pop("source_company_identifier", None),
        source_metadata=extra.pop("source_metadata", {}),
        title=normalize_space(title),
        normalized_title=slugify_text(title),
        company=company,
        company_slug=slugify_text(company) if company else None,
        description_html=description_html,
        description_text=description_text,
        requirements_text=extra.pop("requirements_text", None),
        excerpt=excerpt,
        location_restrictions=location_restrictions,
        timezone_restrictions=[],
        publication_date=extra.pop("publication_date", None),
        application_url=canonical,
        description_completeness=completeness,
        imported_manually=bool(extra.pop("imported_manually", False)),
        first_seen_at=fetched_at,
        last_seen_at=fetched_at,
        fetched_at=fetched_at,
        detected_language=language,
        work_mode=work_mode,
        detected_city=detected_city,
        blocker=bool(initial_blocker_reasons),
        blocker_reasons=initial_blocker_reasons,
        warnings=warnings,
        raw_application_urls=[canonical] if canonical else [],
        employment_type=extra.pop("employment_type", None),
        salary_min=extra.pop("salary_min", None),
        salary_max=extra.pop("salary_max", None),
        salary_currency=extra.pop("salary_currency", None),
        salary_period=extra.pop("salary_period", None),
        seniority=extra.pop("seniority", None),
        categories=extra.pop("categories", []),
        international_remote_eligibility=extra.pop("international_remote_eligibility", None),
        international_remote_evidence=extra.pop("international_remote_evidence", []),
        requires_manual_location_review=extra.pop("requires_manual_location_review", False),
    )


def _dict_name(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return value["name"]
    return None


def _headhunter_work_mode(record: RawRecord, schedule: str | None, location_text: str, preferences: Preferences) -> str:
    schedule_data = record.get("schedule") if isinstance(record.get("schedule"), dict) else {}
    schedule_id = schedule_data.get("id") if isinstance(schedule_data.get("id"), str) else ""
    structured = " ".join(as_list(record.get("work_format")) + as_list(record.get("work_formats")))
    lowered = structured.lower()
    schedule_lowered = " ".join([schedule_id, schedule or ""]).lower()
    if (
        "удален" in lowered
        or "удалён" in lowered
        or "remote" in lowered
        or schedule_id == "remote"
        or "удален" in schedule_lowered
        or "удалён" in schedule_lowered
    ):
        return "remote"
    if "гибрид" in lowered or "hybrid" in lowered:
        return "hybrid"
    if "офис" in lowered or "office" in lowered or "onsite" in lowered:
        return "onsite"
    return detect_work_mode(location_text, preferences)


def _normalize_headhunter_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    warnings: list[str] = []
    title = record.get("name")
    if not isinstance(title, str) or not title.strip():
        LOGGER.warning("Skipping malformed HeadHunter record without a title: %s", record)
        return None
    snippet = record.get("snippet") if isinstance(record.get("snippet"), dict) else {}
    requirement = snippet.get("requirement") if isinstance(snippet.get("requirement"), str) else None
    responsibility = snippet.get("responsibility") if isinstance(snippet.get("responsibility"), str) else None
    description_html = (
        record.get("description")
        if isinstance(record.get("description"), str)
        else "\n".join(part for part in [responsibility, requirement] if part) or None
    )
    description_text = html_to_text(description_html)
    area = _dict_name(record.get("area"))
    employer = _dict_name(record.get("employer"))
    schedule = _dict_name(record.get("schedule"))
    employment = _dict_name(record.get("employment"))
    experience = _dict_name(record.get("experience"))
    analysis_tags = headhunter_analysis_tags(record)
    text_for_detection = "\n".join([title, description_text or "", area or "", schedule or ""])
    language = detect_language(description_text or "")
    if language not in preferences.languages.accepted:
        warnings.append(f"unaccepted detected language: {language}")
    location_restrictions = [area] if area else []
    location_text = "\n".join([*location_restrictions, schedule or "", text_for_detection])
    salary = record.get("salary") if isinstance(record.get("salary"), dict) else {}
    application_url = canonical_url(
        record.get("alternate_url") if isinstance(record.get("alternate_url"), str) else None
    )
    raw_urls = [application_url] if application_url else []
    return NormalizedVacancy(
        source="headhunter",
        sources=["headhunter"],
        source_id=str(record.get("id")) if record.get("id") is not None else None,
        source_ids={"headhunter": [str(record.get("id"))]} if record.get("id") is not None else {},
        source_url=application_url,
        source_urls=[application_url] if application_url else [],
        apply_url=canonical_url(
            record.get("apply_alternate_url") if isinstance(record.get("apply_alternate_url"), str) else None
        )
        or application_url,
        source_queries=[query],
        source_metadata={
            "area": area,
            "work_format": as_list(record.get("work_format")) or as_list(record.get("work_formats")),
            "key_skills": as_list(record.get("key_skills")),
            "professional_roles": as_list(record.get("professional_roles")),
            "specializations": as_list(record.get("specializations")),
            "analysis_tags": analysis_tags,
            "response_letter_required": record.get("response_letter_required"),
        },
        title=normalize_space(title),
        normalized_title=slugify_text(title),
        company=employer,
        company_slug=slugify_text(employer) if employer else None,
        description_html=description_html,
        description_text=description_text,
        excerpt=description_text,
        employment_type=employment,
        seniority=experience,
        categories=analysis_tags,
        location_restrictions=location_restrictions,
        timezone_restrictions=[],
        salary_min=parse_optional_float(salary.get("from"), "salary.from", warnings),
        salary_max=parse_optional_float(salary.get("to"), "salary.to", warnings),
        salary_currency=salary.get("currency") if isinstance(salary.get("currency"), str) else None,
        salary_period=None,
        salary_gross=salary.get("gross") if isinstance(salary.get("gross"), bool) else None,
        publication_date=parse_datetime(record.get("published_at")),
        expiry_date=parse_datetime(record.get("archived_at")),
        application_url=application_url,
        description_completeness="complete" if description_text and len(description_text) >= 200 else "incomplete",
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        fetched_at=utc_now(),
        detected_language=language,
        work_mode=_headhunter_work_mode(record, schedule, location_text, preferences),
        detected_city=detect_allowed_city(location_text, preferences),
        warnings=warnings,
        raw_application_urls=raw_urls,
    )


def _normalize_jooble_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    snippet = record.get("snippet") if isinstance(record.get("snippet"), str) else None
    location = record.get("location") if isinstance(record.get("location"), str) else None
    salary = record.get("salary") if isinstance(record.get("salary"), str) else None
    warnings = [f"salary text: {salary}"] if salary else []
    return _base_vacancy(
        "jooble",
        str(record.get("id") or record.get("link") or "") or None,
        query,
        title,
        record.get("company") if isinstance(record.get("company"), str) else None,
        None,
        snippet,
        snippet,
        location,
        record.get("link") if isinstance(record.get("link"), str) else None,
        preferences,
        warnings=warnings,
        employment_type=record.get("type") if isinstance(record.get("type"), str) else None,
        publication_date=parse_datetime(record.get("updated")),
        description_completeness="incomplete",
    )


def _normalize_arbeitnow_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    html = record.get("description") if isinstance(record.get("description"), str) else None
    text = html_to_text(html)
    location = record.get("location") if isinstance(record.get("location"), str) else None
    return _base_vacancy(
        "arbeitnow",
        str(record.get("slug") or "") or None,
        query,
        title,
        record.get("company_name") if isinstance(record.get("company_name"), str) else None,
        html,
        text,
        text,
        location,
        record.get("url") if isinstance(record.get("url"), str) else None,
        preferences,
        source_metadata={"remote": record.get("remote"), "visa_sponsorship": record.get("visa_sponsorship")},
        publication_date=parse_datetime(record.get("created_at")),
        work_mode="remote" if record.get("remote") is True else None,
    )


def _normalize_greenhouse_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    html = record.get("content") if isinstance(record.get("content"), str) else None
    text = html_to_text(html)
    offices = as_list(record.get("offices"))
    departments = as_list(record.get("departments"))
    absolute_url = record.get("absolute_url") if isinstance(record.get("absolute_url"), str) else None
    return _base_vacancy(
        "greenhouse",
        str(record.get("id") or "") or None,
        query,
        title,
        record.get("__company") if isinstance(record.get("__company"), str) else None,
        html,
        text,
        text,
        ", ".join(offices) or None,
        absolute_url,
        preferences,
        apply_url=absolute_url,
        source_company_identifier=record.get("__board_token"),
        source_metadata={"offices": offices, "departments": departments, "board_token": record.get("__board_token")},
        publication_date=parse_datetime(record.get("updated_at")),
    )


def _normalize_lever_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    title = record.get("text")
    if not isinstance(title, str) or not title.strip():
        return None
    desc = (
        record.get("descriptionPlain")
        if isinstance(record.get("descriptionPlain"), str)
        else html_to_text(record.get("description") if isinstance(record.get("description"), str) else None)
    )
    categories = record.get("categories") if isinstance(record.get("categories"), dict) else {}
    location = categories.get("location") if isinstance(categories.get("location"), str) else None
    apply_url = record.get("hostedUrl") if isinstance(record.get("hostedUrl"), str) else None
    return _base_vacancy(
        "lever",
        str(record.get("id") or "") or None,
        query,
        title,
        record.get("__company") if isinstance(record.get("__company"), str) else None,
        None,
        desc,
        desc,
        location,
        apply_url,
        preferences,
        apply_url=apply_url,
        source_company_identifier=record.get("__site"),
        source_metadata={
            "categories": categories,
            "lists": record.get("lists"),
            "site": record.get("__site"),
            "region": record.get("__region"),
        },
        employment_type=categories.get("commitment") if isinstance(categories.get("commitment"), str) else None,
        publication_date=parse_datetime(record.get("createdAt") if isinstance(record.get("createdAt"), str) else None),
    )


def _normalize_manual_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    title = record.get("title") if isinstance(record.get("title"), str) else "Manual import vacancy"
    text = record.get("text") if isinstance(record.get("text"), str) else None
    return _base_vacancy(
        str(record.get("manual_source") or "manual"),
        str(record.get("url") or "") or None,
        query,
        title,
        record.get("company") if isinstance(record.get("company"), str) else None,
        None,
        text,
        text,
        None,
        record.get("url") if isinstance(record.get("url"), str) else None,
        preferences,
        imported_manually=True,
        detected_language=record.get("language") if isinstance(record.get("language"), str) else None,
        publication_date=parse_datetime(record.get("published_at")),
    )


def _normalize_linkedin_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    declared_labels = {
        label for label in (record.get("source_label"), record.get("manual_source")) if isinstance(label, str) and label
    }
    if declared_labels and declared_labels != {"linkedin"}:
        raise ValueError(
            f"Conflicting raw source: __source='linkedin' but declared source is {sorted(declared_labels)!r}"
        )
    explicit_title = (
        record.get("vacancy_title")
        if isinstance(record.get("vacancy_title"), str) and record.get("vacancy_title").strip()
        else None
    )
    page_title = explicit_title or record.get("title") or record.get("document_title") or "Captured vacancy"
    if not isinstance(page_title, str):
        page_title = "Captured vacancy"
    company = (
        record.get("company") if isinstance(record.get("company"), str) and record.get("company").strip() else None
    )
    selected = record.get("selected_text") if isinstance(record.get("selected_text"), str) else ""
    visible = record.get("visible_text") if isinstance(record.get("visible_text"), str) else record.get("text") or ""
    if not isinstance(visible, str):
        visible = ""
    text = visible.strip() or selected.strip()
    requirements_text = (
        record.get("requirements_text")
        if isinstance(record.get("requirements_text"), str) and record.get("requirements_text").strip()
        else None
    )
    detected_language = detect_linkedin_content_language(linkedin_description_for_language(text))
    initial_blocker_reasons = []
    if detected_language not in preferences.languages.accepted:
        initial_blocker_reasons.append(f"unsupported LinkedIn job-content language: {detected_language}")
    blocker_text = "\n".join([page_title, _description_from_main_text(visible), selected, requirements_text or ""])
    if has_linkedin_no_longer_accepting_marker(blocker_text):
        initial_blocker_reasons.append(LINKEDIN_CLOSED_BLOCKER_REASON)
    if record.get("linkedin_status") == "expired":
        initial_blocker_reasons.append(LINKEDIN_CLOSED_BLOCKER_REASON)
    page_url = record.get("page_url") if isinstance(record.get("page_url"), str) else record.get("url")
    return _base_vacancy(
        "linkedin",
        str(page_url or "") or None,
        query,
        page_title,
        company,
        None,
        text,
        text,
        None,
        page_url if isinstance(page_url, str) else None,
        preferences,
        imported_manually=True,
        detected_language=detected_language,
        initial_blocker_reasons=initial_blocker_reasons,
        requirements_text=requirements_text,
        publication_date=parse_datetime(record.get("published_at")) or parse_datetime(record.get("received_at")),
        source_metadata={
            "hostname": record.get("hostname"),
            "captured_at": record.get("captured_at"),
            "document_title": record.get("document_title"),
            "received_at": record.get("received_at"),
            "publication_date_basis": "posting" if parse_datetime(record.get("published_at")) else "received",
            "linkedin_status": record.get("linkedin_status"),
            "linkedin_status_checked_at": record.get("linkedin_status_checked_at"),
        },
    )


def _normalize_telegram_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    warnings: list[str] = []
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        LOGGER.warning("Skipping malformed Telegram record without a title")
        return None
    channel_id = record.get("channel_id")
    message_id = record.get("message_id")
    vacancy_index = record.get("vacancy_index", 0)
    if channel_id is None or message_id is None or not isinstance(vacancy_index, int):
        LOGGER.warning("Skipping malformed Telegram record without a stable source identity")
        return None
    source_id = f"{channel_id}:{message_id}:{vacancy_index}"
    raw_text = record.get("raw_text") if isinstance(record.get("raw_text"), str) else None
    requirements_text = record.get("requirements_text") if isinstance(record.get("requirements_text"), str) else None
    location = record.get("location") if isinstance(record.get("location"), str) else None
    message_url = record.get("message_url") if isinstance(record.get("message_url"), str) else None
    apply_url = record.get("apply_url") if isinstance(record.get("apply_url"), str) else None
    eligibility = record.get("international_remote_eligibility")
    if eligibility == "explicit_restricted" and raw_text and location:
        # Older Telegram parsing treated broad regions as definite exclusions.
        # Re-evaluate that derived label on rebuild without losing explicit restrictions.
        ambiguous_region = any(term in location.casefold() for term in ("europe", "emea", "cis", "европ", "снг"))
        if ambiguous_region:
            eligibility = classify_remote_eligibility(raw_text, record.get("work_mode"), location)
    vacancy = _base_vacancy(
        "telegram",
        source_id,
        query,
        title,
        record.get("company") if isinstance(record.get("company"), str) else None,
        None,
        raw_text,
        raw_text,
        location,
        message_url,
        preferences,
        apply_url=canonical_url(apply_url),
        requirements_text=requirements_text,
        publication_date=parse_datetime(record.get("published_at")),
        salary_min=parse_optional_float(record.get("salary_min"), "salary_min", warnings),
        salary_max=parse_optional_float(record.get("salary_max"), "salary_max", warnings),
        salary_currency=record.get("salary_currency") if isinstance(record.get("salary_currency"), str) else None,
        work_mode=record.get("work_mode") if isinstance(record.get("work_mode"), str) else None,
        international_remote_eligibility=eligibility if isinstance(eligibility, str) else None,
        requires_manual_location_review=record.get("requires_manual_location_review") is True,
        warnings=warnings,
        source_metadata={
            "channel_username": record.get("channel_username"),
            "channel_id": channel_id,
            "message_id": message_id,
            "message_url": message_url,
            "vacancy_index": vacancy_index,
            "published_at": record.get("published_at"),
            "edited_at": record.get("edited_at"),
            "forwarded_from": record.get("forwarded_from"),
            "raw_text": raw_text,
            "apply_url": apply_url,
            "source_references": [
                {
                    "source": "telegram",
                    "channel_username": record.get("channel_username"),
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "vacancy_index": vacancy_index,
                    "message_url": message_url,
                    "published_at": record.get("published_at"),
                    "edited_at": record.get("edited_at"),
                    "forwarded_from": record.get("forwarded_from"),
                    "raw_text": raw_text,
                    "apply_url": apply_url,
                }
            ],
        },
    )
    external_url = canonical_url(apply_url)
    vacancy.application_url = external_url or canonical_url(message_url)
    vacancy.raw_application_urls = list(dict.fromkeys(url for url in (external_url, canonical_url(message_url)) if url))
    return vacancy


def _normalize_rvc_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    title = record.get("title")
    if not isinstance(title, str) or not title.strip() or not record.get("id"):
        return None
    text = record.get("description_text")
    text = text if isinstance(text, str) else None
    reasons = []
    if record.get("review_status") != "reviewed" or not record.get("reviewed_at"):
        reasons.append("RVC candidate requires review or is rejected/expired")
    published_at = parse_datetime(record.get("published_at"))
    if published_at is None:
        reasons.append("RVC publication date is unverified")
    if not text or len(text.strip()) < 200:
        reasons.append("RVC full description is missing")
    if record.get("accepting_applications") is not True:
        reasons.append("RVC application availability is unverified")
    warnings: list[str] = []
    application_url = canonical_url(record.get("application_url"))
    vacancy = _base_vacancy(
        "rvc",
        str(record["id"]),
        query,
        title,
        record.get("company"),
        None,
        text,
        text,
        record.get("location"),
        record.get("url"),
        preferences,
        apply_url=application_url or canonical_url(record.get("url")),
        publication_date=published_at,
        first_seen_at=parse_datetime(record.get("first_seen_at")),
        last_seen_at=parse_datetime(record.get("last_seen_at")),
        work_mode=record.get("work_mode"),
        imported_manually=True,
        initial_blocker_reasons=reasons,
        requires_manual_location_review=record.get("eligible_from_georgia") is not True,
        salary_min=parse_optional_float(record.get("salary_min"), "salary_min", warnings),
        salary_max=parse_optional_float(record.get("salary_max"), "salary_max", warnings),
        salary_currency=record.get("salary_currency"),
        salary_period=record.get("salary_period"),
        warnings=warnings,
        source_metadata={
            "reviewed_at": record.get("reviewed_at"),
            "source_references": [{"source": "rvc", "id": str(record["id"]), "url": record.get("url")}],
        },
    )
    vacancy.application_url = vacancy.apply_url
    vacancy.raw_application_urls = [application_url] if application_url else []
    return vacancy


def _normalize_jobicy_record(record: RawRecord, query: str, preferences: Preferences) -> NormalizedVacancy | None:
    warnings: list[str] = []
    title = record.get("jobTitle")
    if not isinstance(title, str) or not title.strip():
        LOGGER.warning("Skipping malformed Jobicy record without a title: %s", record)
        return None
    source_id = record.get("id") or record.get("jobSlug")
    if source_id is not None and not isinstance(source_id, str):
        source_id = str(source_id)
    if not source_id:
        warnings.append("missing source id")
    description_html = record.get("jobDescription") if isinstance(record.get("jobDescription"), str) else None
    description_text = html_to_text(description_html)
    excerpt = html_to_text(record.get("jobExcerpt") if isinstance(record.get("jobExcerpt"), str) else None)
    company = record.get("companyName") if isinstance(record.get("companyName"), str) else None
    location = record.get("jobGeo") if isinstance(record.get("jobGeo"), str) else None
    text_for_detection = "\n".join([title, excerpt or "", description_text or ""])
    language = detect_language(description_text or "")
    if language not in preferences.languages.accepted:
        warnings.append(f"unaccepted detected language: {language}")
    location_restrictions = [normalize_space(location)] if location else []
    location_text = "\n".join([*location_restrictions, text_for_detection])
    work_mode = detect_work_mode(location_text, preferences) or "remote"
    application_url = canonical_url(record.get("url") if isinstance(record.get("url"), str) else None)
    raw_urls = [application_url] if application_url else []
    job_types = as_list(record.get("jobType"))
    job_levels = as_list(record.get("jobLevel"))
    return NormalizedVacancy(
        source="jobicy",
        source_id=source_id,
        source_queries=[query],
        title=normalize_space(title),
        normalized_title=slugify_text(title),
        company=company,
        company_slug=slugify_text(company) if company else None,
        description_html=description_html,
        description_text=description_text,
        excerpt=excerpt,
        employment_type=", ".join(job_types) or None,
        seniority=", ".join(job_levels) or None,
        categories=as_list(record.get("jobIndustry")),
        location_restrictions=location_restrictions,
        timezone_restrictions=[],
        salary_min=parse_optional_float(record.get("annualSalaryMin"), "annualSalaryMin", warnings),
        salary_max=parse_optional_float(record.get("annualSalaryMax"), "annualSalaryMax", warnings),
        salary_currency=record.get("salaryCurrency") if isinstance(record.get("salaryCurrency"), str) else None,
        salary_period="year"
        if record.get("annualSalaryMin") is not None or record.get("annualSalaryMax") is not None
        else None,
        publication_date=parse_datetime(record.get("pubDate")),
        expiry_date=None,
        application_url=application_url,
        fetched_at=utc_now(),
        detected_language=language,
        work_mode=work_mode,
        detected_city=detect_allowed_city(location_text, preferences),
        warnings=warnings,
        raw_application_urls=raw_urls,
    )


def normalize_records(raw_records: list[RawRecord], preferences: Preferences) -> list[NormalizedVacancy]:
    normalized: list[NormalizedVacancy] = []
    for item in raw_records:
        if not isinstance(item, dict):
            LOGGER.warning("Skipping malformed non-object raw record; contents omitted")
            continue
        record = item.get("record", item)
        query = str(item.get("query", "unknown"))
        if not isinstance(record, dict):
            LOGGER.warning("Skipping malformed non-object raw record; contents omitted")
            continue
        container_source = item.get("__source") if isinstance(item, dict) and "record" in item else None
        record_source = record.get("__source")
        if container_source is not None and container_source != record_source:
            raise ValueError(
                f"Conflicting raw sources: wrapper __source={container_source!r}, record __source={record_source!r}"
            )
        vacancy = normalize_record(record, query, preferences)
        if vacancy:
            normalized.append(vacancy)
    return normalized


_SOURCE_NORMALIZERS = {
    "headhunter": _normalize_headhunter_record,
    "linkedin": _normalize_linkedin_record,
    "telegram": _normalize_telegram_record,
    "rvc": _normalize_rvc_record,
    "jooble": _normalize_jooble_record,
    "arbeitnow": _normalize_arbeitnow_record,
    "greenhouse": _normalize_greenhouse_record,
    "lever": _normalize_lever_record,
    "manual": _normalize_manual_record,
    "jobicy": _normalize_jobicy_record,
}
