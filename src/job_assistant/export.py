from __future__ import annotations

import csv
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Preferences
from .errors import sanitize_error
from .models import BatchStats, NormalizedVacancy
from .paths import output_paths
from .utils import ensure_directory, write_json


def sorted_shortlist(vacancies: list[NormalizedVacancy], size: int) -> list[NormalizedVacancy]:
    eligible = [
        vacancy
        for vacancy in vacancies
        if not vacancy.blocker
        and not vacancy.requires_manual_location_review
        and not vacancy.requires_manual_role_review
    ]
    return sorted(
        eligible,
        key=lambda item: (item.score, item.publication_date.timestamp() if item.publication_date else 0.0),
        reverse=True,
    )[:size]


def export_all(
    raw_records: list[dict[str, Any]],
    vacancies: list[NormalizedVacancy],
    preferences: Preferences,
    stats: BatchStats,
    queries: list[str],
) -> dict[str, Any]:
    paths = output_paths(preferences)
    ensure_directory(paths["dir"])
    shortlist = sorted_shortlist(vacancies, preferences.run.shortlist_size)
    blocked = sorted([v for v in vacancies if v.blocker], key=lambda item: item.score, reverse=True)
    role_review = sorted(
        [v for v in vacancies if not v.blocker and v.requires_manual_role_review],
        key=lambda item: item.score,
        reverse=True,
    )
    eligible_count = sum(
        not v.blocker and not v.requires_manual_location_review and not v.requires_manual_role_review for v in vacancies
    )
    write_json(paths["raw"], raw_records)
    write_json(paths["normalized"], [vacancy.model_dump(mode="json") for vacancy in vacancies])
    write_json(paths["combined_json"], [vacancy.model_dump(mode="json") for vacancy in vacancies])
    _write_csv(paths["csv"], vacancies)
    _write_csv(paths["combined_csv"], vacancies)
    paths["shortlist"].write_text(_shortlist_markdown(shortlist), encoding="utf-8")
    paths["combined_shortlist"].write_text(_shortlist_markdown(shortlist), encoding="utf-8")
    paths["blocked"].write_text(_blocked_markdown(blocked), encoding="utf-8")
    paths["role_review"].write_text(_role_review_markdown(role_review), encoding="utf-8")
    summary = {
        "run_timestamp": datetime.now().astimezone().isoformat(),
        "source": _source_name(vacancies, raw_records, preferences),
        "queries_executed": queries,
        "requests_made": stats.requests_made,
        "raw_records_received": stats.raw_records_received,
        "normalized_records": len(vacancies),
        "duplicates_merged": stats.duplicates_merged,
        "blocked_count": len(blocked),
        "eligible_count": eligible_count,
        "role_review_count": len(role_review),
        "shortlist_count": len(shortlist),
        "warning_count": sum(len(vacancy.warnings) for vacancy in vacancies),
        "errors": [sanitize_error(error) for error in stats.errors],
    }
    write_json(paths["summary"], summary)
    return summary


def rebuild_exports(vacancies: list[NormalizedVacancy], preferences: Preferences) -> dict[str, Any]:
    paths = output_paths(preferences)
    ensure_directory(paths["dir"])
    shortlist = sorted_shortlist(vacancies, preferences.run.shortlist_size)
    blocked = sorted([v for v in vacancies if v.blocker], key=lambda item: item.score, reverse=True)
    role_review = sorted(
        [v for v in vacancies if not v.blocker and v.requires_manual_role_review],
        key=lambda item: item.score,
        reverse=True,
    )
    eligible_count = sum(
        not v.blocker and not v.requires_manual_location_review and not v.requires_manual_role_review for v in vacancies
    )
    write_json(paths["normalized"], [vacancy.model_dump(mode="json") for vacancy in vacancies])
    write_json(paths["combined_json"], [vacancy.model_dump(mode="json") for vacancy in vacancies])
    _write_csv(paths["csv"], vacancies)
    _write_csv(paths["combined_csv"], vacancies)
    paths["shortlist"].write_text(_shortlist_markdown(shortlist), encoding="utf-8")
    paths["combined_shortlist"].write_text(_shortlist_markdown(shortlist), encoding="utf-8")
    paths["blocked"].write_text(_blocked_markdown(blocked), encoding="utf-8")
    paths["role_review"].write_text(_role_review_markdown(role_review), encoding="utf-8")
    summary = {
        "run_timestamp": datetime.now().astimezone().isoformat(),
        "source": _source_name(vacancies, [], preferences),
        "queries_executed": sorted({q for vacancy in vacancies for q in vacancy.source_queries}),
        "requests_made": 0,
        "raw_records_received": 0,
        "normalized_records": len(vacancies),
        "duplicates_merged": 0,
        "blocked_count": len(blocked),
        "eligible_count": eligible_count,
        "role_review_count": len(role_review),
        "shortlist_count": len(shortlist),
        "warning_count": sum(len(vacancy.warnings) for vacancy in vacancies),
        "errors": [],
    }
    write_json(paths["summary"], summary)
    return summary


