from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .config import Preferences
from .deduplicate import deduplicate_vacancies
from .export import sorted_shortlist
from .filters import apply_filters
from .models import BatchStats, NormalizedVacancy, RawRecord
from .normalize import normalize_records
from .paths import output_paths
from .persistence import rebuild_from_authoritative_sources
from .scoring import score_vacancies
from .telegram_client import TelegramError, TelegramMessage, TelegramRateLimitError, build_telegram_reader
from .telegram_parser import TelegramParseResult, parse_telegram_message
from .utils import read_json, utc_now, write_json


class TelegramReader(Protocol):
    async def __aenter__(self) -> TelegramReader: ...

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...

    async def resolve_joined_sources(self, allowlist: list[str]) -> dict[str, Any]: ...

    async def read_messages(
        self, source: str, entity: Any, since: datetime, limit: int | None = None
    ) -> list[TelegramMessage]: ...


@dataclass
class TelegramSourceReport:
    source: str
    messages_read: int = 0
    messages_inspected: int = 0
    vacancy_like_messages: int = 0
    parsed_vacancies: int = 0
    target_role_matches: int = 0
    rejected_non_vacancy_messages: int = 0
    parse_failures: int = 0
    most_recent_message_date: str | None = None
    imported_records: int = 0
    error: str | None = None


@dataclass
class TelegramFetchResult:
    dry_run: bool
    source_reports: dict[str, TelegramSourceReport] = field(default_factory=dict)
    parsed_results: dict[str, list[TelegramParseResult]] = field(default_factory=dict)
    eligible_targets: list[NormalizedVacancy] = field(default_factory=list)
    eligible_targets_by_source: dict[str, list[NormalizedVacancy]] = field(default_factory=dict)
    summary: dict[str, Any] | None = None

    @property
    def messages_read(self) -> int:
        return sum(report.messages_read for report in self.source_reports.values())

    @property
    def imported_records(self) -> int:
        return sum(report.imported_records for report in self.source_reports.values())

    @property
    def messages_inspected(self) -> int:
        return sum(report.messages_inspected for report in self.source_reports.values())

    @property
    def errors(self) -> list[str]:
        return [f"{name}: {report.error}" for name, report in self.source_reports.items() if report.error]


