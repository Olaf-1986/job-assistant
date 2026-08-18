from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

from .config import Preferences, load_target_companies
from .models import SourceStatus
from .utils import read_json, utc_now, write_json


SOURCE_NAMES = ["headhunter", "email", "linkedin", "arbeitnow", "jooble", "jobicy", "greenhouse", "lever"]
SOURCE_PRIORITIES = {
    "headhunter": "primary",
    "email": "read_only_alert_ingestion",
    "linkedin": "manual_current_page_only",
    "arbeitnow": "disabled",
    "jooble": "disabled",
    "jobicy": "disabled",
    "greenhouse": "disabled",
    "lever": "disabled",
}


def status_path(preferences: Preferences) -> Path:
    return preferences.outputs.output_dir() / preferences.outputs.source_status_file


def load_statuses(preferences: Preferences) -> dict[str, SourceStatus]:
    path = status_path(preferences)
    saved: dict[str, Any] = read_json(path) if path.exists() else {}
    statuses: dict[str, SourceStatus] = {}
    companies = load_target_companies()
    for source in SOURCE_NAMES:
        config = getattr(preferences.sources, source, None)
        if config is None:
            continue
        item = saved.get(source, {}) if isinstance(saved, dict) else {}
        required = getattr(config, "requires_env", []) or []
        credential_status = "present" if required and all(os.getenv(name) for name in required) else "missing" if required else "not_required"
        if source == "headhunter" and getattr(config, "require_authentication", False):
            token_env = str(getattr(config, "access_token_env", "HH_ACCESS_TOKEN"))
            credential_status = "present" if os.getenv(token_env, "").strip() else "authentication_missing"
        company_list_status = "not_applicable"
        if source in {"greenhouse", "lever"}:
            entries = getattr(companies, source).get("companies", [])
            enabled_entries = [entry for entry in entries if entry.enabled]
            company_list_status = "ready" if enabled_entries else "empty_company_list"
        configured_search_count = 0
        statuses[source] = SourceStatus(
            source=source,
            enabled=bool(getattr(config, "enabled", False)),
            mode=str(getattr(config, "mode", "unknown")),
            credential_status=credential_status,
            company_list_status=company_list_status,
            last_successful_run=item.get("last_successful_run"),
            last_error=item.get("last_error"),
            status=item.get("status", "configured"),
            records=int(item.get("records", 0) or 0),
            requests_made=int(item.get("requests_made", 0) or 0),
            priority=SOURCE_PRIORITIES.get(source, "secondary"),
            raw_count=int(item.get("raw_count", item.get("records", 0)) or 0),
            role_relevant_count=int(item.get("role_relevant_count", 0) or 0),
            shortlist_count=int(item.get("shortlist_count", 0) or 0),
            relevant_ratio=item.get("relevant_ratio"),
            last_ten_run_ratios=list(item.get("last_ten_run_ratios", []) or []),
            card_prefilter_count=int(item.get("card_prefilter_count", 0) or 0),
            opened_vacancy_count=int(item.get("opened_vacancy_count", 0) or 0),
            manual_review_count=int(item.get("manual_review_count", 0) or 0),
            configured_search_count=configured_search_count or int(item.get("configured_search_count", 0) or 0),
        )
    return statuses


def write_statuses(preferences: Preferences, statuses: dict[str, SourceStatus]) -> None:
    write_json(status_path(preferences), {name: status.model_dump(mode="json") for name, status in statuses.items()})


def already_succeeded_today(status: SourceStatus) -> bool:
    if not status.last_successful_run:
        return False
    return status.last_successful_run[:10] == date.today().isoformat()


def mark_status(preferences: Preferences, source: str, status: str, records: int = 0, requests_made: int = 0, error: str | None = None, role_relevant_count: int = 0, shortlist_count: int = 0, card_prefilter_count: int = 0, opened_vacancy_count: int = 0, manual_review_count: int = 0) -> SourceStatus:
    statuses = load_statuses(preferences)
    current = statuses[source]
    current.status = status
    current.records = records
    current.raw_count = records
    current.requests_made = requests_made
    current.role_relevant_count = role_relevant_count
    current.shortlist_count = shortlist_count
    current.card_prefilter_count = card_prefilter_count
    current.opened_vacancy_count = opened_vacancy_count
    current.manual_review_count = manual_review_count
    current.relevant_ratio = round(role_relevant_count / records, 4) if records else None
    if records:
        current.last_ten_run_ratios = [*current.last_ten_run_ratios, current.relevant_ratio or 0.0][-10:]
    current.last_error = error
    if status == "success":
        current.last_successful_run = utc_now().isoformat()
    statuses[source] = current
    write_statuses(preferences, statuses)
    return current
