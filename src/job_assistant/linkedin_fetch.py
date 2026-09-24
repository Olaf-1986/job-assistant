"""LinkedIn queue orchestration with compatibility exports for the capture workflow.

The implementation is split into policy, DOM/browser, content extraction, and queue
strategy modules. This facade intentionally keeps the historical function names used
by the CLI, extension capture path, and tests.
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .capture import ManualCapturePayload, process_manual_capture
from .config import Preferences
from .export import sorted_shortlist
from .linkedin_browser import (
    _expand_show_more,
    _main_text_is_ready,
    _playwright_page_fetcher,
    _read_linkedin_text,
    _visible_blocks_from_page,
    _wait_for_linkedin_page,
    _wait_for_linkedin_page_or_stop,
    run_login,
)
from .linkedin_content import (
    _description_from_main_text,
    _requirements_text_from_content,
    extract_linkedin_vacancy,
)
from .linkedin_policy import LINKEDIN_CLOSED_BLOCKER_REASON, STOP_REASONS, classify_linkedin_page
from .linkedin_queue import (
    linkedin_job_id,
    load_queue,
    mark_linkedin_manual_expired,
    update_queue_processed,
    validate_linkedin_queue,
)
from .linkedin_rate_limit import record_linkedin_rate_limit, stop_if_linkedin_rate_limited_locally
from .linkedin_strategy import (
    linkedin_location_prefilter_reason as _linkedin_location_prefilter_reason,
)
from .linkedin_strategy import (
    pending_linkedin_candidates as _pending_linkedin_candidates,
)
from .linkedin_types import (
    LinkedInFetchError,
    LinkedInPageContent,
    LinkedInStopRun,
    LinkedInVacancy,
    LinkedInVisibleBlock,
)
from .models import NormalizedVacancy
from .role_relevance import title_prefilter_matches

MIN_PAGE_DELAY_SECONDS = 5
MAX_PAGE_DELAY_SECONDS = 20

__all__ = [
    "LinkedInFetchError",
    "LinkedInPageContent",
    "LinkedInStopRun",
    "LinkedInVacancy",
    "LinkedInVisibleBlock",
    "STOP_REASONS",
    "classify_linkedin_page",
    "extract_linkedin_vacancy",
    "fetch_pending_linkedin",
    "run_login",
    "_description_from_main_text",
    "_requirements_text_from_content",
    "_expand_show_more",
    "_main_text_is_ready",
    "_read_linkedin_text",
    "_visible_blocks_from_page",
    "_wait_for_linkedin_page",
    "_wait_for_linkedin_page_or_stop",
]


def fetch_pending_linkedin(
    preferences: Preferences,
    limit: int | None = 5,
    dry_run: bool = False,
    pause_seconds: float | None = None,
    page_fetcher: Callable[[str], LinkedInPageContent | tuple[str, str]] | None = None,
    location_prefilter: bool = False,
    prioritize: bool = False,
    pending_since_days: int | None = None,
    recheck_shortlist_size: int = 0,
    recheck_since_days: int | None = None,
    delay_sampler: Callable[[int, int], float] = random.randint,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Process pending queue items through the shared LinkedIn import pipeline."""
    if limit is not None and limit < 1:
        raise LinkedInFetchError("limit must be at least 1")
    if pause_seconds is not None and pause_seconds < 0:
        raise LinkedInFetchError("pause_seconds must be non-negative")
    if recheck_shortlist_size < 0:
        raise LinkedInFetchError("recheck_shortlist_size must be non-negative")
    if recheck_since_days is not None and not 1 <= recheck_since_days <= 90:
        raise LinkedInFetchError("recheck_since_days must be between 1 and 90")
    if pending_since_days is not None and not 1 <= pending_since_days <= 90:
        raise LinkedInFetchError("pending_since_days must be between 1 and 90")
    stop_if_linkedin_rate_limited_locally(preferences)
    close_fetcher = False
    if page_fetcher is None:
        page_fetcher = _playwright_page_fetcher(headless=False)
        close_fetcher = True
    try:
        candidates = load_queue(preferences)
        validate_linkedin_queue(candidates)
        stats: dict[str, Any] = {
            "opened": 0,
            "imported": 0,
            "title_prefilter_rejected": 0,
            # Keep the old key for callers that still consume the internal stats mapping.
            "skipped_title": 0,
            "location_prefilter_rejected": 0,
            "expired": 0,
            "extraction_failed": 0,
            "rechecked": 0,
            "recheck_expired": 0,
            "recheck_failed": 0,
            "pending_outside_window": 0,
            "errors": [],
        }
        processed = 0
        verified_job_ids: set[str] = set()
        previous_delay: float | None = None
        pending_candidates = _pending_linkedin_candidates(candidates, preferences, prioritize)
        if pending_since_days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=pending_since_days)
            within_window = [item for item in pending_candidates if _queue_item_on_or_after(item, cutoff)]
            stats["pending_outside_window"] = len(pending_candidates) - len(within_window)
            pending_candidates = within_window
        for item in pending_candidates:
            if limit is not None and processed >= limit:
                break
            job_id = str(item.get("external_id") or "")
            title = str(item.get("title") or "")
            if not _title_matches_prefilter(title, preferences):
                stats["title_prefilter_rejected"] += 1
                stats["skipped_title"] += 1
                processed += 1
                if not dry_run and job_id:
                    update_queue_processed(
                        preferences,
                        job_id,
                        {
                            "pipeline_outcome": "title_prefilter_rejected",
                            "prefilter_reasons": ["title_prefilter_rejected"],
                        },
                    )
                continue
            if location_prefilter:
                location_reason = _linkedin_location_prefilter_reason(item, preferences)
                if location_reason:
                    stats["location_prefilter_rejected"] += 1
                    processed += 1
                    if not dry_run and job_id:
                        update_queue_processed(
                            preferences,
                            job_id,
                            {
                                "pipeline_outcome": "location_prefilter_rejected",
                                "prefilter_reasons": [location_reason],
                            },
                        )
                    continue
            url = str(item.get("canonical_url") or "")
            stop_if_linkedin_rate_limited_locally(preferences)
            processed += 1
            try:
                result = page_fetcher(url)
                stats["opened"] += 1
                vacancy = _vacancy_from_page_result(result, title, url)
                if job_id:
                    verified_job_ids.add(job_id)
                if dry_run:
                    stats["imported"] += 1
                else:
                    _import_vacancy(preferences, vacancy)
                    stats["imported"] += 1
            except LinkedInStopRun as exc:
                _raise_recorded_linkedin_stop(preferences, exc)
            except LinkedInFetchError as exc:
                _record_fetch_failure(preferences, stats, job_id, str(exc), dry_run)
            previous_delay = _pause_between_linkedin_pages(
                previous_delay,
                pause_seconds=pause_seconds,
                delay_sampler=delay_sampler,
                sleeper=sleeper,
            )
        if recheck_shortlist_size:
            _recheck_linkedin_shortlist(
                preferences,
                stats,
                page_fetcher,
                recheck_shortlist_size,
                recheck_since_days,
                verified_job_ids,
                previous_delay,
                pause_seconds=pause_seconds,
                delay_sampler=delay_sampler,
                sleeper=sleeper,
                dry_run=dry_run,
            )
        if not dry_run and (stats["expired"] or stats["recheck_expired"]):
            from .persistence import rebuild_from_authoritative_sources

            rebuild_from_authoritative_sources(preferences)
        return stats
    finally:
        if close_fetcher and hasattr(page_fetcher, "close"):
            page_fetcher.close()


