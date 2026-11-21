from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit

from .models import NormalizedVacancy, SalaryVariant
from .utils import canonical_url, slugify_text


def deduplicate_vacancies(vacancies: list[NormalizedVacancy]) -> tuple[list[NormalizedVacancy], int]:
    result: list[NormalizedVacancy] = []
    key_sets: list[set[str]] = []
    indexes: dict[str, int] = {}
    duplicates = 0
    for vacancy in vacancies:
        incoming_keys = vacancy_identity_keys(vacancy)
        matching_indexes = sorted(
            {indexes[key] for key in incoming_keys if key in indexes}
            | {
                index
                for index, existing in enumerate(result)
                if _near_identical_company_title_description(vacancy, existing)
            }
        )
        if not matching_indexes:
            indexes.update({key: len(result) for key in incoming_keys})
            result.append(vacancy)
            key_sets.append(incoming_keys)
            continue

        existing_index = matching_indexes[0]
        merged = result[existing_index]
        merged_keys = set(key_sets[existing_index]) | incoming_keys
        for bridged_index in reversed(matching_indexes[1:]):
            merged = merge_vacancies(merged, result[bridged_index])
            merged_keys.update(key_sets[bridged_index])
            del result[bridged_index]
            del key_sets[bridged_index]
            duplicates += 1
        duplicates += 1
        merged = merge_vacancies(merged, vacancy)
        result[existing_index] = merged
        merged_keys.update(vacancy_identity_keys(merged))
        key_sets[existing_index] = merged_keys
        indexes = {key: index for index, keys in enumerate(key_sets) for key in keys}
    return result, duplicates


def url_identity_keys(value: str | None) -> set[str]:
    url = canonical_url(value)
    if not url:
        return set()
    keys = {f"url:{url}"}
    # A Telegram/ATS card can expose a provider's vacancy URL without its source_ids.
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return keys
    prefix, _, number = parts.path.rstrip("/").rpartition("/")
    if number.isdecimal():
        if host == "www.linkedin.com" and prefix == "/jobs/view":
            keys.add(f"id:linkedin:{number}")
        if (host in {"hh.ru", "headhunter.ge"} or host.endswith((".hh.ru", ".headhunter.ge"))) and prefix == "/vacancy":
            keys.add(f"id:headhunter:{number}")
    return keys


def vacancy_identity_keys(vacancy: NormalizedVacancy) -> set[str]:
    """Exact keys shared by live deduplication and persistent exclusion history."""
    keys: set[str] = set()
    for url_field in [vacancy.apply_url, vacancy.application_url, vacancy.source_url, *vacancy.source_urls]:
        keys.update(url_identity_keys(url_field))
    if vacancy.source_id:
        keys.add(f"id:{vacancy.source}:{vacancy.source_id}")
    for source, ids in vacancy.source_ids.items():
        keys.update(f"id:{source}:{source_id}" for source_id in ids if source_id)
    title = slugify_text(vacancy.title)
    company = slugify_text(vacancy.company)
    location = slugify_text(", ".join(vacancy.location_restrictions))
    if title and company and location:
        keys.add(f"company-title-location:{company}::{title}::{location}")
    description = slugify_text(vacancy.description_text)
    if len(description) >= 80:
        digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
        keys.add(f"description:{digest}")
    return keys


def vacancies_match(left: NormalizedVacancy, right: NormalizedVacancy) -> bool:
    """Return whether the shared deterministic duplicate keys identify the same vacancy."""
    return bool(
        vacancy_identity_keys(left) & vacancy_identity_keys(right)
    ) or _near_identical_company_title_description(left, right)


MIN_REPOST_WORDS = 40


def repost_group(vacancy: NormalizedVacancy) -> str:
    company, title = slugify_text(vacancy.company), slugify_text(vacancy.title)
    return f"{company}/{title}" if company and title else ""


def _near_identical_company_title_description(left: NormalizedVacancy, right: NormalizedVacancy) -> bool:
    """Match reposts whose URLs differ but company, title, and substantive word content do not."""
    group = repost_group(left)
    if not group or group != repost_group(right):
        return False
    return repost_descriptions_match(slugify_text(left.description_text), slugify_text(right.description_text))


