from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NormalizedVacancy(BaseModel):
    source: str
    source_id: str | None = None
    source_url: str | None = None
    apply_url: str | None = None
    source_queries: list[str] = Field(default_factory=list)
    source_company_identifier: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    source_ids: dict[str, list[str]] = Field(default_factory=dict)
    title: str
    normalized_title: str
    company: str | None = None
    company_slug: str | None = None
    description_html: str | None = None
    description_text: str | None = None
    excerpt: str | None = None
    employment_type: str | None = None
    seniority: str | None = None
    categories: list[str] = Field(default_factory=list)
    location_restrictions: list[str] = Field(default_factory=list)
    timezone_restrictions: list[str] = Field(default_factory=list)
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    publication_date: datetime | None = None
    expiry_date: datetime | None = None
    application_url: str | None = None
    description_completeness: str = "complete"
    imported_manually: bool = False
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    fetched_at: datetime
    detected_language: str | None = None
    work_mode: str | None = None
    detected_city: str | None = None
    blocker: bool = False
    blocker_reasons: list[str] = Field(default_factory=list)
    role_relevance_breakdown: list[str] = Field(default_factory=list)
    requires_manual_role_review: bool = False
    score: int = 0
    score_breakdown: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw_application_urls: list[str] = Field(default_factory=list)
    international_remote_eligibility: str | None = None
    international_remote_evidence: list[str] = Field(default_factory=list)
    requires_manual_location_review: bool = False


class BatchStats(BaseModel):
    requests_made: int = 0
    raw_records_received: int = 0
    duplicates_merged: int = 0
    errors: list[str] = Field(default_factory=list)
    role_relevant_count: int = 0
    shortlist_count: int = 0
    card_prefilter_count: int = 0
    opened_vacancy_count: int = 0
    manual_review_count: int = 0


class SourceStatus(BaseModel):
    source: str
    enabled: bool
    mode: str
    credential_status: str = "not_required"
    company_list_status: str = "not_applicable"
    last_successful_run: str | None = None
    last_error: str | None = None
    status: str = "unknown"
    records: int = 0
    requests_made: int = 0
    priority: str = "secondary"
    raw_count: int = 0
    role_relevant_count: int = 0
    shortlist_count: int = 0
    relevant_ratio: float | None = None
    last_ten_run_ratios: list[float] = Field(default_factory=list)
    card_prefilter_count: int = 0
    opened_vacancy_count: int = 0
    manual_review_count: int = 0
    configured_search_count: int = 0


RawRecord = dict[str, Any]
