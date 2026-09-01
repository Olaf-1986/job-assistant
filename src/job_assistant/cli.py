from __future__ import annotations

# ruff: noqa: E402
import asyncio
import logging
from datetime import datetime
from enum import Enum
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
from .export import export_channel_shortlists, export_shortlist, export_source_shortlist
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
from .models import BatchStats, NormalizedVacancy
from .paths import output_paths
from .persistence import read_headhunter_raw, rebuild_from_authoritative_sources
from .sources import LINKEDIN_EXECUTION_MODE, already_succeeded_today, load_statuses, mark_status, write_statuses
from .telegram_audit import TelegramAuditResult, audit_telegram
from .telegram_client import TelegramConnectionError, TelegramError, telegram_login
from .telegram_ingestion import TelegramFetchResult, fetch_telegram
from .utils import read_json, write_json

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
app = typer.Typer(help="Local job-search assistant vertical slice.")
console = Console()


class FetchWindow(str, Enum):
    day = "day"
    two_days = "2-days"
    three_days = "3-days"
    week = "week"
    two_weeks = "2-weeks"
    three_weeks = "3-weeks"
    month = "month"

    @property
    def days(self) -> int:
        return {
            FetchWindow.day: 1,
            FetchWindow.two_days: 2,
            FetchWindow.three_days: 3,
            FetchWindow.week: 7,
            FetchWindow.two_weeks: 14,
            FetchWindow.three_weeks: 21,
            FetchWindow.month: 30,
        }[self]


class QuickWindow(str, Enum):
    one_day = "1d"
    two_days = "2d"
    three_days = "3d"
    one_week = "1w"
    two_weeks = "2w"
    three_weeks = "3w"
    one_month = "1m"

    @property
    def days(self) -> int:
        return {
            QuickWindow.one_day: 1,
            QuickWindow.two_days: 2,
            QuickWindow.three_days: 3,
            QuickWindow.one_week: 7,
            QuickWindow.two_weeks: 14,
            QuickWindow.three_weeks: 21,
            QuickWindow.one_month: 30,
        }[self]


class QuickSource(str, Enum):
    all = "all"
    headhunter = "hh"
    linkedin = "li"
    telegram = "tg"


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
    shortlist_size: int | None = typer.Option(None, "--shortlist-size", "-n", min=1, max=500),
    last: FetchWindow | None = typer.Option(
        None,
        "--last",
        help="Publication window: day, 2-days, 3-days, week, 2-weeks, 3-weeks, or month.",
    ),
) -> None:
    """Fetch vacancies, normalize, deduplicate, score, and export outputs."""
    preferences = _load_or_exit(shortlist_size)
    raw_records, stats, skipped = _fetch_source(
        preferences, source, force, since_days=last.days if last is not None else None
    )
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
    shortlist_size: int | None = typer.Option(None, "--shortlist-size", "-n", min=1, max=500),
    last: FetchWindow | None = typer.Option(
        None,
        "--last",
        help="Publication window: day, 2-days, 3-days, week, 2-weeks, 3-weeks, or month.",
    ),
) -> None:
    """Run all enabled automatic sources with error isolation."""
    preferences = _load_or_exit(shortlist_size)
    all_raw: list[dict] = []
    combined_stats = BatchStats()
    for source in _fetch_all_sources():
        raw_records, stats, skipped = _fetch_source(
            preferences, source, force, since_days=last.days if last is not None else None
        )
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
    if preferences.sources.email.enabled:
        try:
            email_result = sync_email_alerts(preferences, since_days=last.days if last is not None else 30)
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
        except Exception as exc:
            error = _tag_email_errors([exc])[0]
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
    except Exception as exc:
        console.print(f"[red]Email sync failed:[/red] {sanitize_error(exc)}")
        raise typer.Exit(1) from None
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
    limit: int = typer.Option(5, "--limit", min=1, max=1000, help="Maximum pending LinkedIn candidates to process."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Open and extract without writing queue/import/shortlist files."
    ),
    pause_seconds: float | None = typer.Option(
        None,
        "--pause-seconds",
        min=5.0,
        max=20.0,
        help="Optional safe fixed delay; omit to choose a different 5-20 second delay after each LinkedIn page.",
    ),
    location_prefilter: bool = typer.Option(
        False,
        "--location-prefilter",
        help="Reject only explicit non-Tbilisi onsite/hybrid locations from queue metadata before navigation.",
    ),
    prioritize: bool = typer.Option(
        False,
        "--prioritize",
        help="Prioritize Tbilisi/remote and stronger titles while keeping EMEA ambiguous.",
    ),
    shortlist_size: int | None = typer.Option(None, "--shortlist-size", "-n", min=1, max=500),
) -> None:
    """Fetch queued LinkedIn vacancies through a dedicated Playwright browser profile."""
    if login:
        run_login()
        console.print("LinkedIn login window closed. Cookies remain only in the dedicated local browser profile.")
        return
    preferences = _load_or_exit(shortlist_size)
    try:
        stats = fetch_pending_linkedin(
            preferences,
            limit=limit,
            dry_run=dry_run,
            pause_seconds=pause_seconds,
            location_prefilter=location_prefilter,
            prioritize=prioritize,
        )
    except LinkedInStopRun as exc:
        console.print(f"[red]LinkedIn fetch stopped:[/red] {exc}")
        raise typer.Exit(1) from exc
    except LinkedInFetchError as exc:
        console.print(f"[red]LinkedIn fetch failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"LinkedIn fetch: opened={stats['opened']} imported={stats['imported']} "
        f"title_prefilter_rejected={stats['title_prefilter_rejected']} "
        f"location_prefilter_rejected={stats['location_prefilter_rejected']} expired={stats['expired']} "
        f"extraction_failed={stats['extraction_failed']} dry_run={dry_run}"
    )


