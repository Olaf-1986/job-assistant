from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .config import Preferences
from .linkedin_types import LinkedInStopRun
from .paths import output_paths
from .utils import read_json, write_json

RATE_LIMIT_LOCAL_BLOCK = timedelta(minutes=2)
_KNOWN_SIGNALS = {"http_429", "page_marker", "unknown"}


def read_linkedin_rate_limit_state(preferences: Preferences, *, now: datetime | None = None) -> dict[str, Any] | None:
    path = output_paths(preferences)["linkedin_rate_limit_state"]
    if not path.exists():
        return None
    data = read_json(path, None)
    if not isinstance(data, dict):
        return None
    retry_after = _parse_timestamp(data.get("retry_after"))
    current = _as_utc(now or datetime.now(UTC))
    return {
        "reason": "rate_limited",
        "signal": data.get("signal") if data.get("signal") in _KNOWN_SIGNALS else "unknown",
        "blocked_at": data.get("blocked_at"),
        "retry_after": data.get("retry_after"),
        "active": retry_after is not None and current < retry_after,
    }


def record_linkedin_rate_limit(
    preferences: Preferences, *, signal: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    blocked_at = _as_utc(now or datetime.now(UTC))
    retry_after = blocked_at + RATE_LIMIT_LOCAL_BLOCK
    state = {
        "reason": "rate_limited",
        "signal": signal if signal in _KNOWN_SIGNALS else "unknown",
        "blocked_at": blocked_at.isoformat(),
        "retry_after": retry_after.isoformat(),
    }
    write_json(output_paths(preferences)["linkedin_rate_limit_state"], state)
    return {**state, "active": True}


def stop_if_linkedin_rate_limited_locally(preferences: Preferences, *, now: datetime | None = None) -> None:
    state = read_linkedin_rate_limit_state(preferences, now=now)
    if not state or not state["active"]:
        return
    raise LinkedInStopRun(
        "rate_limited",
        signal="local_state",
        detail=f"local block active until {state['retry_after']}; start a separate run after that time",
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
