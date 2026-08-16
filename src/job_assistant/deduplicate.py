from __future__ import annotations

from .models import NormalizedVacancy
from .utils import canonical_url, slugify_text


def deduplicate_vacancies(vacancies: list[NormalizedVacancy]) -> tuple[list[NormalizedVacancy], int]:
    result: list[NormalizedVacancy] = []
    indexes: dict[str, int] = {}
    duplicates = 0
    for vacancy in vacancies:
        keys = _keys(vacancy)
        existing_index = next((indexes[key] for key in keys if key in indexes), None)
        if existing_index is None:
            indexes.update({key: len(result) for key in keys})
            result.append(vacancy)
            continue
        duplicates += 1
        merged = merge_vacancies(result[existing_index], vacancy)
        result[existing_index] = merged
        indexes.update({key: existing_index for key in _keys(merged)})
    return result, duplicates


def _keys(vacancy: NormalizedVacancy) -> list[str]:
    keys: list[str] = []
    for url_field in [vacancy.apply_url, vacancy.application_url, vacancy.source_url, *vacancy.source_urls]:
        url = canonical_url(url_field)
        if url:
            keys.append(f"url:{url}")
    if vacancy.source_id:
        keys.append(f"id:{vacancy.source}:{vacancy.source_id}")
    company_title = f"{slugify_text(vacancy.company)}::{slugify_text(vacancy.title)}::{slugify_text(', '.join(vacancy.location_restrictions))}"
    if company_title.strip(":"):
        keys.append(f"company-title:{company_title}")
    return keys


def merge_vacancies(left: NormalizedVacancy, right: NormalizedVacancy) -> NormalizedVacancy:
    if right.description_text and left.description_text and right.description_text not in left.description_text and left.description_text not in right.description_text:
        left.description_text = f"{left.description_text}\n\n{right.description_text}"
        if right.description_html:
            left.description_html = f"{left.description_html or ''}\n{right.description_html}".strip()
    elif len(right.description_html or right.description_text or "") > len(left.description_html or left.description_text or ""):
        left.description_text = right.description_text
        left.description_html = right.description_html
    left.source_queries = sorted(set(left.source_queries + right.source_queries))
    left.sources = sorted(set((left.sources or [left.source]) + (right.sources or [right.source])))
    left.source_urls = sorted(set([url for url in [*left.source_urls, *right.source_urls, left.source_url, right.source_url] if url]))
    left.source_metadata = {**left.source_metadata, **{f"{right.source}:{key}": value for key, value in right.source_metadata.items()}}
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