@app.command("telegram-login")
def telegram_login_command(
    qr: bool = typer.Option(
        False,
        "--qr",
        help="Use local terminal QR login from an already-authorized Telegram client.",
    ),
) -> None:
    """Authorize the local read-only Telegram user session."""
    preferences = _load_or_exit()
    try:
        status = asyncio.run(telegram_login(preferences, qr=qr))
    except TelegramConnectionError as exc:
        console.print(f"[red]Telegram connection failed:[/red] {sanitize_error(exc)}")
        raise typer.Exit(1) from None
    except TelegramError as exc:
        console.print(f"[red]Telegram login failed:[/red] {sanitize_error(exc)}")
        raise typer.Exit(1) from None
    if status == "already_authorized":
        console.print("Telegram session is already authorized. No credential values were displayed.")
    else:
        console.print("Telegram session authorized and stored locally with restricted permissions.")


@app.command("telegram-fetch")
def telegram_fetch(
    since_days: int = typer.Option(
        3, "--since-days", min=1, max=90, help="Initial history window; checkpoints take precedence on later runs."
    ),
    limit: int | None = typer.Option(
        None, "--limit", min=1, max=10000, help="Dry-run-only maximum messages to read across the allowlist."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Read and parse without importing, checkpointing, rebuilding, or updating source status.",
    ),
    all_history: bool = typer.Option(
        False,
        "--all-history",
        help="With --dry-run and --limit, select the newest messages across the allowlist without a day cutoff.",
    ),
    show_targets: bool = typer.Option(
        False,
        "--show-targets",
        help="Print eligible target vacancies in the shared shortlist score/date order.",
    ),
    export_shortlist_preview: bool = typer.Option(
        False,
        "--export-shortlist",
        help="With --dry-run, write only the eligible Telegram preview to output/shortlist_tg.md.",
    ),
    export_channel_check: bool = typer.Option(
        False,
        "--export-channel-check",
        help="With --dry-run, export one count-ranked shortlist per channel for the full --since-days window.",
    ),
    shortlist_size: int | None = typer.Option(None, "--shortlist-size", "-n", min=1, max=500),
) -> None:
    """Read allowlisted joined Telegram sources through the user API."""
    if limit is not None and not dry_run:
        console.print("[red]--limit requires --dry-run so a partial history cannot advance checkpoints.[/red]")
        raise typer.Exit(1)
    if all_history and (not dry_run or limit is None):
        console.print("[red]--all-history requires both --dry-run and --limit.[/red]")
        raise typer.Exit(1)
    if show_targets and not dry_run:
        console.print("[red]--show-targets requires --dry-run.[/red]")
        raise typer.Exit(1)
    if export_shortlist_preview and not dry_run:
        console.print("[red]--export-shortlist requires --dry-run.[/red]")
        raise typer.Exit(1)
    if export_channel_check and not dry_run:
        console.print("[red]--export-channel-check requires --dry-run.[/red]")
        raise typer.Exit(1)
    preferences = _load_or_exit(shortlist_size)
    try:
        result = asyncio.run(
            fetch_telegram(
                preferences,
                since_days=since_days,
                limit=limit,
                dry_run=dry_run,
                all_history=all_history,
                ignore_checkpoints=export_channel_check,
            )
        )
    except TelegramError as exc:
        console.print(f"[red]Telegram fetch failed:[/red] {sanitize_error(exc)}")
        raise typer.Exit(1) from None
    _print_telegram_fetch(result)
    if show_targets:
        _print_telegram_targets(result.eligible_targets)
    if export_shortlist_preview:
        path = output_paths(preferences)["telegram_shortlist"]
        count = export_shortlist(result.eligible_targets, len(result.eligible_targets), path)
        console.print(f"Telegram preview shortlist: {count} records written to {path}.")
    if export_channel_check:
        directory = output_paths(preferences)["dir"] / (
            f"telegram_channel_check_{datetime.now().astimezone():%Y%m%dT%H%M%S}_{since_days}d"
        )
        vacancies_by_channel = {
            source: result.eligible_targets_by_source.get(source, []) for source in result.source_reports
        }
        channel_errors = {source: report.error for source, report in result.source_reports.items()}
        exported = export_channel_shortlists(vacancies_by_channel, channel_errors, directory)
        console.print(f"Telegram channel check: {len(exported)} channel files written to {directory}.")
    if not dry_run:
        status = "success" if not result.errors else "partial"
        mark_status(
            preferences,
            "telegram",
            status,
            result.imported_records,
            sum(1 for report in result.source_reports.values() if report.error is None),
            result.errors[-1] if result.errors else None,
            role_relevant_count=sum(report.target_role_matches for report in result.source_reports.values()),
            shortlist_count=int((result.summary or {}).get("shortlist_count", 0)),
        )


