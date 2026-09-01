from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Preferences
from .deduplicate import deduplicate_vacancies, vacancies_match
from .filters import apply_filters
from .models import NormalizedVacancy
from .normalize import normalize_records
from .paths import output_paths
from .persistence import read_headhunter_raw, read_linkedin_manual_raw
from .scoring import score_vacancies
from .telegram_client import TelegramError, TelegramMessage, TelegramRateLimitError, build_telegram_reader
from .telegram_ingestion import TelegramReader
from .telegram_parser import TelegramParseResult, parse_telegram_message
from .utils import utc_now, write_json


@dataclass
class TelegramAuditMetrics:
    source: str
    messages_read: int = 0
    vacancy_like_messages: int = 0
    successfully_parsed_vacancies: int = 0
    target_role_matches: int = 0
    relevant_vacancies: int = 0
    unique_relevant_vacancies: int = 0
    rejected_non_vacancy_messages: int = 0
    parse_failures: int = 0
    most_recent_message_date: str | None = None
    error: str | None = None


@dataclass
class PairwiseContainment:
    source: str
    other_source: str
    duplicate_count: int
    valid_vacancies: int
    containment: float | None
    other_publishes_no_later: bool | None


@dataclass
class TelegramAuditResult:
    generated_at: str
    window_days: int
    sources: dict[str, TelegramAuditMetrics]
    pairwise_containment: list[PairwiseContainment]
    removal_review_candidates: list[dict[str, str]] = field(default_factory=list)
    it_job_offers_assessment: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "window_days": self.window_days,
            "sources": {name: asdict(metrics) for name, metrics in self.sources.items()},
            "pairwise_containment": [asdict(item) for item in self.pairwise_containment],
            "removal_review_candidates": self.removal_review_candidates,
            "it_job_offers_assessment": self.it_job_offers_assessment,
        }


async def audit_telegram(
    preferences: Preferences,
    *,
    since_days: int = 3,
    reader: TelegramReader | None = None,
    now: datetime | None = None,
    sleep: Any = asyncio.sleep,
    persist: bool = True,
) -> TelegramAuditResult:
    """Audit the current and preceding windows without importing vacancies or changing checkpoints."""
    if since_days < 1:
        raise ValueError("since_days must be at least 1")
    config = preferences.sources.telegram
    if not config.enabled:
        raise TelegramError("Telegram source is disabled in configuration")
    current_time = _as_utc(now or utc_now())
    current_start = current_time - timedelta(days=since_days)
    previous_start = current_time - timedelta(days=since_days * 2)
    source_names = config.enabled_sources
    active_reader = reader or build_telegram_reader(preferences)
    all_messages: dict[str, list[TelegramMessage]] = {source: [] for source in source_names}
    source_errors: dict[str, str] = {}

    async with active_reader:
        resolved = await active_reader.resolve_joined_sources(source_names)
        for index, source in enumerate(source_names):
            if source not in resolved:
                source_errors[source] = "configured source is not present in the account's joined dialogs"
                continue
            try:
                all_messages[source] = await active_reader.read_messages(
                    source, resolved[source], previous_start, limit=None
                )
            except TelegramError as exc:
                source_errors[source] = str(exc)
                if isinstance(exc, TelegramRateLimitError):
                    for skipped_source in source_names[index + 1 :]:
                        source_errors[skipped_source] = (
                            "not processed because an earlier source hit a Telegram rate limit"
                        )
                    break
            except Exception:
                source_errors[source] = "unexpected Telegram source audit failure"
            if index < len(source_names) - 1:
                await sleep(config.source_pause_seconds)

    current_results = _window_results(all_messages, preferences, current_start, current_time)
    previous_results = _window_results(all_messages, preferences, previous_start, current_start)
    current = _window_analysis(preferences, source_names, current_results, source_errors)
    previous = _window_analysis(preferences, source_names, previous_results, source_errors)
    current_metrics, current_containment, current_relevant = current
    previous_metrics, previous_containment, _ = previous
    external_relevant = _existing_relevant_vacancies(preferences)
    _populate_unique_relevant(current_metrics, current_relevant, external_relevant)
    removal_window_eligible = since_days == 3
    recommendations = (
        _removal_review_candidates(
            source_names,
            current_metrics,
            previous_metrics,
            current_containment,
            previous_containment,
        )
        if removal_window_eligible
        else []
    )
    assessment = _it_job_offers_assessment(
        current_metrics.get("it_job_offers"), recommendations, removal_window_eligible=removal_window_eligible
    )
    result = TelegramAuditResult(
        generated_at=current_time.isoformat(),
        window_days=since_days,
        sources=current_metrics,
        pairwise_containment=list(current_containment.values()),
        removal_review_candidates=recommendations,
        it_job_offers_assessment=assessment,
    )
    if persist:
        write_json(output_paths(preferences)["telegram_audit"], result.as_dict())
    return result


