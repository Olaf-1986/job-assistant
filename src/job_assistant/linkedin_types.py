from __future__ import annotations

from dataclasses import dataclass


class LinkedInFetchError(RuntimeError):
    """A recoverable per-vacancy LinkedIn fetch or extraction failure."""


class LinkedInStopRun(LinkedInFetchError):
    """A run-level LinkedIn barrier that must stop processing immediately."""

    def __init__(self, reason: str, *, signal: str | None = None, detail: str | None = None) -> None:
        self.reason = reason
        self.signal = signal
        self.detail = detail
        message = reason if not detail else f"{reason}: {detail}"
        super().__init__(message)


@dataclass
class LinkedInVacancy:
    job_id: str
    canonical_url: str
    title: str
    company: str | None
    location: str | None
    description: str
    document_title: str
    requirements_text: str | None = None


@dataclass(frozen=True)
class LinkedInVisibleBlock:
    tag: str
    role: str | None
    aria_level: int | None
    text: str


@dataclass
class LinkedInPageContent:
    page_url: str
    body_text: str
    main_text: str
    document_title: str
    visible_blocks: tuple[LinkedInVisibleBlock, ...] = ()