@app.command("telegram-audit")
def telegram_audit(
    since_days: int = typer.Option(
        3, "--since-days", min=1, max=30, help="Current audit window; the preceding equal window is also checked."
    ),
) -> None:
    """Report source yield, cross-source containment, and conservative removal evidence."""
    preferences = _load_or_exit()
    try:
        result = asyncio.run(audit_telegram(preferences, since_days=since_days))
    except TelegramError as exc:
        console.print(f"[red]Telegram audit failed:[/red] {sanitize_error(exc)}")
        raise typer.Exit(1) from None
    _print_telegram_audit(result)


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
            "__source": source,
            "manual_source": source,
            "url": url,
            "text": text,
            "title": title or _infer_title(text),
            "company": company or _infer_company(text),
            "language": language,
        },
    }
    paths = output_paths(preferences)
    existing_raw = read_json(paths["manual_imports"], []) if paths["manual_imports"].exists() else []
    existing_raw.append(raw)
    write_json(paths["manual_imports"], existing_raw)
    summary = rebuild_from_authoritative_sources(preferences, BatchStats(raw_records_received=1))
    _print_summary(summary)


@app.command("qf")
def quick_fetch(
    ctx: typer.Context,
    window: QuickWindow = typer.Argument(help="Lookback: 1d, 2d, 3d, 1w, 2w, 3w, or 1m."),
    source: QuickSource = typer.Argument(help="Source: all, hh, li, or tg."),
    shortlist_size: int = typer.Argument(min=1, max=500, help="Maximum shortlist entries."),
    force: bool = typer.Option(False, "--force", help="Allow another successful HeadHunter fetch today."),
    linkedin_limit: int = typer.Option(5, "--li-limit", min=1, max=1000, help="LinkedIn queue items to process."),
) -> None:
    """Short, composable alias for source fetches and shortlist sizing."""
    fetch_window = {
        1: FetchWindow.day,
        2: FetchWindow.two_days,
        3: FetchWindow.three_days,
        7: FetchWindow.week,
        14: FetchWindow.two_weeks,
        21: FetchWindow.three_weeks,
        30: FetchWindow.month,
    }[window.days]

    if source is QuickSource.all:
        ctx.invoke(fetch_all, force=force, shortlist_size=shortlist_size, last=fetch_window)
    elif source is QuickSource.headhunter:
        ctx.invoke(
            fetch,
            source="headhunter",
            force=force,
            shortlist_size=shortlist_size,
            last=fetch_window,
        )
    if source in {QuickSource.all, QuickSource.linkedin}:
        console.print("LinkedIn is queue-driven; the lookback token does not filter queued LinkedIn items.")
        ctx.invoke(
            linkedin_fetch,
            login=False,
            limit=linkedin_limit,
            dry_run=False,
            pause_seconds=None,
            location_prefilter=False,
            prioritize=False,
            shortlist_size=shortlist_size,
        )
    if source in {QuickSource.all, QuickSource.telegram}:
        ctx.invoke(
            telegram_fetch,
            since_days=window.days,
            limit=None,
            dry_run=False,
            all_history=False,
            show_targets=False,
            export_shortlist_preview=False,
            export_channel_check=False,
            shortlist_size=shortlist_size,
        )
    shortlist_source = (
        None
        if source is QuickSource.all
        else {
            QuickSource.headhunter: "headhunter",
            QuickSource.linkedin: "linkedin",
            QuickSource.telegram: "telegram",
        }[source]
    )
    ctx.invoke(shortlist, source=shortlist_source, size=shortlist_size)