def _title_matches_prefilter(title: str, preferences: Preferences) -> bool:
    return title_prefilter_matches(title, preferences.role_relevance.relevant_title_keywords)


def _pause_between_linkedin_pages(
    previous_delay: float | None,
    *,
    pause_seconds: float | None,
    delay_sampler: Callable[[int, int], float],
    sleeper: Callable[[float], None],
) -> float:
    if pause_seconds is not None:
        delay = float(pause_seconds)
    else:
        delay = float(delay_sampler(MIN_PAGE_DELAY_SECONDS, MAX_PAGE_DELAY_SECONDS))
        if not MIN_PAGE_DELAY_SECONDS <= delay <= MAX_PAGE_DELAY_SECONDS:
            raise LinkedInFetchError("sampled LinkedIn page delay must be between 5 and 20 seconds")
        if previous_delay is not None and delay == previous_delay:
            delay = float(MIN_PAGE_DELAY_SECONDS if delay != MIN_PAGE_DELAY_SECONDS else MIN_PAGE_DELAY_SECONDS + 1)
    if delay:
        sleeper(delay)
    return delay


def _vacancy_from_page_result(
    result: LinkedInPageContent | tuple[str, str], candidate_title: str, fallback_url: str
) -> LinkedInVacancy:
    if isinstance(result, LinkedInPageContent):
        return extract_linkedin_vacancy(
            "",
            result.page_url,
            document_title=result.document_title,
            candidate_title=candidate_title,
            main_text=result.main_text,
            visible_blocks=result.visible_blocks,
        )
    html, document_title = result
    return extract_linkedin_vacancy(html, fallback_url, document_title=document_title, candidate_title=candidate_title)


def _record_fetch_failure(
    preferences: Preferences, stats: dict[str, Any], job_id: str, reason: str, dry_run: bool
) -> None:
    if reason == "expired":
        stats["expired"] += 1
        if not dry_run and job_id:
            update_queue_processed(
                preferences,
                job_id,
                {"pipeline_outcome": "expired", "blocker_reasons": [LINKEDIN_CLOSED_BLOCKER_REASON]},
            )
            mark_linkedin_manual_expired(preferences, job_id)
        return
    stats["extraction_failed"] += 1
    stats["errors"].append(reason)
    if not dry_run and job_id:
        update_queue_processed(
            preferences,
            job_id,
            {"pipeline_outcome": "extraction_failed", "blocker_reasons": [reason]},
        )