async def fetch_telegram(
    preferences: Preferences,
    *,
    since_days: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    all_history: bool = False,
    ignore_checkpoints: bool = False,
    reader: TelegramReader | None = None,
    now: datetime | None = None,
    sleep: Any = asyncio.sleep,
) -> TelegramFetchResult:
    """Read allowlisted dialogs and optionally persist target vacancies and checkpoints."""
    config = preferences.sources.telegram
    if not config.enabled:
        raise TelegramError("Telegram source is disabled in configuration")
    effective_since_days = config.initial_since_days if since_days is None else since_days
    if effective_since_days < 1:
        raise ValueError("since_days must be at least 1")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    if limit is not None and not dry_run:
        raise ValueError("limit is only supported with dry_run because a partial history must not advance checkpoints")
    if all_history and (not dry_run or limit is None):
        raise ValueError("all_history requires dry_run and an explicit limit")
    if ignore_checkpoints and not dry_run:
        raise ValueError("ignore_checkpoints is only supported with dry_run")
    current_time = _as_utc(now or utc_now())
    paths = output_paths(preferences)
    raw_store = _load_list(paths["telegram_raw"])
    failures = _load_list(paths["telegram_failures"])
    checkpoints = _load_mapping(paths["telegram_checkpoints"])
    source_names = config.enabled_sources
    result = TelegramFetchResult(dry_run=dry_run)
    remaining = limit
    successful_source = False
    history_messages: dict[str, list[TelegramMessage]] = {}
    active_reader = reader or build_telegram_reader(preferences)

    async with active_reader:
        resolved = await active_reader.resolve_joined_sources(source_names)
        for source_index, source in enumerate(source_names):
            report = TelegramSourceReport(source=source)
            result.source_reports[source] = report
            if source not in resolved:
                report.error = "configured source is not present in the account's joined dialogs"
                continue
            cutoff = (
                datetime.min.replace(tzinfo=UTC)
                if all_history
                else _source_cutoff(
                    None if ignore_checkpoints else checkpoints.get(source),
                    current_time,
                    since_days=effective_since_days,
                    lookback_hours=config.edit_lookback_hours,
                )
            )
            source_limit = limit if all_history else remaining
            try:
                messages = await active_reader.read_messages(source, resolved[source], cutoff, source_limit)
                messages = sorted(messages, key=lambda item: (item.published_at, item.message_id))
                parsed_results = (
                    [] if all_history else [parse_telegram_message(message, preferences) for message in messages]
                )
            except TelegramError as exc:
                report.error = str(exc)
                if isinstance(exc, TelegramRateLimitError):
                    for skipped_source in source_names[source_index + 1 :]:
                        result.source_reports[skipped_source] = TelegramSourceReport(
                            source=skipped_source,
                            error="not processed because an earlier source hit a Telegram rate limit",
                        )
                    break
                continue
            except Exception:
                report.error = "unexpected Telegram source processing failure"
                continue

            successful_source = True
            if all_history:
                history_messages[source] = messages
                report.messages_inspected = len(messages)
            else:
                result.parsed_results[source] = parsed_results
                _populate_report(report, messages, parsed_results)
            if not dry_run:
                raw_store = _replace_processed_messages(raw_store, parsed_results)
                failures = _replace_failures(failures, parsed_results)
                write_json(paths["telegram_raw"], raw_store)
                write_json(paths["telegram_failures"], failures)
                if messages:
                    checkpoints[source] = _updated_checkpoint(checkpoints.get(source), messages, current_time)
                    write_json(paths["telegram_checkpoints"], checkpoints)
            if remaining is not None and not all_history:
                remaining -= len(messages)
                if remaining <= 0:
                    break
            if source_index < len(source_names) - 1:
                await sleep(config.source_pause_seconds)

    if all_history:
        selected_messages = _select_latest_messages(history_messages, limit)
        for source, report in result.source_reports.items():
            if report.error:
                continue
            messages = selected_messages.get(source, [])
            try:
                parsed_results = [parse_telegram_message(message, preferences) for message in messages]
            except Exception:
                report.error = "unexpected Telegram source processing failure"
                continue
            result.parsed_results[source] = parsed_results
            _populate_report(
                report,
                messages,
                parsed_results,
                messages_inspected=report.messages_inspected,
            )

    if dry_run:
        result.eligible_targets = _eligible_target_preview(preferences, result.parsed_results)
        result.eligible_targets_by_source = {
            source: _eligible_target_preview(preferences, {source: parsed_results})
            for source, parsed_results in result.parsed_results.items()
        }
    if not dry_run and successful_source:
        stats = BatchStats(
            requests_made=sum(1 for report in result.source_reports.values() if report.error is None),
            raw_records_received=result.imported_records,
            errors=result.errors,
        )
        result.summary = rebuild_from_authoritative_sources(preferences, stats)
    return result


def _select_latest_messages(
    messages_by_source: dict[str, list[TelegramMessage]], limit: int | None
) -> dict[str, list[TelegramMessage]]:
    flattened = [(source, message) for source, messages in messages_by_source.items() for message in messages]
    flattened.sort(
        key=lambda item: (item[1].published_at, item[1].message_id, item[0]),
        reverse=True,
    )
    selected = flattened[:limit] if limit is not None else flattened
    grouped: dict[str, list[TelegramMessage]] = {}
    for source, message in selected:
        grouped.setdefault(source, []).append(message)
    for messages in grouped.values():
        messages.sort(key=lambda item: (item.published_at, item.message_id))
    return grouped


def _eligible_target_preview(
    preferences: Preferences,
    parsed_results: dict[str, list[TelegramParseResult]],
) -> list[NormalizedVacancy]:
    raw = [
        {"query": f"telegram:{source}", "record": dict(record)}
        for source, results in parsed_results.items()
        for result in results
        for record in result.target_records
    ]
    normalized = normalize_records(raw, preferences)
    deduplicated, _ = deduplicate_vacancies(normalized)
    scored = score_vacancies(apply_filters(deduplicated, preferences), preferences)
    return sorted_shortlist(scored, len(scored))


def _populate_report(
    report: TelegramSourceReport,
    messages: list[TelegramMessage],
    parsed_results: list[TelegramParseResult],
    *,
    messages_inspected: int | None = None,
) -> None:
    report.messages_read = len(messages)
    report.messages_inspected = len(messages) if messages_inspected is None else messages_inspected
    report.vacancy_like_messages = sum(item.vacancy_like for item in parsed_results)
    report.parsed_vacancies = sum(len(item.candidates) for item in parsed_results)
    report.target_role_matches = sum(len(item.target_records) for item in parsed_results)
    report.rejected_non_vacancy_messages = sum(item.rejection_reason is not None for item in parsed_results)
    report.parse_failures = sum(item.failure_reason is not None for item in parsed_results)
    report.imported_records = report.target_role_matches
    if messages:
        report.most_recent_message_date = max(message.published_at for message in messages).isoformat()


