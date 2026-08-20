from __future__ import annotations

# ruff: noqa: E402
import logging
from pathlib import Path
from typing import Annotated

from .env import load_project_env

load_project_env()

import typer
from rich.console import Console
from rich.table import Table

from .ats import discover_greenhouse_board_token, discover_lever_site
from .capture import run_capture_server
from .config import ConfigLoadError, load_preferences
from .connectors.arbeitnow import ArbeitnowConnector
from .connectors.greenhouse import GreenhouseConnector
from .connectors.headhunter import HeadHunterConnector
from .connectors.jobicy import JobicyConnector
from .connectors.jooble import JoobleConnector
from .connectors.lever import LeverConnector
from .email_ingestion import sync_email_alerts
from .errors import sanitize_error
from .linkedin_fetch import LinkedInFetchError, LinkedInStopRun, fetch_pending_linkedin, run_login
from .linkedin_queue import (
    LinkedInQueueError,
    load_queue,
    open_next_pending,
    pending_linkedin_candidates,
    select_candidate,
    status_counts,
    validate_linkedin_queue,
)
from .models import BatchStats
from .paths import output_paths
from .persistence import read_headhunter_raw, rebuild_from_authoritative_sources
from .sources import LINKEDIN_EXECUTION_MODE, already_succeeded_today, load_statuses, mark_status, write_statuses
from .utils import read_json, write_json

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
app = typer.Typer(help="Local job-search assistant vertical slice.")
console = Console()


@app.command()
def validate_config() -> None:
    """Validate config/preferences.yaml and target-company config files."""
    try:
        from .config import load_target_companies

        preferences = load_preferences()
        load_target_companies()
    except ConfigLoadError as exc:
        console.print(f"[red]Invalid configuration:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Configuration is valid.[/green] {len(preferences.all_queries)} queries configured.")


@app.command()
def fetch(
    source: str = typer.Option("headhunter", help="Vacancy source to fetch."),
    force: bool = typer.Option(False, "--force", help="Allow a second successful automatic fetch today."),
) -> None:
    """Fetch vacancies, normalize, deduplicate, score, and export outputs."""
    preferences = _load_or_exit()
    raw_records, stats, skipped = _fetch_source(preferences, source, force)
    if skipped:
        if _source_key(source) in load_statuses(preferences):
            mark_status(
                preferences,
                _source_key(source),
                skipped,
                0,
                stats.requests_made,
                stats.errors[-1] if stats.errors else skipped,
            )
        detail = f": {stats.errors[-1]}" if stats.errors else ""
        console.print(f"[yellow]{source} skipped: {skipped}{detail}[/yellow]")
        return
    status = "success" if not stats.errors else "HTTP error"
    summary = rebuild_from_authoritative_sources(
        preferences, stats, headhunter_raw=raw_records if status == "success" else None
    )
    mark_status(
        preferences,
        _source_key(source),
        status,
        len(raw_records),
        stats.requests_made,
        stats.errors[-1] if stats.errors else None,
        stats.role_relevant_count,
        stats.shortlist_count,
    )
    _print_summary(summary)


@app.command("fetch-all")
def fetch_all(
    force: bool = typer.Option(False, "--force", help="Allow a second successful automatic fetch today."),
) -> None:
    """Run all enabled automatic sources with error isolation."""
    preferences = _load_or_exit()
    all_raw: list[dict] = []
    combined_stats = BatchStats()
    for source in _fetch_all_sources(preferences):
        raw_records, stats, skipped = _fetch_source(preferences, source, force)
        combined_stats.requests_made += stats.requests_made
        combined_stats.raw_records_received += stats.raw_records_received
        combined_stats.errors.extend(stats.errors)
        if skipped:
            mark_status(preferences, _source_key(source), skipped, 0, 0, skipped)
            continue
        all_raw.extend(raw_records)
        mark_status(
            preferences,
            _source_key(source),
            "success" if not stats.errors else "HTTP error",
            len(raw_records),
            stats.requests_made,
            stats.errors[-1] if stats.errors else None,
        )
    email_raw: list[dict] = []
    try:
        email_result = sync_email_alerts(preferences, since_days=30)
        email_raw = email_result.headhunter_raw
        combined_stats.requests_made += email_result.stats.requests_made
        combined_stats.raw_records_received += email_result.stats.raw_records_received
        email_errors = _tag_email_errors(email_result.stats.errors)
        combined_stats.errors.extend(email_errors)
        mark_status(
            preferences,
            "email",
            "success" if not email_errors else "error",
            len(email_result.candidates),
            email_result.stats.requests_made,
            email_errors[-1] if email_errors else None,
        )
    except RuntimeError as exc:
        error = _tag_email_errors([str(exc)])[0]
        combined_stats.errors.append(error)
        mark_status(preferences, "email", "error", 0, 0, error)
    if all_raw:
        headhunter_raw = [*all_raw, *email_raw]
    elif email_raw:
        headhunter_raw = [*read_headhunter_raw(preferences), *email_raw]
    else:
        headhunter_raw = None
    summary = rebuild_from_authoritative_sources(preferences, combined_stats, headhunter_raw=headhunter_raw)
    _print_summary(summary)


