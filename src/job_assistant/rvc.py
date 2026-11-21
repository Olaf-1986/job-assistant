from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from .config import Preferences
from .connectors.rvc import RvcClient, RvcSearchResult
from .deduplicate import vacancies_match
from .export import sorted_shortlist
from .normalize import normalize_records
from .persistence import prepare_authoritative_vacancies, rebuild_from_authoritative_sources
from .rvc_store import RvcEntry, RvcError, RvcReview, RvcRun, load_rvc_store, save_rvc_store
from .sources import mark_status
from .utils import utc_now


def discover_rvc(
    preferences: Preferences,
    *,
    query: str = "Business Analyst",
    country: str = "GE",
    worldwide: bool = False,
    since_days: int = 14,
    limit: int = 10,
    dry_run: bool = False,
    client: RvcClient | None = None,
    now: datetime | None = None,
) -> tuple[RvcSearchResult, int]:
    if not preferences.sources.rvc.enabled:
        raise RvcError("RVC is disabled in configuration")
    if not 1 <= limit <= 10 or not 1 <= since_days <= 90:
        raise RvcError("RVC limit must be 1–10 and since-days 1–90")
    query = query.strip()
    if not 2 <= len(query) <= 500 or not re.fullmatch(r"[A-Za-z]{2}", country):
        raise RvcError("Use a specific role and a two-letter country code")
    current = (now or utc_now()).astimezone(UTC)
    store = load_rvc_store(preferences)
    countries = [] if worldwide else [country.upper()]
    arguments = {
        "query": query,
        "conversation_language_code": preferences.languages.accepted[0],
        "allow_english": "en" in preferences.languages.accepted,
        "additional_allowed_language_codes": preferences.languages.accepted[1:],
        "target_country_codes": countries,
        "posted_after": (current - timedelta(days=since_days)).date().isoformat(),
        "limit": limit,
    }
    if worldwide:
        arguments["work_arrangements"] = ["FULLY_REMOTE"]
    result = (client or RvcClient(preferences.run.request_timeout_seconds)).search(arguments)
    if len(result.results) > limit:
        raise RvcError("RVC exceeded the requested result limit; data retained unchanged")
    new_count = 0
    for card in result.results:
        if card.masked_id in store.entries:
            entry = store.entries[card.masked_id]
            entry.card = card
            entry.last_seen_at = current
        else:
            new_count += 1
            store.entries[card.masked_id] = RvcEntry(card=card, first_seen_at=current, last_seen_at=current)
    store.runs.append(
        RvcRun(
            at=current,
            query=query,
            countries=countries,
            since_days=since_days,
            returned=len(result.results),
            new_candidates=new_count,
            total_available=result.total_available_count,
            truncated=result.truncated,
        )
    )
    if not dry_run:
        save_rvc_store(preferences, store)
        mark_status(preferences, "rvc", "success", records=len(result.results), requests_made=3)
    return result, new_count


def review_rvc(
    preferences: Preferences,
    masked_id: str,
    *,
    review: RvcReview | None = None,
    status: Literal["reviewed", "rejected", "expired"] = "reviewed",
    minutes: float | None = None,
    include_seen: bool = False,
    now: datetime | None = None,
) -> dict:
    store = load_rvc_store(preferences)
    if masked_id not in store.entries:
        raise RvcError("RVC candidate is absent from the discovery queue")
    if (minutes is not None and (not math.isfinite(minutes) or minutes < 0)) or status not in {
        "reviewed",
        "rejected",
        "expired",
    }:
        raise RvcError("Invalid review status or duration")
    current = (now or utc_now()).astimezone(UTC)
    entry = store.entries[masked_id]
    if status == "reviewed":
        if review is None:
            raise RvcError("A completed review file is required")
        if review.published_at > current:
            raise RvcError("Publication date must not be in the future")
        first_review = entry.review is None
        entry.review = review
        entry.status = status
        entry.reviewed_at = current
        if first_review:
            raw = entry.raw_record()
            vacancy = normalize_records([raw], preferences)[0]
            _, existing, _ = prepare_authoritative_vacancies(preferences, include_rvc=False)
            entry.existing_sources_at_review = sorted(
                {source for old in existing if vacancies_match(vacancy, old) for source in old.sources}
            )
    elif review is not None:
        raise RvcError("Choose a review file or a rejection/expiry status")
    entry.status = status
    entry.reviewed_at = current
    # Duration is the total spent on this candidate, so rerunning the same review is idempotent.
    if minutes is not None:
        entry.review_minutes = minutes
    save_rvc_store(preferences, store)
    # The authoritative review is saved first; a failed export can be retried safely.
    return rebuild_from_authoritative_sources(preferences, include_seen=include_seen)


def audit_rvc(preferences: Preferences, *, now: datetime | None = None) -> dict:
    """Local pilot metrics; no remote calls and no global LinkedIn coverage claims."""
    current = (now or utc_now()).astimezone(UTC)
    store = load_rvc_store(preferences)
    _, combined, _ = prepare_authoritative_vacancies(preferences, compare_history=False)
    attributed = [v for v in combined if "rvc" in v.sources]
    eligible = sorted_shortlist(attributed, len(attributed))
    exclusive = [v for v in eligible if set(v.sources) == {"rvc"}]
    overlap = [v for v in attributed if "linkedin" in v.sources]
    cross_source_duplicates = [v for v in attributed if set(v.sources) - {"rvc"}]
    pending = sum(e.status == "pending" for e in store.entries.values())
    start = min((run.at for run in store.runs), default=None)
    elapsed_days = max(0, (current - start).days) if start else 0
    if start is None:
        assessment = "not_started"
    elif elapsed_days < 14:
        assessment = "in_progress"
    elif len({run.at.date() for run in store.runs}) < 2 or pending:
        assessment = "needs_more_observations_or_reviews"
    elif len(exclusive) >= 3:
        assessment = "incremental_yield_threshold_met"
    else:
        assessment = "incremental_yield_below_three"
    return {
        "started_at": start.isoformat() if start else None,
        "evaluate_after": (start + timedelta(days=14)).isoformat() if start else None,
        "elapsed_days": elapsed_days,
        "successful_searches": len(store.runs),
        "truncated_searches": sum(run.truncated for run in store.runs),
        "discovered_candidates": len(store.entries),
        "pending_review": pending,
        "rejected": sum(e.status == "rejected" for e in store.entries.values()),
        "expired": sum(e.status == "expired" for e in store.entries.values()),
        "reviewed_with_description": sum(e.review is not None for e in store.entries.values()),
        "eligible_after_shared_pipeline": len(eligible),
        "eligible_absent_from_other_current_stores": len(exclusive),
        "matches_in_saved_linkedin": len(overlap),
        "cross_source_duplicate_vacancies": len(cross_source_duplicates),
        "reviewed_duplicate_fraction": len(cross_source_duplicates) / len(attributed) if attributed else None,
        "blocked_after_shared_pipeline": sum(v.blocker for v in attributed),
        "review_minutes": sum(e.review_minutes for e in store.entries.values()),
        "assessment": assessment,
        "scope": "Reviewed records versus local authoritative stores; pending cards and global LI overlap unresolved.",
    }
