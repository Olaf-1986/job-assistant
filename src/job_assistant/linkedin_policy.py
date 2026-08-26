from __future__ import annotations

import re
from urllib.parse import urlsplit

STOP_REASONS = {"login_required", "captcha", "rate_limited", "account_restricted"}
LINKEDIN_CLOSED_BLOCKER_REASON = "LinkedIn vacancy is no longer accepting applications"

_LOGIN_PATHS = {
    "/authwall",
    "/checkpoint",
    "/checkpoint/challenge",
    "/login",
    "/uas/login",
}
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "try again later",
    "you have reached the limit",
    "you've reached the limit",
    "temporarily blocked",
)
_NO_LONGER_ACCEPTING_MARKERS = (
    "no longer accepting",
    "job is no longer available",
    "this job is no longer available",
)


def has_linkedin_no_longer_accepting_marker(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _NO_LONGER_ACCEPTING_MARKERS)


def classify_linkedin_page(url: str, text: str) -> str | None:
    """Classify only explicit LinkedIn barriers or an unavailable job page."""
    lowered_text = text.casefold()
    lowered_url_path = urlsplit(url).path.rstrip("/").casefold() or "/"
    if "captcha" in lowered_text or "security verification" in lowered_text or "quick security check" in lowered_text:
        return "captcha"
    if lowered_url_path in _LOGIN_PATHS or re.search(r"\b(?:sign|log) in to linkedin\b", lowered_text):
        return "login_required"
    lowered = f"{lowered_url_path}\n{lowered_text}"
    if "account restricted" in lowered or "temporarily restricted" in lowered:
        return "account_restricted"
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return "rate_limited"
    if has_linkedin_no_longer_accepting_marker(lowered):
        return "expired"
    return None
