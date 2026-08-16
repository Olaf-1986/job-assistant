from __future__ import annotations

from .config import Preferences


def detect_work_mode(text: str, preferences: Preferences) -> str:
    lowered = text.lower()
    if any(_contains_work_mode_keyword(lowered, keyword) for keyword in preferences.location.hybrid_keywords):
        return "hybrid"
    if any(_contains_work_mode_keyword(lowered, keyword) for keyword in preferences.location.onsite_keywords):
        return "onsite"
    if any(_contains_work_mode_keyword(lowered, keyword) for keyword in preferences.location.remote_keywords):
        return "remote"
    return "unknown"


def _contains_work_mode_keyword(text: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    return keyword in text


def detect_allowed_city(text: str, preferences: Preferences) -> str | None:
    lowered = text.lower()
    for city in preferences.location.onsite_hybrid_allowed_cities:
        if city.lower() in lowered:
            return city
    return None
