from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import Preferences
from .deduplicate import deduplicate_vacancies
from .export import export_all
from .filters import apply_filters
from .headhunter_state import successful_observation
from .linkedin_queue import linkedin_job_id
from .models import BatchStats, NormalizedVacancy, RawRecord, with_raw_source
from .normalize import normalize_records
from .paths import output_paths
from .publication import recover_publication
from .raw_store import read_raw_store, validate_raw_records
from .recovered_state import apply_recovered_state
from .scoring import score_vacancies
from .utils import read_json


def read_headhunter_raw(preferences: Preferences) -> list[RawRecord]:
    recover_publication(output_paths(preferences)["dir"])
    path = output_paths(preferences)["raw"]
    return _with_source(read_raw_store(path, "headhunter"), "headhunter", "HeadHunter raw store")


def read_linkedin_manual_raw(preferences: Preferences) -> list[RawRecord]:
    recover_publication(output_paths(preferences)["dir"])
    path = output_paths(preferences)["manual_imports"]
    data = read_raw_store(path, "manual")
    tagged = [
        _with_source([item], "linkedin", "LinkedIn manual store")[0]
        for item in data
        if isinstance(item, dict) and _is_linkedin_raw(item)
    ]
    received_at_by_job_id = _linkedin_received_at_by_job_id(preferences)
    return [_with_linkedin_received_at(item, received_at_by_job_id) for item in tagged]


def read_telegram_raw(preferences: Preferences) -> list[RawRecord]:
    recover_publication(output_paths(preferences)["dir"])
    path = output_paths(preferences)["telegram_raw"]
    return _with_source(read_raw_store(path, "telegram"), "telegram", "Telegram raw store")


def rebuild_from_authoritative_sources(
    preferences: Preferences,
    stats: BatchStats | None = None,
    headhunter_raw: list[RawRecord] | None = None,
    *,
    shortlist_source: str | None = None,
    published_since: datetime | None = None,
) -> dict[str, Any]:
    hh_raw, vacancies, duplicates = prepare_authoritative_vacancies(preferences, headhunter_raw)
    run_stats = stats or BatchStats()
    run_stats.duplicates_merged = duplicates
    return export_all(
        hh_raw,
        vacancies,
        preferences,
        run_stats,
        preferences.all_queries,
        shortlist_source=shortlist_source,
        published_since=published_since,
    )


def prepare_authoritative_vacancies(
    preferences: Preferences, headhunter_raw: list[RawRecord] | None = None
) -> tuple[list[RawRecord], list[NormalizedVacancy], int]:
    """Run the shared pipeline without exporting its derived outputs."""
    # Even a successful replacement must not hide a damaged existing store.
    stored_raw = read_headhunter_raw(preferences)
    source_raw = stored_raw if headhunter_raw is None else headhunter_raw
    validate_raw_records(source_raw, "headhunter")
    hh_raw = _dedupe_headhunter_raw(_with_source(source_raw, "headhunter", "HeadHunter input"))
    linkedin_raw = read_linkedin_manual_raw(preferences)
    telegram_raw = read_telegram_raw(preferences)
    all_raw = [*hh_raw, *linkedin_raw, *telegram_raw]
    vacancies = normalize_records(all_raw, preferences)
    vacancies, duplicates = deduplicate_vacancies(vacancies)
    apply_recovered_state(vacancies, preferences)
    vacancies = score_vacancies(apply_filters(vacancies, preferences), preferences)
    return hh_raw, vacancies, duplicates


def _dedupe_headhunter_raw(raw_records: list[RawRecord]) -> list[RawRecord]:
    deduped: list[RawRecord] = []
    positions: dict[str, int] = {}
    for item in raw_records:
        record = item.get("record", item) if isinstance(item, dict) else {}
        vacancy_id = record.get("id") if isinstance(record, dict) else None
        key = f"headhunter:{vacancy_id}" if vacancy_id is not None else str(item)
        if key in positions:
            index = positions[key]
            old_time = successful_observation(deduped[index])
            new_time = successful_observation(item)
            # Unknown legacy ordering is preserved, never interpreted as freshness.
            if new_time is not None and (old_time is None or new_time > old_time):
                deduped[index] = item
            continue
        positions[key] = len(deduped)
        deduped.append(item)
    return deduped


def merge_headhunter_raw(stored: list[RawRecord], incoming: list[RawRecord]) -> list[RawRecord]:
    """Upsert only proven newer complete observations; keep legacy records readable."""
    combined = [*stored, *incoming]
    validate_raw_records(combined, "headhunter")
    return _dedupe_headhunter_raw(combined)


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


def _linkedin_received_at_by_job_id(preferences: Preferences) -> dict[str, str]:
    path = output_paths(preferences)["email_candidates"]
    if not path.exists():
        return {}
    candidates = read_json(path, [])
    if not isinstance(candidates, list):
        return {}
    return {
        str(item["external_id"]): item["received_at"]
        for item in candidates
        if isinstance(item, dict)
        and item.get("source") == "linkedin"
        and item.get("external_id") is not None
        and isinstance(item.get("received_at"), str)
        and item["received_at"].strip()
    }


def _with_linkedin_received_at(item: RawRecord, received_at_by_job_id: dict[str, str]) -> RawRecord:
    is_wrapper = isinstance(item.get("record"), dict)
    record = item["record"] if is_wrapper else item
    if record.get("published_at") or record.get("received_at"):
        return item
    url = next(
        (record.get(field) for field in ("page_url", "url", "canonical_url") if isinstance(record.get(field), str)),
        None,
    )
    received_at = received_at_by_job_id.get(linkedin_job_id(url) or "")
    if received_at is None:
        return item
    enriched = {**record, "received_at": received_at}
    return {**item, "record": enriched} if is_wrapper else enriched


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