def repost_descriptions_match(left: str, right: str) -> bool:
    """Compare normalized descriptions after exact company and title matching."""
    left_words = left.split()
    right_words = right.split()
    if min(len(left_words), len(right_words)) < MIN_REPOST_WORDS:
        return False
    left_counts = Counter(left_words)
    right_counts = Counter(right_words)
    shared_word_count = sum((left_counts & right_counts).values())
    similarity = 2 * shared_word_count / (len(left_words) + len(right_words))
    return similarity >= 0.98


def _posting_datetime(vacancy: NormalizedVacancy) -> datetime | None:
    date = vacancy.publication_date
    if date is None or date.tzinfo is None or vacancy.source in {"greenhouse", "jooble"}:
        return None
    basis = vacancy.source_metadata.get("publication_date_basis")
    if basis and basis != "posting":
        return None
    # Legacy LinkedIn records used received_at as a publication-date fallback.
    if vacancy.source == "linkedin" and not basis and vacancy.source_metadata.get("received_at"):
        return None
    return date


def _latest_conditions(left: NormalizedVacancy, right: NormalizedVacancy) -> NormalizedVacancy | None:
    left_date, right_date = _posting_datetime(left), _posting_datetime(right)
    if left_date is None or right_date is None or left_date == right_date:
        return None
    if any(v.description_completeness != "complete" for v in (left, right)):
        return None
    if any(v.source_metadata.get("unresolved_version_conflict") for v in (left, right)):
        return None
    # A weak company/title/location match does not prove these are versions of one opening.
    shared = vacancy_identity_keys(left) & vacancy_identity_keys(right)
    if not any(key.startswith(("url:", "id:")) for key in shared):
        return None
    return left if left_date > right_date else right


_SALARY_FIELDS = ("salary_min", "salary_max", "salary_currency", "salary_period", "salary_gross", "hiring_country")
SALARY_CONFLICT_WARNING = "conflicting salary offers across sources; verify salary"


def _salary_variants(vacancy: NormalizedVacancy) -> list[SalaryVariant]:
    if vacancy.salary_variants:
        return vacancy.salary_variants
    if vacancy.salary_min is None and vacancy.salary_max is None:
        return []
    return [
        SalaryVariant(
            **{field: getattr(vacancy, field) for field in _SALARY_FIELDS},
            source=vacancy.source,
            source_id=vacancy.source_id,
        )
    ]


def _merge_salary_variants(left: NormalizedVacancy, right: NormalizedVacancy) -> None:
    variants = _salary_variants(left) + _salary_variants(right)
    if not variants:
        return
    if left.salary_min is None and left.salary_max is None:
        for field in _SALARY_FIELDS:
            setattr(left, field, getattr(variants[0], field))
    distinct = {tuple(getattr(variant, field) for field in _SALARY_FIELDS) for variant in variants}
    if len(distinct) < 2:
        return
    unique = {variant.model_dump_json(): variant for variant in variants}
    left.salary_variants = [unique[key] for key in sorted(unique)]
    left.warnings.append(SALARY_CONFLICT_WARNING)