def _window_results(
    messages: dict[str, list[TelegramMessage]],
    preferences: Preferences,
    start: datetime,
    end: datetime,
) -> dict[str, list[TelegramParseResult]]:
    return {
        source: [
            parse_telegram_message(message, preferences)
            for message in source_messages
            if start <= _as_utc(message.published_at) < end
        ]
        for source, source_messages in messages.items()
    }


def _window_analysis(
    preferences: Preferences,
    source_names: list[str],
    results: dict[str, list[TelegramParseResult]],
    source_errors: dict[str, str],
) -> tuple[
    dict[str, TelegramAuditMetrics],
    dict[tuple[str, str], PairwiseContainment],
    dict[str, list[NormalizedVacancy]],
]:
    metrics: dict[str, TelegramAuditMetrics] = {}
    valid: dict[str, list[NormalizedVacancy]] = {}
    relevant: dict[str, list[NormalizedVacancy]] = {}
    for source in source_names:
        parsed = results.get(source, [])
        candidates = [candidate for item in parsed for candidate in item.candidates]
        metrics[source] = TelegramAuditMetrics(
            source=source,
            messages_read=len(parsed),
            vacancy_like_messages=sum(item.vacancy_like for item in parsed),
            successfully_parsed_vacancies=len(candidates),
            target_role_matches=sum(candidate.target_role_match for candidate in candidates),
            rejected_non_vacancy_messages=sum(item.rejection_reason is not None for item in parsed),
            parse_failures=sum(item.failure_reason is not None for item in parsed),
            most_recent_message_date=max((item.message.published_at for item in parsed), default=None).isoformat()
            if parsed
            else None,
            error=source_errors.get(source),
        )
        valid[source] = _normalize_candidates(preferences, [candidate.record for candidate in candidates])
        target_records = [candidate.record for candidate in candidates if candidate.target_role_match]
        target_vacancies = _normalize_candidates(preferences, target_records)
        filtered = score_vacancies(apply_filters(target_vacancies, preferences), preferences)
        relevant[source] = _deduped(
            [
                vacancy
                for vacancy in filtered
                if not vacancy.blocker
                and not vacancy.requires_manual_location_review
                and not vacancy.requires_manual_role_review
            ]
        )
        metrics[source].relevant_vacancies = len(relevant[source])
    containment: dict[tuple[str, str], PairwiseContainment] = {}
    for source in source_names:
        for other in source_names:
            if source == other:
                continue
            duplicates = [
                vacancy
                for vacancy in valid[source]
                if any(vacancies_match(vacancy, candidate) for candidate in valid[other])
            ]
            no_later = _other_publishes_no_later(duplicates, valid[other])
            containment[(source, other)] = PairwiseContainment(
                source=source,
                other_source=other,
                duplicate_count=len(duplicates),
                valid_vacancies=len(valid[source]),
                containment=round(len(duplicates) / len(valid[source]), 4) if valid[source] else None,
                other_publishes_no_later=no_later,
            )
    return metrics, containment, relevant


def _normalize_candidates(preferences: Preferences, records: list[dict[str, object]]) -> list[NormalizedVacancy]:
    raw = [
        {"query": f"telegram:{record.get('channel_username', 'unknown')}", "record": dict(record)} for record in records
    ]
    return normalize_records(raw, preferences)


