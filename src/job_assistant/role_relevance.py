from __future__ import annotations

from .filters import contains_phrase


def title_prefilter_matches(title: str, keywords: list[str]) -> bool:
    return any(contains_phrase(title, keyword) for keyword in keywords)