def _replace_processed_messages(
    existing: list[RawRecord], parsed_results: list[TelegramParseResult]
) -> list[RawRecord]:
    processed_messages = {(result.message.channel_id, result.message.message_id) for result in parsed_results}
    old_records: dict[tuple[int, int, int], dict[str, Any]] = {}
    kept: list[RawRecord] = []
    for item in existing:
        record = _raw_record(item)
        identity = _record_identity(record)
        if identity is not None:
            old_records[identity] = record
            if identity[:2] in processed_messages:
                continue
        kept.append(item)
    for result in parsed_results:
        for record in result.target_records:
            updated = dict(record)
            identity = _record_identity(updated)
            if identity is not None and identity in old_records:
                original_published_at = old_records[identity].get("published_at")
                if original_published_at:
                    updated["published_at"] = original_published_at
            kept.append({"query": f"telegram:{result.message.channel_username}", "record": updated})
    return sorted(kept, key=_raw_sort_key)


def _replace_failures(
    existing: list[dict[str, Any]], parsed_results: list[TelegramParseResult]
) -> list[dict[str, Any]]:
    processed = {(result.message.channel_id, result.message.message_id) for result in parsed_results}
    kept = [item for item in existing if _failure_identity(item) not in processed]
    for result in parsed_results:
        if result.failure_reason is None:
            continue
        message = result.message
        kept.append(
            {
                "channel_username": message.channel_username,
                "channel_id": message.channel_id,
                "message_id": message.message_id,
                "message_url": message.message_url,
                "published_at": message.published_at.isoformat(),
                "edited_at": message.edited_at.isoformat() if message.edited_at else None,
                "reason": result.failure_reason,
            }
        )
    return sorted(kept, key=_failure_sort_key)


def _source_cutoff(
    checkpoint: Any,
    now: datetime,
    *,
    since_days: int,
    lookback_hours: int,
) -> datetime:
    initial_cutoff = now - timedelta(days=since_days)
    if not isinstance(checkpoint, dict):
        return initial_cutoff
    checkpoint_date = _parse_datetime(checkpoint.get("last_message_date"))
    if checkpoint_date is None:
        return initial_cutoff
    return checkpoint_date - timedelta(hours=lookback_hours)


def _updated_checkpoint(existing: Any, messages: list[TelegramMessage], now: datetime) -> dict[str, Any]:
    newest = max(messages, key=lambda item: (item.published_at, item.message_id))
    existing_date = _parse_datetime(existing.get("last_message_date")) if isinstance(existing, dict) else None
    if existing_date and existing_date > newest.published_at:
        last_date = existing_date
        last_id = existing.get("last_message_id")
    elif existing_date and existing_date == newest.published_at:
        last_date = existing_date
        last_id = max(_safe_int(existing.get("last_message_id")), newest.message_id)
    else:
        last_date = newest.published_at
        last_id = newest.message_id
    return {
        "last_message_id": last_id,
        "last_message_date": last_date.isoformat(),
        "last_successful_fetch_at": now.isoformat(),
    }


def source_reports_as_dict(result: TelegramFetchResult) -> dict[str, dict[str, Any]]:
    return {name: asdict(report) for name, report in result.source_reports.items()}


def _load_list(path: Any) -> list[Any]:
    if not path.exists():
        return []
    value = read_json(path, [])
    return value if isinstance(value, list) else []


def _load_mapping(path: Any) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = read_json(path, {})
    return value if isinstance(value, dict) else {}


def _raw_record(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    record = item.get("record", item)
    return record if isinstance(record, dict) else {}


def _record_identity(record: dict[str, Any]) -> tuple[int, int, int] | None:
    try:
        return int(record["channel_id"]), int(record["message_id"]), int(record.get("vacancy_index", 0))
    except (KeyError, TypeError, ValueError):
        return None


def _raw_sort_key(item: Any) -> tuple[str, int, int]:
    record = _raw_record(item)
    return (
        str(record.get("channel_username", "")),
        _safe_int(record.get("message_id")),
        _safe_int(record.get("vacancy_index")),
    )


def _failure_identity(item: Any) -> tuple[int, int] | None:
    if not isinstance(item, dict):
        return None
    try:
        return int(item["channel_id"]), int(item["message_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _failure_sort_key(item: Any) -> tuple[str, int]:
    if not isinstance(item, dict):
        return "", 0
    return str(item.get("channel_username", "")), _safe_int(item.get("message_id"))


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