def _existing_relevant_vacancies(preferences: Preferences) -> list[NormalizedVacancy]:
    raw = [*read_headhunter_raw(preferences), *read_linkedin_manual_raw(preferences)]
    filtered = score_vacancies(apply_filters(normalize_records(raw, preferences), preferences), preferences)
    return _deduped(
        [
            vacancy
            for vacancy in filtered
            if not vacancy.blocker
            and not vacancy.requires_manual_location_review
            and not vacancy.requires_manual_role_review
        ]
    )


def _populate_unique_relevant(
    metrics: dict[str, TelegramAuditMetrics],
    relevant: dict[str, list[NormalizedVacancy]],
    external: list[NormalizedVacancy],
) -> None:
    for source, vacancies in relevant.items():
        others = [
            vacancy
            for other_source, other_vacancies in relevant.items()
            if other_source != source
            for vacancy in other_vacancies
        ]
        metrics[source].unique_relevant_vacancies = sum(
            not any(vacancies_match(vacancy, other) for other in [*others, *external]) for vacancy in vacancies
        )


def _removal_review_candidates(
    source_names: list[str],
    current_metrics: dict[str, TelegramAuditMetrics],
    previous_metrics: dict[str, TelegramAuditMetrics],
    current: dict[tuple[str, str], PairwiseContainment],
    previous: dict[tuple[str, str], PairwiseContainment],
) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    for source in source_names:
        for other in source_names:
            if source == other:
                continue
            current_pair = current[(source, other)]
            previous_pair = previous[(source, other)]
            if not (
                current_pair.containment is not None
                and current_pair.containment >= 0.8
                and previous_pair.containment is not None
                and previous_pair.containment >= 0.8
                and current_pair.other_publishes_no_later is True
                and previous_pair.other_publishes_no_later is True
                and current_metrics[other].relevant_vacancies >= current_metrics[source].relevant_vacancies
                and previous_metrics[other].relevant_vacancies >= previous_metrics[source].relevant_vacancies
            ):
                continue
            recommendations.append(
                {
                    "source": source,
                    "contained_by": other,
                    "recommendation": "review removal; no configuration was changed",
                }
            )
    return recommendations


def _it_job_offers_assessment(
    metrics: TelegramAuditMetrics | None,
    recommendations: list[dict[str, str]],
    *,
    removal_window_eligible: bool,
) -> dict[str, Any]:
    if metrics is None:
        return {
            "configured": False,
            "active_in_window": False,
            "produces_unique_relevant_vacancies": False,
            "recommendation": "not configured",
        }
    removal = next((item for item in recommendations if item["source"] == "it_job_offers"), None)
    if removal:
        recommendation = f"review removal against {removal['contained_by']}; keep enabled until user review"
    elif not removal_window_eligible:
        recommendation = "keep trial enabled; removal recommendations require consecutive three-day windows"
    elif metrics.messages_read == 0:
        recommendation = "keep trial enabled; the current window has insufficient activity evidence"
    elif metrics.unique_relevant_vacancies > 0:
        recommendation = "keep enabled; it contributes relevant unique vacancies"
    else:
        recommendation = "keep trial enabled; two-window removal criteria were not met"
    return {
        "configured": True,
        "active_in_window": metrics.messages_read > 0,
        "produces_unique_relevant_vacancies": metrics.unique_relevant_vacancies > 0,
        "recommendation": recommendation,
    }


def _other_publishes_no_later(duplicates: list[NormalizedVacancy], candidates: list[NormalizedVacancy]) -> bool | None:
    if not duplicates:
        return None
    for vacancy in duplicates:
        matches = [candidate for candidate in candidates if vacancies_match(vacancy, candidate)]
        if not matches or vacancy.publication_date is None:
            return False
        dates = [candidate.publication_date for candidate in matches if candidate.publication_date is not None]
        if not dates or min(dates) > vacancy.publication_date:
            return False
    return True


def _deduped(vacancies: list[NormalizedVacancy]) -> list[NormalizedVacancy]:
    copies = [vacancy.model_copy(deep=True) for vacancy in vacancies]
    return deduplicate_vacancies(copies)[0]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