@app.command("email-sync")
def email_sync(
    since_days: int = typer.Option(
        30, "--since-days", min=1, max=3650, help="How far back to search the configured mailbox."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse messages without writing state or outputs."),
    full_refresh: bool = typer.Option(
        False, "--full-refresh", help="Ignore local email UID/Message-ID state and rebuild email artifacts."
    ),
) -> None:
    """Read Gmail job alerts from the configured IMAP mailbox."""
    preferences = _load_or_exit()
    try:
        result = sync_email_alerts(preferences, since_days=since_days, dry_run=dry_run, full_refresh=full_refresh)
    except RuntimeError as exc:
        console.print(f"[red]Email sync failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    headhunter_raw = None if dry_run else [*read_headhunter_raw(preferences), *result.headhunter_raw]
    summary = {
        "requests_made": result.stats.requests_made,
        "raw_records_received": result.stats.raw_records_received,
        "normalized_records": 0,
        "duplicates_merged": 0,
        "blocked_count": 0,
        "eligible_count": 0,
        "shortlist_count": 0,
        "warning_count": 0,
        "errors": result.stats.errors,
    }
    if not dry_run:
        summary = rebuild_from_authoritative_sources(preferences, result.stats, headhunter_raw=headhunter_raw)
    _print_summary(summary)
    console.print(
        f"Email candidates: {len(result.candidates)}; LinkedIn manual queue: {result.linkedin_queue_count}; "
        f"processed messages: {result.processed_message_count}; dry-run: {dry_run}"
    )


@app.command("capture-server")
def capture_server() -> None:
    """Run the local browser-extension capture receiver."""
    run_capture_server(host="127.0.0.1", port=8765)


@app.command("discover-ats")
def discover_ats(url: Annotated[str, typer.Option(help="Manually captured Greenhouse or Lever URL")]) -> None:
    """Discover target-company ATS identifiers from a manually captured URL."""
    greenhouse = discover_greenhouse_board_token(url)
    if greenhouse:
        console.print(f"Greenhouse board token candidate: [bold]{greenhouse}[/bold]")
        console.print("Confirm with the company before adding it to config/target_companies.yaml.")
        return
    lever = discover_lever_site(url)
    if lever:
        site, region = lever
        console.print(f"Lever site candidate: [bold]{site}[/bold], region: [bold]{region}[/bold]")
        console.print("Confirm with the company before adding it to config/target_companies.yaml.")
        return
    console.print("No supported Greenhouse or Lever identifier found.")


@app.command("sources")
def sources_command() -> None:
    """Show source configuration, credentials, company lists, and last run status."""
    preferences = _load_or_exit()
    statuses = load_statuses(preferences)
    write_statuses(preferences, statuses)
    table = Table(title="Sources")
    table.add_column("Source", no_wrap=True)
    table.add_column("Enabled", no_wrap=True)
    table.add_column("Mode", no_wrap=True)
    table.add_column("Credentials", no_wrap=True)
    table.add_column("Companies", no_wrap=True)
    table.add_column("Last success")
    table.add_column("Last error")
    table.add_column("Status")
    table.add_column("Priority", no_wrap=True)
    table.add_column("Searches", no_wrap=True)
    table.add_column("Raw", no_wrap=True)
    table.add_column("Cards", no_wrap=True)
    table.add_column("Opened", no_wrap=True)
    table.add_column("Main", no_wrap=True)
    table.add_column("Review", no_wrap=True)
    for status in statuses.values():
        table.add_row(
            status.source,
            str(status.enabled),
            status.mode,
            status.credential_status,
            status.company_list_status,
            status.last_successful_run or "n/a",
            status.last_error or "n/a",
            status.status,
            status.priority,
            str(status.configured_search_count),
            str(status.raw_count),
            str(status.card_prefilter_count),
            str(status.opened_vacancy_count),
            str(status.shortlist_count),
            str(status.manual_review_count),
        )
    console.print(table)


@app.command("linkedin-queue")
def linkedin_queue(
    status: bool = typer.Option(False, "--status", help="Show LinkedIn queue counts by status."),
    next_item: bool = typer.Option(False, "--next", help="Show the next pending LinkedIn URL."),
    open_item: bool = typer.Option(
        False, "--open", help="Open the next pending LinkedIn URL in the default browser. Requires --next."
    ),
    list_items: bool = typer.Option(False, "--list", help="List LinkedIn queue candidates."),
    pending_only: bool = typer.Option(False, "--pending-only", help="Only list pending manual-capture candidates."),
    select: str | None = typer.Option(None, "--select", help="Show one candidate by queue ID or LinkedIn job ID."),
) -> None:
    """Inspect LinkedIn email candidates awaiting browser capture."""
    preferences = _load_or_exit()
    try:
        candidates = load_queue(preferences)
        validate_linkedin_queue(candidates)
    except LinkedInQueueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if open_item and not next_item:
        console.print("[red]--open is only supported together with --next.[/red]")
        raise typer.Exit(1)
    if pending_only and not any([status, next_item, list_items, select]):
        list_items = True
    if status or not any([next_item, list_items, select]):
        _print_linkedin_queue_status(candidates)
    if next_item:
        pending = pending_linkedin_candidates(candidates)
        if not pending:
            console.print("No pending LinkedIn candidates.")
        else:
            _print_linkedin_candidate(*pending[0])
            if open_item:
                try:
                    result = open_next_pending(preferences)
                except LinkedInQueueError as exc:
                    console.print(f"[red]{exc}[/red]")
                    raise typer.Exit(1) from exc
                if result.get("opened"):
                    console.print(f"Opened {result.get('url')}.", markup=False)
                else:
                    error = result.get("error")
                    if error:
                        console.print(f"[yellow]Could not open {result.get('url')}: {error}[/yellow]", markup=False)
                    else:
                        console.print("No pending LinkedIn candidates.")
    if select:
        selected = select_candidate(candidates, select)
        if selected is None:
            console.print(f"[red]No LinkedIn queue candidate found for {select!r}.[/red]")
            raise typer.Exit(1)
        _print_linkedin_candidate(*selected)
    if list_items:
        items = (
            pending_linkedin_candidates(candidates)
            if pending_only
            else [
                (index, item)
                for index, item in enumerate(candidates, start=1)
                if isinstance(item, dict) and item.get("source") == "linkedin"
            ]
        )
        if not items:
            console.print("No LinkedIn candidates found.")
        for index, item in items:
            _print_linkedin_candidate(index, item)


@app.command("linkedin-fetch")
def linkedin_fetch(
    login: bool = typer.Option(
        False, "--login", help="Open dedicated headed Chromium profile for manual LinkedIn login."
    ),
    limit: int = typer.Option(5, "--limit", min=1, max=100, help="Maximum pending LinkedIn candidates to process."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Open and extract without writing queue/import/shortlist files."
    ),
    pause_seconds: float = typer.Option(
        1.0, "--pause-seconds", min=0.0, max=60.0, help="Pause between LinkedIn pages."
    ),
) -> None:
    """Fetch queued LinkedIn vacancies through a dedicated Playwright browser profile."""
    if login:
        run_login()
        console.print("LinkedIn login window closed. Cookies remain only in the dedicated local browser profile.")
        return
    preferences = _load_or_exit()
    try:
        stats = fetch_pending_linkedin(preferences, limit=limit, dry_run=dry_run, pause_seconds=pause_seconds)
    except LinkedInStopRun as exc:
        console.print(f"[red]LinkedIn fetch stopped:[/red] {exc}")
        raise typer.Exit(1) from exc
    except LinkedInFetchError as exc:
        console.print(f"[red]LinkedIn fetch failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"LinkedIn fetch: opened={stats['opened']} imported={stats['imported']} "
        f"skipped_title={stats['skipped_title']} expired={stats['expired']} "
        f"extraction_failed={stats['extraction_failed']} dry_run={dry_run}"
    )


@app.command("import-manual")
def import_manual(
    source: Annotated[str, typer.Option(help="linkedin or other")],
    url: Annotated[str, typer.Option(help="Original vacancy URL; never fetched")],
    file: Annotated[Path, typer.Option(help="Local text file provided by the user")],
    title: Annotated[str | None, typer.Option(help="Explicit vacancy title")] = None,
    company: Annotated[str | None, typer.Option(help="Explicit company name")] = None,
    language: Annotated[str | None, typer.Option(help="Explicit language code")] = None,
) -> None:
    """Import user-provided vacancy text without fetching the URL."""
    preferences = _load_or_exit()
    if source not in {"linkedin", "other"}:
        console.print("[red]Manual source must be one of: linkedin, other.[/red]")
        raise typer.Exit(1)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]Could not read manual import file:[/red] {exc}")
        raise typer.Exit(1) from exc
    raw = {
        "query": "manual_import",
        "record": {
            "__source": "manual",
            "manual_source": source,
            "url": url,
            "text": text,
            "title": title or _infer_title(text),
            "company": company or _infer_company(text),
            "language": language,
        },
    }
    paths = output_paths(preferences)
    existing_raw = read_json(paths["manual_imports"]) if paths["manual_imports"].exists() else []
    existing_raw.append(raw)
    write_json(paths["manual_imports"], existing_raw)
    summary = rebuild_from_authoritative_sources(preferences, BatchStats(raw_records_received=1))
    _print_summary(summary)


@app.command()
def shortlist() -> None:
    """Rebuild shortlist outputs from latest HeadHunter raw plus LinkedIn captures."""
    preferences = _load_or_exit()
    summary = rebuild_from_authoritative_sources(preferences, BatchStats())
    _print_summary(summary)


def _load_or_exit():
    try:
        return load_preferences()
    except ConfigLoadError as exc:
        console.print(f"[red]Invalid configuration:[/red] {exc}")
        raise typer.Exit(1) from exc


def _print_summary(summary: dict) -> None:
    table = Table(title="Job Assistant Run Summary")
    table.add_column("Metric")
    table.add_column("Value")
    for key in [
        "requests_made",
        "raw_records_received",
        "normalized_records",
        "duplicates_merged",
        "blocked_count",
        "eligible_count",
        "role_review_count",
        "shortlist_count",
        "warning_count",
    ]:
        table.add_row(key, str(summary.get(key)))
    if summary.get("errors"):
        errors = [sanitize_error(error) for error in summary["errors"]]
        table.add_row("errors", str(len(errors)))
        table.add_row("error_details", "\n".join(errors[:5]))
    console.print(table)


def _tag_email_errors(errors: list[str]) -> list[str]:
    return [error if error.startswith("email ") else f"email request=imap_sync: {error}" for error in errors]


def _print_linkedin_queue_status(candidates: list[dict]) -> None:
    table = Table(title="LinkedIn Queue")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    counts = status_counts(candidates)
    for item_status, count in sorted(counts.items()):
        table.add_row(item_status, str(count))
    table.add_row("total", str(sum(counts.values())))
    console.print(table)


def _print_linkedin_candidate(index: int, item: dict) -> None:
    title = item.get("title") or "LinkedIn vacancy"
    company = item.get("company") or "Unknown company"
    status = item.get("status") or "unknown"
    url = item.get("canonical_url") or "n/a"
    console.print(f"{index}. [{status}] {title} - {company}", markup=False)
    console.print(f"   job_id={item.get('external_id') or 'n/a'} url={url}", markup=False)


def _fetch_source(preferences, source: str, force: bool) -> tuple[list[dict], BatchStats, str | None]:
    source = _source_key(source)
    if source == "linkedin":
        message = (
            f"{LINKEDIN_EXECUTION_MODE}: use `uv run python -m job_assistant capture-server` "
            "with the Chromium extension, or explicitly run "
            "`uv run python -m job_assistant linkedin-fetch`; Playwright never starts through `fetch` or `fetch-all`"
        )
        return [], BatchStats(errors=[message]), LINKEDIN_EXECUTION_MODE
    config = getattr(preferences.sources, source, None)
    if config is None:
        raise typer.BadParameter(f"Unsupported source: {source}")
    if not config.enabled:
        return [], BatchStats(), "disabled"
    statuses = load_statuses(preferences)
    status = statuses[source]
    if status.credential_status == "missing":
        return [], BatchStats(errors=["disabled_missing_credentials"]), "disabled_missing_credentials"
    if status.credential_status == "authentication_missing":
        return [], BatchStats(errors=["authentication_missing"]), "authentication_missing"
    if status.company_list_status == "empty_company_list":
        return [], BatchStats(errors=["empty_company_list"]), "empty_company_list"
    if status.company_list_status == "skipped_empty_configuration":
        return [], BatchStats(errors=["skipped_empty_configuration"]), "skipped_empty_configuration"
    if already_succeeded_today(status) and not force:
        return [], BatchStats(), "already_successful_today"
    if force:
        console.print(
            f"[yellow]Warning: --force bypasses the one-successful-fetch-per-day guard for {source}.[/yellow]"
        )
    connectors = {
        "headhunter": HeadHunterConnector,
        "jooble": JoobleConnector,
        "arbeitnow": ArbeitnowConnector,
        "greenhouse": GreenhouseConnector,
        "lever": LeverConnector,
        "jobicy": JobicyConnector,
    }
    if source not in connectors:
        return [], BatchStats(errors=["unsupported"]), "unsupported"
    connector = connectors[source](preferences)
    raw, stats = connector.fetch()
    return raw, stats, None


def _fetch_all_sources(preferences) -> list[str]:
    return ["headhunter"]


def _source_key(source: str) -> str:
    aliases = {}
    return aliases.get(source.strip().lower(), source.strip().lower().replace("-", "_"))


def _infer_title(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:120]
    return "Manual import vacancy"


def _infer_company(text: str) -> str | None:
    for line in text.splitlines()[1:5]:
        cleaned = line.strip()
        if cleaned:
            return cleaned[:120]
    return None
