from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .config import ConfigLoadError, load_preferences
from .rvc import audit_rvc, discover_rvc, review_rvc
from .rvc_store import RvcError, RvcReview, load_rvc_store


@contextmanager
def _report_errors(console: Console):
    try:
        yield
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        console.print("Invalid RVC review JSON; use docs/rvc-review.example.json. Contents omitted.")
        raise typer.Exit(1) from None
    except OSError:
        console.print("RVC local file operation failed. Saved reviews remain authoritative; retry after repair.")
        raise typer.Exit(1) from None
    except (RvcError, ConfigLoadError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from None


def register_rvc_commands(app: typer.Typer, console: Console) -> None:
    @app.command("rvc-discover")
    def discover(
        query: str = typer.Option("Business Analyst", help="One specific role per invocation."),
        country: str = typer.Option("GE", help="Two-letter target job country."),
        worldwide: bool = typer.Option(False, help="Search worldwide remote instead of a target country."),
        since_days: int = typer.Option(14, min=1, max=90, help="RVC discovery window; publication needs review."),
        limit: int = typer.Option(10, min=1, max=10),
        dry_run: bool = typer.Option(False, help="Perform one search without saving any local state."),
    ) -> None:
        """Discover up to ten RVC candidates for manual review through the public MCP."""
        with _report_errors(console):
            result, new_count = discover_rvc(
                load_preferences(),
                query=query,
                country=country,
                worldwide=worldwide,
                since_days=since_days,
                limit=limit,
                dry_run=dry_run,
            )
            console.print(
                f"RVC discovery: returned={len(result.results)} new_candidates={new_count} "
                f"reported_available={result.total_available_count} truncated={result.truncated} dry_run={dry_run}"
            )
            if not dry_run:
                console.print("Saved to the review queue. Inspect it with rvc-queue; cards are not shortlist entries.")
            console.print("RVC's date and country labels require independent review before import.")

    @app.command("rvc-queue")
    def queue() -> None:
        """Show saved RVC candidates and their review status without network access."""
        with _report_errors(console):
            store = load_rvc_store(load_preferences())
            table = Table(title="RVC review queue")
            for label in ("ID", "Status", "Company", "Title", "RVC URL"):
                table.add_column(label)
            for entry in sorted(store.entries.values(), key=lambda entry: entry.first_seen_at, reverse=True):
                table.add_row(
                    *(
                        Text(value)
                        for value in (
                            entry.card.masked_id,
                            entry.status,
                            entry.card.company,
                            entry.card.title,
                            entry.card.attributed_url,
                        )
                    )
                )
            console.print(table)

    @app.command("rvc-review")
    def review(
        masked_id: str = typer.Argument(help="ID from rvc-queue."),
        file: Path | None = typer.Option(None, help="User-completed JSON; see docs/rvc-review.example.json."),
        reject: bool = typer.Option(False, help="Reject a candidate without importing its description."),
        expired: bool = typer.Option(False, help="Mark the candidate as no longer accepting applications."),
        minutes: float | None = typer.Option(
            None, min=0, help="Total review minutes for this candidate; omitting it preserves the prior value."
        ),
    ) -> None:
        """Review a candidate locally and rebuild through the shared pipeline."""
        with _report_errors(console):
            if sum((file is not None, reject, expired)) != 1:
                raise RvcError("Choose exactly one of --file, --reject, --expired")
            if file is not None and file.suffix.lower() != ".json":
                raise RvcError("Review input must be a .json file")
            reviewed = RvcReview.model_validate_json(file.read_text(encoding="utf-8")) if file else None
            summary = review_rvc(
                load_preferences(),
                masked_id,
                review=reviewed,
                status="rejected" if reject else "expired" if expired else "reviewed",
                minutes=minutes,
            )
            console.print(
                f"RVC review saved; shared pipeline rebuilt ({summary['shortlist_count']} shortlisted overall)."
            )

    @app.command("rvc-audit")
    def audit() -> None:
        """Measure the pilot's local incremental yield and review effort, without network access."""
        with _report_errors(console):
            console.print_json(data=audit_rvc(load_preferences()))
