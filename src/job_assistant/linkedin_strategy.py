from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import Preferences
from .linkedin_queue import PENDING_STATUSES
from .role_relevance import title_prefilter_matches
from .utils import normalize_space

_QUEUE_REMOTE_MARKERS = (
    "remote",
    "remotely",
    "work from home",
    "distributed",
    "worldwide",
    "global",
    "удаленно",
    "удалённо",
)
_QUEUE_REGIONAL_MARKERS = ("emea", "europe", "european", "middle east", "africa")
_QUEUE_LOCATION_PREFILTER_REASON = "explicit onsite or hybrid outside Tbilisi"


def linkedin_location_prefilter_reason(item: dict[str, Any], preferences: Preferences) -> str | None:
    """Reject only explicit onsite/hybrid metadata outside the configured allowed city."""
    location = normalize_space(str(item.get("location") or ""))
    if not location:
        return None
    lowered = location.casefold()
    work_mode_markers = [*preferences.location.onsite_keywords, *preferences.location.hybrid_keywords]
    explicit_onsite_or_hybrid = any(marker.casefold() in lowered for marker in work_mode_markers if marker.strip())
    allowed_city = any(city.casefold() in lowered for city in preferences.location.onsite_hybrid_allowed_cities)
    if explicit_onsite_or_hybrid and not allowed_city:
        return _QUEUE_LOCATION_PREFILTER_REASON
    return None


def linkedin_queue_priority(item: dict[str, Any], preferences: Preferences, original_index: int) -> tuple[Any, ...]:
    """Return a stable, conservative queue ordering key."""
    location = normalize_space(str(item.get("location") or "")).casefold()
    allowed_city = any(city.casefold() in location for city in preferences.location.onsite_hybrid_allowed_cities)
    remote_markers = (*preferences.location.remote_keywords, *_QUEUE_REMOTE_MARKERS)
    remote = any(marker.casefold() in location for marker in remote_markers if marker.strip())
    regional = any(marker in location for marker in _QUEUE_REGIONAL_MARKERS)
    if allowed_city:
        location_rank = 0
    elif remote:
        location_rank = 1
    elif regional:
        location_rank = 2
    else:
        location_rank = 3

    title = str(item.get("title") or "")
    matching_title_lengths = [
        len(keyword)
        for keyword in preferences.role_relevance.relevant_title_keywords
        if title_prefilter_matches(title, [keyword])
    ]
    title_rank = -max(matching_title_lengths, default=0)
    received_at = str(item.get("received_at") or "")
    try:
        received_timestamp = datetime.fromisoformat(received_at.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        received_timestamp = 0.0
    return (location_rank, title_rank, -received_timestamp, original_index)


def pending_linkedin_candidates(
    candidates: list[dict[str, Any]], preferences: Preferences, prioritize: bool
) -> list[dict[str, Any]]:
    pending = [
        (index, item)
        for index, item in enumerate(candidates)
        if isinstance(item, dict)
        and item.get("source") == "linkedin"
        and str(item.get("status") or "") in PENDING_STATUSES
    ]
    if prioritize:
        pending.sort(key=lambda pair: linkedin_queue_priority(pair[1], preferences, pair[0]))
    return [item for _, item in pending]