def qf_app() -> None:
    """Expose quick fetch as a standalone console command."""
    typer.run(quick_fetch)


@app.command()
def shortlist(
    source: str | None = typer.Option(
        None, "--source", help="Optional source-only view: headhunter (hh), linkedin (li), or telegram (tg)."
    ),
    size: int | None = typer.Option(None, "--size", "-n", min=1, max=500, help="Shortlist entries for this run."),
) -> None:
    """Rebuild canonical shortlist outputs from all authoritative source stores."""
    preferences = _load_or_exit(size)
    source_key = _short_source_key(source) if source is not None else None
    if source_key is not None and source_key not in {"headhunter", "linkedin", "telegram"}:
        console.print("[red]--source supports only: headhunter (hh), linkedin (li), or telegram (tg).[/red]")
        raise typer.Exit(1)
    summary = rebuild_from_authoritative_sources(preferences, BatchStats())
    _print_summary(summary)
    if source_key is not None:
        paths = output_paths(preferences)
        combined = read_json(paths["combined_json"], [])
        vacancies = [NormalizedVacancy.model_validate(item) for item in combined if isinstance(item, dict)]
        count = export_source_shortlist(
            vacancies,
            source_key,
            preferences.run.shortlist_size,
            paths["shortlist"],
        )
        if source_key == "telegram":
            export_source_shortlist(
                vacancies,
                source_key,
                preferences.run.shortlist_size,
                paths["telegram_shortlist"],
            )
        console.print(f"{source_key.title()}-only shortlist: {count} records written to {paths['shortlist']}.")


def _load_or_exit(shortlist_size: int | None = None):
    try:
        preferences = load_preferences()
    except ConfigLoadError as exc:
        console.print(f"[red]Invalid configuration:[/red] {exc}")
        raise typer.Exit(1) from exc
    if shortlist_size is None:
        return preferences
    return preferences.model_copy(update={"run": preferences.run.model_copy(update={"shortlist_size": shortlist_size})})


def _short_source_key(source: str) -> str:
    return {"hh": "headhunter", "li": "linkedin", "tg": "telegram"}.get(_source_key(source), _source_key(source))


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


def _tag_email_errors(errors: list[object]) -> list[str]:
    return [
        sanitized if sanitized.startswith("email ") else f"email request=imap_sync: {sanitized}"
        for error in errors
        if (sanitized := sanitize_error(error))
    ]


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


def _print_telegram_fetch(result: TelegramFetchResult) -> None:
    table = Table(title=f"Telegram Fetch (dry-run={result.dry_run})")
    table.add_column("Source")
    table.add_column("Read", justify="right")
    table.add_column("Vacancy-like", justify="right")
    table.add_column("Parsed", justify="right")
    table.add_column("Target", justify="right")
    table.add_column("Rejected", justify="right")
    table.add_column("Failures", justify="right")
    table.add_column("Latest")
    table.add_column("Status")
    for report in result.source_reports.values():
        table.add_row(
            report.source,
            str(report.messages_read),
            str(report.vacancy_like_messages),
            str(report.parsed_vacancies),
            str(report.target_role_matches),
            str(report.rejected_non_vacancy_messages),
            str(report.parse_failures),
            report.most_recent_message_date or "n/a",
            report.error or "ok",
        )
    console.print(table)
    console.print(
        f"Messages selected: {result.messages_read}; messages inspected: {result.messages_inspected}; "
        f"target records parsed: {result.imported_records}; "
        f"eligible targets after shared filters/deduplication: {len(result.eligible_targets)}; "
        f"project state changed: {not result.dry_run}."
    )
    if result.summary is not None:
        _print_summary(result.summary)


