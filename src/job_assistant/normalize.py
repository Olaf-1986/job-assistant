from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from .config import Preferences
from .language import detect_language
from .location import detect_allowed_city, detect_work_mode
from .models import NormalizedVacancy, RawRecord
from .utils import canonical_url, normalize_space, slugify_text, utc_now

LOGGER = logging.getLogger(__name__)


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
    return normalizer(record, query, preferences)


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
    text_for_detection = "\n".join([title, excerpt or "", description_text or "", location or ""])
    language = extra.pop("detected_language", None) or detect_language(text_for_detection)
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
        work_mode=extra.pop("work_mode", None) or detect_work_mode(location_text, preferences),
        detected_city=detect_allowed_city(location_text, preferences),
        warnings=warnings,
        raw_application_urls=[canonical] if canonical else [],
        employment_type=extra.pop("employment_type", None),
        salary_min=extra.pop("salary_min", None),
        salary_max=extra.pop("salary_max", None),
        salary_currency=extra.pop("salary_currency", None),
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
    text_for_detection = "\n".join([title, description_text or "", area or "", schedule or ""])
    language = detect_language(text_for_detection)
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
        categories=[],
        location_restrictions=location_restrictions,
        timezone_restrictions=[],
        salary_min=parse_optional_float(salary.get("from"), "salary.from", warnings),
        salary_max=parse_optional_float(salary.get("to"), "salary.to", warnings),
        salary_currency=salary.get("currency") if isinstance(salary.get("currency"), str) else None,
        salary_period=None,
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
    text = selected.strip() or visible.strip() or page_title
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
        source_metadata={
            "hostname": record.get("hostname"),
            "captured_at": record.get("captured_at"),
            "document_title": record.get("document_title"),
        },
    )


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
    language = detect_language(text_for_detection)
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
        record = item.get("record", item)
        query = str(item.get("query", "unknown"))
        if not isinstance(record, dict):
            LOGGER.warning("Skipping malformed non-object API record: %s", item)
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
    "jooble": _normalize_jooble_record,
    "arbeitnow": _normalize_arbeitnow_record,
    "greenhouse": _normalize_greenhouse_record,
    "lever": _normalize_lever_record,
    "manual": _normalize_manual_record,
    "jobicy": _normalize_jobicy_record,
}
