from __future__ import annotations

from typing import Any

from .config import Preferences
from .deduplicate import deduplicate_vacancies
from .export import export_all
from .filters import apply_filters
from .models import BatchStats, RawRecord
from .normalize import normalize_records
from .paths import output_paths
from .scoring import score_vacancies
from .utils import read_json


def read_headhunter_raw(preferences: Preferences) -> list[RawRecord]:
    path = output_paths(preferences)["raw"]
    if not path.exists():
        return []
    data = read_json(path, [])
    return data if isinstance(data, list) else []


def read_linkedin_manual_raw(preferences: Preferences) -> list[RawRecord]:
    path = output_paths(preferences)["manual_imports"]
    if not path.exists():
        return []
    data = read_json(path, [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and _is_linkedin_raw(item)]


def rebuild_from_authoritative_sources(
    preferences: Preferences, stats: BatchStats | None = None, headhunter_raw: list[RawRecord] | None = None
) -> dict[str, Any]:
    hh_raw = _dedupe_headhunter_raw(read_headhunter_raw(preferences) if headhunter_raw is None else headhunter_raw)
    linkedin_raw = read_linkedin_manual_raw(preferences)
    all_raw = [*hh_raw, *linkedin_raw]
    vacancies = normalize_records(all_raw, preferences)
    vacancies, duplicates = deduplicate_vacancies(vacancies)
    run_stats = stats or BatchStats()
    run_stats.duplicates_merged = duplicates
    vacancies = score_vacancies(apply_filters(vacancies, preferences), preferences)
    return export_all(hh_raw, vacancies, preferences, run_stats, preferences.all_queries)


def _dedupe_headhunter_raw(raw_records: list[RawRecord]) -> list[RawRecord]:
    deduped: list[RawRecord] = []
    seen: set[str] = set()
    for item in raw_records:
        record = item.get("record", item) if isinstance(item, dict) else {}
        vacancy_id = record.get("id") if isinstance(record, dict) else None
        key = f"headhunter:{vacancy_id}" if vacancy_id is not None else str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _is_linkedin_raw(item: dict[str, Any]) -> bool:
    record = item.get("record", item)
    if not isinstance(record, dict):
        return False
    if record.get("__source") == "manual_capture":
        return record.get("source_label") == "linkedin"
    if record.get("__source") == "manual":
        return record.get("manual_source") == "linkedin"
    return False
