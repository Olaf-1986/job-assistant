from __future__ import annotations

import hashlib

from .models import NormalizedVacancy
from .utils import canonical_url, slugify_text


def deduplicate_vacancies(vacancies: list[NormalizedVacancy]) -> tuple[list[NormalizedVacancy], int]:
    result: list[NormalizedVacancy] = []
    key_sets: list[set[str]] = []
    indexes: dict[str, int] = {}
    duplicates = 0
    for vacancy in vacancies:
        incoming_keys = set(_keys(vacancy))
        matching_indexes = sorted({indexes[key] for key in incoming_keys if key in indexes})
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
        merged_keys.update(_keys(merged))
        key_sets[existing_index] = merged_keys
        indexes = {key: index for index, keys in enumerate(key_sets) for key in keys}
    return result, duplicates


def _keys(vacancy: NormalizedVacancy) -> list[str]:
    keys: list[str] = []
    for url_field in [vacancy.apply_url, vacancy.application_url, vacancy.source_url, *vacancy.source_urls]:
        url = canonical_url(url_field)
        if url:
            keys.append(f"url:{url}")
    if vacancy.source_id:
        keys.append(f"id:{vacancy.source}:{vacancy.source_id}")
    title = slugify_text(vacancy.title)
    company = slugify_text(vacancy.company)
    location = slugify_text(", ".join(vacancy.location_restrictions))
    if title and company and location:
        keys.append(f"company-title-location:{company}::{title}::{location}")
    description = slugify_text(vacancy.description_text)
    if len(description) >= 80:
        digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
        keys.append(f"description:{digest}")
    return keys


def vacancies_match(left: NormalizedVacancy, right: NormalizedVacancy) -> bool:
    """Return whether the shared deterministic duplicate keys identify the same vacancy."""
    return bool(set(_keys(left)) & set(_keys(right)))


def merge_vacancies(left: NormalizedVacancy, right: NormalizedVacancy) -> NormalizedVacancy:
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
    left.source_queries = sorted(set(left.source_queries + right.source_queries))
    left.sources = sorted(set((left.sources or [left.source]) + (right.sources or [right.source])))
    left.source_urls = sorted(
        set([url for url in [*left.source_urls, *right.source_urls, left.source_url, right.source_url] if url])
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
