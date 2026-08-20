from __future__ import annotations

from typing import Any

from .config import Preferences
from .deduplicate import deduplicate_vacancies
from .export import export_all
from .filters import apply_filters
from .models import BatchStats, RawRecord, with_raw_source
from .normalize import normalize_records
from .paths import output_paths
from .scoring import score_vacancies
from .utils import read_json


def read_headhunter_raw(preferences: Preferences) -> list[RawRecord]:
    path = output_paths(preferences)["raw"]
    if not path.exists():
        return []
    data = read_json(path, [])
    return _with_source(data, "headhunter", "HeadHunter raw store") if isinstance(data, list) else []


def read_linkedin_manual_raw(preferences: Preferences) -> list[RawRecord]:
    path = output_paths(preferences)["manual_imports"]
    if not path.exists():
        return []
    data = read_json(path, [])
    if not isinstance(data, list):
        return []
    return [
        _with_source([item], "linkedin", "LinkedIn manual store")[0]
        for item in data
        if isinstance(item, dict) and _is_linkedin_raw(item)
    ]


def rebuild_from_authoritative_sources(
    preferences: Preferences, stats: BatchStats | None = None, headhunter_raw: list[RawRecord] | None = None
) -> dict[str, Any]:
    source_raw = read_headhunter_raw(preferences) if headhunter_raw is None else headhunter_raw
    hh_raw = _dedupe_headhunter_raw(_with_source(source_raw, "headhunter", "HeadHunter input"))
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
    source = record.get("__source")
    if source == "linkedin":
        return True
    if source == "manual_capture":
        return record.get("source_label") == "linkedin"
    if record.get("__source") == "manual":
        return record.get("manual_source") == "linkedin"
    return source is None


def _with_source(raw_records: list[RawRecord], source: str, boundary: str) -> list[RawRecord]:
    tagged: list[RawRecord] = []
    for item in raw_records:
        if not isinstance(item, dict):
            tagged.append(item)
            continue
        is_wrapper = isinstance(item.get("record"), dict)
        record = item["record"] if is_wrapper else item
        existing = record.get("__source")
        if existing not in {None, source}:
            if source == "linkedin" and _is_legacy_linkedin_source(record, existing):
                record = {key: value for key, value in record.items() if key != "__source"}
            else:
                raise ValueError(f"Conflicting source at {boundary}: expected __source={source!r}, got {existing!r}")
        updated_record = with_raw_source(record, source, boundary)
        tagged.append({**item, "record": updated_record} if is_wrapper else updated_record)
    return tagged


def _is_legacy_linkedin_source(record: RawRecord, source: Any) -> bool:
    return (source == "manual_capture" and record.get("source_label") == "linkedin") or (
        source == "manual" and record.get("manual_source") == "linkedin"
    )