def _write_csv(path: Path, vacancies: list[NormalizedVacancy]) -> None:
    fields = [
        "score",
        "blocker",
        "requires_manual_role_review",
        "role_relevance_breakdown",
        "title",
        "company",
        "employment_type",
        "detected_language",
        "work_mode",
        "international_remote_eligibility",
        "requires_manual_location_review",
        "international_remote_evidence",
        "location_restrictions",
        "salary_min",
        "salary_max",
        "salary_currency",
        "publication_date",
        "description_completeness",
        "matched_signals",
        "blocker_reasons",
        "warnings",
        "source_url",
        "apply_url",
        "application_url",
        "source",
        "sources",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for vacancy in vacancies:
            row = vacancy.model_dump(mode="json")
            writer.writerow({field: _csv_value(row.get(field)) for field in fields})


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    else:
        value = "" if value is None else str(value)
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def _shortlist_markdown(vacancies: list[NormalizedVacancy]) -> str:
    lines = ["# Job Shortlist", "", f"Source: {_display_source_name(vacancies)}", ""]
    for rank, vacancy in enumerate(vacancies, start=1):
        lines.extend(
            [
                f"## {rank}. {_markdown_title(vacancy.title)} - {vacancy.company or 'Unknown company'}",
                f"- Score: {vacancy.score}",
                f"- Role relevance: {'; '.join(vacancy.role_relevance_breakdown) or 'n/a'}",
                f"- Employment type: {vacancy.employment_type or 'n/a'}",
                f"- Work format: {vacancy.work_mode or 'n/a'}",
                f"- Language: {vacancy.detected_language or 'n/a'}",
                f"- Location restrictions: {', '.join(vacancy.location_restrictions) or 'n/a'}",
                f"- Salary: {_salary(vacancy)}",
                f"- Publication date: {vacancy.publication_date.isoformat() if vacancy.publication_date else 'n/a'}",
                f"- Description completeness: {vacancy.description_completeness}",
                f"- Matched signals: {', '.join(vacancy.matched_signals) or 'n/a'}",
                f"- Score breakdown: {'; '.join(vacancy.score_breakdown) or 'n/a'}",
                f"- Warnings: {'; '.join(vacancy.warnings) or 'none'}",
                f"- Vacancy URL: {vacancy.source_url or vacancy.application_url or 'n/a'}",
                f"- Application URL: {vacancy.apply_url or vacancy.application_url or 'n/a'}",
                f"- Source: {_display_source_name([vacancy])}",
                "",
            ]
        )
    return "\n".join(lines)


def _markdown_title(title: str) -> str:
    title = re.sub(r"[\r\n]+", " ", title)
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|>~\-:/@])", r"\\\1", title)
    return html.escape(escaped, quote=False)


def _blocked_markdown(vacancies: list[NormalizedVacancy]) -> str:
    lines = ["# Blocked Jobs", ""]
    for vacancy in vacancies:
        lines.extend(
            [
                f"## {vacancy.title} - {vacancy.company or 'Unknown company'}",
                f"- Score: {vacancy.score}",
                f"- Blocker reasons: {'; '.join(vacancy.blocker_reasons)}",
                f"- Application URL: {vacancy.application_url or 'n/a'}",
                "",
            ]
        )
    return "\n".join(lines)


def _role_review_markdown(vacancies: list[NormalizedVacancy]) -> str:
    lines = ["# Manual Role Review", ""]
    for vacancy in vacancies:
        lines.extend(
            [
                f"## {vacancy.title} - {vacancy.company or 'Unknown company'}",
                f"- Score: {vacancy.score}",
                f"- Role relevance: {'; '.join(vacancy.role_relevance_breakdown)}",
                f"- Location restrictions: {', '.join(vacancy.location_restrictions) or 'n/a'}",
                f"- Application URL: {vacancy.apply_url or vacancy.application_url or 'n/a'}",
                f"- Source: {_display_source_name([vacancy])}",
                "",
            ]
        )
    return "\n".join(lines)


def _salary(vacancy: NormalizedVacancy) -> str:
    if vacancy.salary_min is None and vacancy.salary_max is None:
        return "n/a"
    amount = f"{vacancy.salary_min or ''}-{vacancy.salary_max or ''}".strip("-")
    return " ".join(part for part in [amount, vacancy.salary_currency, vacancy.salary_period] if part)


def _source_name(
    vacancies: list[NormalizedVacancy], raw_records: list[dict[str, Any]], preferences: Preferences
) -> str:
    if vacancies:
        return vacancies[0].source
    if raw_records:
        record = raw_records[0].get("record", raw_records[0])
        if isinstance(record, dict) and "alternate_url" in record:
            return "headhunter"
        if isinstance(record, dict) and "jobTitle" in record:
            return "jobicy"
    for name in ("headhunter", "jobicy"):
        source_config = getattr(preferences.sources, name, None)
        if source_config is not None and source_config.enabled:
            return name
    return "unknown"


def _display_source_name(vacancies: list[NormalizedVacancy]) -> str:
    source = vacancies[0].source if vacancies else "unknown"
    return {"headhunter": "HeadHunter", "jobicy": "Jobicy"}.get(source, source)