def _print_telegram_targets(vacancies: list[NormalizedVacancy]) -> None:
    console.print(f"Eligible Telegram target vacancies ({len(vacancies)}), sorted by score then publication date:")
    if not vacancies:
        console.print("No eligible target vacancies were found.")
        return
    for index, vacancy in enumerate(vacancies, start=1):
        sources = ", ".join(vacancy.sources or [vacancy.source])
        location = ", ".join(vacancy.location_restrictions) or "n/a"
        published = vacancy.publication_date.isoformat() if vacancy.publication_date else "n/a"
        url = vacancy.apply_url or vacancy.source_url or vacancy.application_url or "n/a"
        console.print(
            f"{index}. score={vacancy.score} | {vacancy.title} — {vacancy.company or 'Unknown company'}",
            markup=False,
        )
        console.print(
            f"   published={published} | source={sources} | work_mode={vacancy.work_mode or 'n/a'} | "
            f"location={location}",
            markup=False,
        )
        console.print(f"   url={url}", markup=False)


def _print_telegram_audit(result: TelegramAuditResult) -> None:
    table = Table(title=f"Telegram Source Audit ({result.window_days}-day window)")
    table.add_column("Source")
    table.add_column("Read", justify="right")
    table.add_column("Vacancy-like", justify="right")
    table.add_column("Parsed", justify="right")
    table.add_column("Target", justify="right")
    table.add_column("Relevant", justify="right")
    table.add_column("Unique relevant", justify="right")
    table.add_column("Non-vacancy", justify="right")
    table.add_column("Failures", justify="right")
    table.add_column("Latest")
    for metrics in result.sources.values():
        table.add_row(
            metrics.source,
            str(metrics.messages_read),
            str(metrics.vacancy_like_messages),
            str(metrics.successfully_parsed_vacancies),
            str(metrics.target_role_matches),
            str(metrics.relevant_vacancies),
            str(metrics.unique_relevant_vacancies),
            str(metrics.rejected_non_vacancy_messages),
            str(metrics.parse_failures),
            metrics.most_recent_message_date or metrics.error or "n/a",
        )
    console.print(table)

    containment = Table(title="Pairwise Telegram Containment")
    containment.add_column("Source")
    containment.add_column("Contained by")
    containment.add_column("Duplicate / valid")
    containment.add_column("Containment")
    containment.add_column("Other no later")
    for item in result.pairwise_containment:
        containment.add_row(
            item.source,
            item.other_source,
            f"{item.duplicate_count}/{item.valid_vacancies}",
            f"{item.containment:.0%}" if item.containment is not None else "n/a",
            "yes"
            if item.other_publishes_no_later is True
            else "no"
            if item.other_publishes_no_later is False
            else "n/a",
        )
    console.print(containment)
    assessment = result.it_job_offers_assessment
    console.print(
        "it_job_offers: "
        f"active={assessment.get('active_in_window')} "
        f"unique_relevant={assessment.get('produces_unique_relevant_vacancies')} "
        f"recommendation={assessment.get('recommendation')}"
    )
    if result.removal_review_candidates:
        for item in result.removal_review_candidates:
            console.print(
                f"Review only: {item['source']} is contained by {item['contained_by']}; no source was changed."
            )
    else:
        console.print("No source meets the conservative two-window removal-review criteria.")


def _fetch_source(
    preferences, source: str, force: bool, since_days: int | None = None
) -> tuple[list[dict], BatchStats, str | None]:
    source = _source_key(source)
    if source == "linkedin":
        message = (
            f"{LINKEDIN_EXECUTION_MODE}: use `uv run python -m job_assistant capture-server` "
            "with the Chromium extension, or explicitly run "
            "`uv run python -m job_assistant linkedin-fetch`; Playwright never starts through `fetch` or `fetch-all`"
        )
        return [], BatchStats(errors=[message]), LINKEDIN_EXECUTION_MODE
    if source == "telegram":
        mode = "explicit_read_only_allowlist"
        message = (
            "use `uv run python -m job_assistant telegram-fetch`; Telegram never starts through `fetch` or `fetch-all`"
        )
        return [], BatchStats(errors=[message]), mode
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
    connector = (
        HeadHunterConnector(preferences, since_days=since_days)
        if source == "headhunter"
        else connectors[source](preferences)
    )
    raw, stats = connector.fetch()
    return raw, stats, None


def _fetch_all_sources() -> list[str]:
    return ["headhunter"]


def _source_key(source: str) -> str:
    return source.strip().lower().replace("-", "_")


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