def _merge_unversioned_conditions(left: NormalizedVacancy, right: NormalizedVacancy) -> None:
    _merge_salary_variants(left, right)
    # A tax basis belongs to its salary tuple, never to an unrelated amount.
    salary_fields = ("salary_min", "salary_max", "salary_currency", "salary_period", "hiring_country")
    if all(getattr(left, field) == getattr(right, field) for field in salary_fields):
        if left.salary_gross is None:
            left.salary_gross = right.salary_gross
        elif right.salary_gross is not None and left.salary_gross != right.salary_gross:
            left.salary_gross = True
            left.warnings.append("conflicting gross/net salary markers across sources; verify net pay")
    if (
        right.description_text
        and left.description_text
        and right.description_text not in left.description_text
        and left.description_text not in right.description_text
    ):
        left.description_text = f"{left.description_text}\n\n{right.description_text}"
        if right.description_html:
            left.description_html = f"{left.description_html or ''}\n{right.description_html}".strip()
    elif len(right.description_html or right.description_text or "") > len(
        left.description_html or left.description_text or ""
    ):
        left.description_text = right.description_text
        left.description_html = right.description_html
    if right.requirements_text and left.requirements_text:
        requirements_differ = (
            right.requirements_text not in left.requirements_text
            and left.requirements_text not in right.requirements_text
        )
        if requirements_differ:
            left.requirements_text = f"{left.requirements_text}\n\n{right.requirements_text}"
        elif len(right.requirements_text) > len(left.requirements_text):
            left.requirements_text = right.requirements_text
    elif right.requirements_text:
        left.requirements_text = right.requirements_text
    left.categories = sorted(set([*left.categories, *right.categories]), key=str.casefold)


def merge_vacancies(left: NormalizedVacancy, right: NormalizedVacancy) -> NormalizedVacancy:
    latest = _latest_conditions(left, right)
    if latest is right:
        left, right = right, left
    if latest is None:
        if any(
            getattr(left, field) != getattr(right, field)
            for field in (
                "description_text",
                "requirements_text",
                "salary_min",
                "salary_max",
                "salary_gross",
                "salary_currency",
                "salary_period",
                "hiring_country",
                "location_restrictions",
                "work_mode",
            )
        ):
            left.warnings.append("conflicting vacancy versions; posting datetime does not establish replacement")
            left.source_metadata["unresolved_version_conflict"] = True
        _merge_unversioned_conditions(left, right)
    else:
        left.source_metadata["conditions_selected_by"] = "posting_datetime"
    # Availability and application evidence survive content-version selection.
    left.blocker = left.blocker or right.blocker
    left.blocker_reasons = sorted(set(left.blocker_reasons + right.blocker_reasons))
    left.source_queries = sorted(set(left.source_queries + right.source_queries))
    left.sources = sorted(set((left.sources or [left.source]) + (right.sources or [right.source])))
    left.source_urls = sorted(
        {
            url
            for vacancy in (left, right)
            for url in (*vacancy.source_urls, vacancy.source_url, vacancy.apply_url, vacancy.application_url)
            if url
        }
    )
    left_references = left.source_metadata.get("source_references", [])
    right_references = right.source_metadata.get("source_references", [])
    references = [
        reference
        for reference in [
            *(left_references if isinstance(left_references, list) else []),
            *(right_references if isinstance(right_references, list) else []),
        ]
        if isinstance(reference, dict)
    ]
    right_metadata = {key: value for key, value in right.source_metadata.items() if key != "source_references"}
    left.source_metadata = {
        **left.source_metadata,
        **{f"{right.source}:{key}": value for key, value in right_metadata.items()},
    }
    if references:
        left.source_metadata["source_references"] = [
            reference for index, reference in enumerate(references) if reference not in references[:index]
        ]
    if left.source_id:
        left.source_ids[left.source] = sorted(set(left.source_ids.get(left.source, []) + [left.source_id]))
    for source, ids in right.source_ids.items():
        left.source_ids[source] = sorted(set(left.source_ids.get(source, []) + ids))
    if right.source_id:
        left.source_ids[right.source] = sorted(set(left.source_ids.get(right.source, []) + [right.source_id]))
    left.warnings = sorted(set(left.warnings + right.warnings))
    left.raw_application_urls = sorted(set(left.raw_application_urls + right.raw_application_urls))
    if not left.application_url and right.application_url:
        left.application_url = right.application_url
    if not left.apply_url and right.apply_url:
        left.apply_url = right.apply_url
    if not left.source_url and right.source_url:
        left.source_url = right.source_url
    if not left.source_id and right.source_id:
        left.source_id = right.source_id
    if right.last_seen_at and (not left.last_seen_at or right.last_seen_at > left.last_seen_at):
        left.last_seen_at = right.last_seen_at
    return left