def _raise_recorded_linkedin_stop(preferences: Preferences, exc: LinkedInStopRun) -> None:
    if exc.reason != "rate_limited":
        raise exc
    try:
        state = record_linkedin_rate_limit(preferences, signal=exc.signal)
    except OSError as state_error:
        raise LinkedInStopRun(
            "rate_limited",
            signal=exc.signal,
            detail="local block state could not be persisted; resume only with a separate run",
        ) from state_error
    raise LinkedInStopRun(
        "rate_limited",
        signal=exc.signal,
        detail=f"local block active until {state['retry_after']}; resume with a separate run",
    ) from exc


def _import_vacancy(preferences: Preferences, vacancy: LinkedInVacancy) -> dict[str, object]:
    payload = ManualCapturePayload(
        page_url=vacancy.canonical_url,
        document_title=vacancy.document_title,
        visible_text=vacancy.description,
        requirements_text=vacancy.requirements_text,
        selected_text="",
        hostname="www.linkedin.com",
        captured_at=datetime.now(UTC).isoformat(),
        source_label="linkedin",
        vacancy_title=vacancy.title,
        company=vacancy.company,
    )
    return process_manual_capture(preferences, payload, auto_open_next=False)


def _recheck_linkedin_shortlist(
    preferences: Preferences,
    stats: dict[str, Any],
    page_fetcher: Callable[[str], LinkedInPageContent | tuple[str, str]],
    shortlist_size: int,
    since_days: int | None,
    verified_job_ids: set[str],
    previous_delay: float | None,
    *,
    pause_seconds: float | None,
    delay_sampler: Callable[[int, int], float],
    sleeper: Callable[[float], None],
    dry_run: bool,
) -> None:
    candidates = _linkedin_shortlist_candidates(preferences, since_days)
    confirmed = 0
    for vacancy in candidates:
        if confirmed >= shortlist_size:
            break
        job_id = _linkedin_vacancy_job_id(vacancy)
        if not job_id:
            continue
        if job_id in verified_job_ids:
            confirmed += 1
            continue
        stop_if_linkedin_rate_limited_locally(preferences)
        stats["rechecked"] += 1
        try:
            result = page_fetcher(f"https://www.linkedin.com/jobs/view/{job_id}")
            _vacancy_from_page_result(result, vacancy.title, f"https://www.linkedin.com/jobs/view/{job_id}")
            confirmed += 1
        except LinkedInStopRun as exc:
            _raise_recorded_linkedin_stop(preferences, exc)
        except LinkedInFetchError as exc:
            if str(exc) == "expired":
                stats["recheck_expired"] += 1
                if not dry_run:
                    mark_linkedin_manual_expired(preferences, job_id)
                    update_queue_processed(
                        preferences,
                        job_id,
                        {"pipeline_outcome": "expired", "blocker_reasons": [LINKEDIN_CLOSED_BLOCKER_REASON]},
                    )
            else:
                stats["recheck_failed"] += 1
                stats["errors"].append(f"recheck {job_id}: {exc}")
                confirmed += 1
        previous_delay = _pause_between_linkedin_pages(
            previous_delay,
            pause_seconds=pause_seconds,
            delay_sampler=delay_sampler,
            sleeper=sleeper,
        )


def _linkedin_shortlist_candidates(preferences: Preferences, since_days: int | None) -> list[NormalizedVacancy]:
    from .persistence import prepare_authoritative_vacancies

    _, all_vacancies, _ = prepare_authoritative_vacancies(preferences)
    vacancies = [vacancy for vacancy in all_vacancies if vacancy.source == "linkedin" or "linkedin" in vacancy.sources]
    if since_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        vacancies = [
            vacancy
            for vacancy in vacancies
            if vacancy.publication_date is None or _as_utc(vacancy.publication_date) >= cutoff
        ]
    return sorted_shortlist(vacancies, len(vacancies))


def _linkedin_vacancy_job_id(vacancy: NormalizedVacancy) -> str | None:
    urls = [
        vacancy.source_url,
        vacancy.apply_url,
        vacancy.application_url,
        *vacancy.source_urls,
        *vacancy.raw_application_urls,
    ]
    return next((job_id for url in urls if (job_id := linkedin_job_id(url)) is not None), None)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _queue_item_on_or_after(item: dict[str, Any], cutoff: datetime) -> bool:
    value = item.get("published_at") or item.get("received_at")
    if not isinstance(value, str) or not value.strip():
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return True
    return _as_utc(parsed) >= cutoff
