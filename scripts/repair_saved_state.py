"""Recover the September 22 audit and available shortlist history without network access.

Run without arguments to inspect aggregate counts, then --apply to back up and repair.
Only raw source stores supply candidates. Markdown snapshots supply comparison identities.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
from datetime import UTC, datetime

from job_assistant.config import load_preferences
from job_assistant.deduplicate import merge_vacancies, vacancies_match
from job_assistant.export import export_shortlist, export_source_shortlist, sorted_shortlist
from job_assistant.linkedin_policy import LINKEDIN_CLOSED_BLOCKER_REASON
from job_assistant.linkedin_queue import linkedin_job_id, mark_linkedin_manual_expired, update_queue_processed
from job_assistant.models import BatchStats, NormalizedVacancy
from job_assistant.paths import output_paths
from job_assistant.persistence import prepare_authoritative_vacancies, rebuild_from_authoritative_sources
from job_assistant.recovered_state import (
    RecoveredExclusion,
    RecoveredHistoryEntry,
    RecoveredState,
    apply_recovered_state,
)
from job_assistant.utils import ROOT, canonical_url, slugify_text, write_json

AUDIT_DATE = "2026-09-22"
ORIGINAL = "deduplicated_shortlist.before-filter-2026-09-22.md"
SNAPSHOTS = [
    "combined_shortlist.md",
    "deduplicated_shortlist.md",
    ORIGINAL,
    "shortlist_hh.md",
    "shortlist_li.md",
    "shortlist_tg.md",
    "shortlist_extra_4.md",
]
AUDITS = ["shortlist-check-2026-09-22-li.json", "shortlist-check-2026-09-22-hh-public.json"]


def parse_snapshot(text: str) -> list[dict[str, str | int]]:
    entries = []
    for block in re.split(r"(?=^## )", text, flags=re.M)[1:]:
        heading = block.splitlines()[0]
        url_match = re.search(r"^- (?:Vacancy|Application) URL: (https?://\S+)", block, re.M)
        if url_match is None:
            raise ValueError("A shortlist entry has no recoverable vacancy URL")
        title_line = re.sub(r"^## (?:\d+\. )?", "", heading)
        title, _, company = title_line.rpartition(" - ")
        title = html.unescape(re.sub(r"\\(.)", r"\1", title or title_line))
        mode_match = re.search(r"^- Work format: (.+)", block, re.M)
        entries.append(
            {
                "rank": len(entries) + 1,
                "url": canonical_url(url_match[1]),
                "title": title,
                "company": html.unescape(company),
                "work_mode": mode_match[1] if mode_match else "unknown",
            }
        )
    return entries


def comparison_identity(entry, authoritative, reconstructed_at):
    url = entry["url"]
    for vacancy in authoritative:
        references = [vacancy.source_url, vacancy.apply_url, vacancy.application_url, *vacancy.source_urls]
        if url in {canonical_url(value) for value in references if value}:
            return vacancy.model_copy(deep=True, update={"previously_exported": False})
    source = "linkedin" if linkedin_job_id(url) else "headhunter" if "/vacancy/" in url else "other"
    return NormalizedVacancy(
        source=source,
        source_id=linkedin_job_id(url) if source == "linkedin" else url.rsplit("/", 1)[-1],
        source_url=url,
        source_urls=[url],
        title=entry["title"],
        normalized_title=slugify_text(entry["title"]),
        company=entry["company"] or None,
        fetched_at=reconstructed_at,
        source_metadata={"comparison_only": True, "fetched_at_basis": "schema placeholder, not a fetch timestamp"},
    )


def build_recovery(directory, authoritative, reconstructed_at):
    history = []
    artifacts = []
    original = None
    for filename in SNAPSHOTS:
        path = directory / filename
        entries = parse_snapshot(path.read_text(encoding="utf-8"))
        artifacts.append(
            {
                "path": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "file_mtime": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "evidence_kind": "shortlist_snapshot",
                "entries": len(entries),
                "exact_delivery_time": None,
            }
        )
        if filename == ORIGINAL:
            original = entries
        for entry in entries:
            identity = comparison_identity(entry, authoritative, reconstructed_at)
            reference = f"{filename}#entry-{entry['rank']}"
            existing = next((item for item in history if vacancies_match(identity, item.identity)), None)
            if existing is None:
                history.append(RecoveredHistoryEntry(identity=identity, evidence=[reference]))
            else:
                existing.identity = merge_vacancies(existing.identity, identity)
                existing.evidence = sorted(set([*existing.evidence, reference]))
    if original is None or len(original) != 50:
        raise ValueError("The original audited 50-entry snapshot is required")
    # The old baseline was not the exported set. Preserve it as uncertain comparison evidence only.
    baseline = directory / "previous_combined_shortlist.json"
    artifacts.append(
        {
            "path": baseline.name,
            "sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
            "file_mtime": datetime.fromtimestamp(baseline.stat().st_mtime, UTC).isoformat(),
            "evidence_kind": "comparison_baseline_not_proof_of_delivery",
            "exact_delivery_time": None,
        }
    )
    exclusions = []
    closures = []
    rows_by_rank = {}
    for filename in AUDITS:
        path = directory / filename
        rows = json.loads(path.read_text(encoding="utf-8"))
        artifacts.append(
            {
                "path": filename,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "evidence_kind": "saved_audit",
                "observation_date": AUDIT_DATE,
            }
        )
        for row in rows:
            rank = row["rank"]
            entry = original[rank - 1]
            if rank in rows_by_rank:
                raise ValueError("Duplicate rank in saved audit")
            rows_by_rank[rank] = (filename, row)
            identity = comparison_identity(entry, authoritative, reconstructed_at)
            evidence = [f"{filename}#rank-{rank}", f"{ORIGINAL}#entry-{rank}"]
            reason = None
            mode = None
            if row["status"] == "closed":
                if not linkedin_job_id(entry["url"]):
                    raise ValueError("A LinkedIn closure has a non-LinkedIn identity")
                closures.append(linkedin_job_id(entry["url"]))
                reason = LINKEDIN_CLOSED_BLOCKER_REASON
            elif row["status"] == "remove_company":
                reason = "company excluded by user in saved shortlist audit"
            elif row["status"] == "remove_work_mode":
                mode = entry["work_mode"]
                if mode not in {"hybrid", "onsite"}:
                    raise ValueError("Audited work-mode exclusion conflicts with the snapshot")
                reason = "office/hybrid position excluded by user in saved shortlist audit"
            # These three descriptions contradicted their original remote/unknown labels.
            if rank in {6, 22, 50}:
                mode = "hybrid" if rank == 22 else "onsite"
                markers = {6: r"работа в офисе", 22: r"гибридный график", 50: r"^on-site$"}
                if not any(re.search(markers[rank], text, re.I) for text in row.get("work_evidence", [])):
                    raise ValueError("Required saved work-location evidence is missing")
                reason = "confirmed office/hybrid requirement in saved audit; excluded by user"
            if reason:
                exclusions.append(
                    RecoveredExclusion(
                        identity=identity, reason=reason, evidence=evidence, observed_date=AUDIT_DATE, work_mode=mode
                    )
                )
    if set(rows_by_rank) != set(range(1, 51)) or len(closures) != 10 or len(exclusions) != 19:
        raise ValueError("Saved audit does not match the reviewed repair scope")
    return RecoveredState(
        reconstructed_at=reconstructed_at, artifacts=artifacts, history=history, exclusions=exclusions
    ), closures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    preferences = load_preferences()
    preferences = preferences.model_copy(update={"run": preferences.run.model_copy(update={"shortlist_size": 50})})
    paths = output_paths(preferences)
    directory = paths["dir"]
    if not directory.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Repair writes must stay inside the repository")
    if paths["recovered_state"].exists():
        recovered = RecoveredState.model_validate_json(paths["recovered_state"].read_text())
        print(
            json.dumps(
                {
                    "already_applied": True,
                    "recovered_identities": len(recovered.history),
                    "saved_exclusions": len(recovered.exclusions),
                    "history_coverage": "partial",
                }
            )
        )
        return
    _, authoritative, _ = prepare_authoritative_vacancies(preferences)
    now = datetime.now(UTC)
    state, closures = build_recovery(directory, authoritative, now)
    apply_recovered_state(authoritative, preferences, state=state)
    plan = {
        "recovered_identities": len(state.history),
        "snapshot_count": len(SNAPSHOTS),
        "closure_count": len(closures),
        "work_location_exclusions": sum(x.work_mode is not None for x in state.exclusions),
        "company_exclusions": 3,
        "history_coverage": "partial",
        "older_history": "unknown",
        "missing_intermediate_history": "unknown",
        "live_requests": 0,
        "expected_shortlist_count": len(sorted_shortlist(authoritative, 50)),
    }
    print(json.dumps(plan, indent=2), flush=True)
    if not args.apply:
        return

    archive = directory / f"state_repair_{now:%Y%m%dT%H%M%SZ}"
    archive.mkdir(exist_ok=False)
    backup_paths = {
        *[directory / name for name in [*SNAPSHOTS, *AUDITS]],
        *[
            path
            for key, path in paths.items()
            if key
            in {
                "raw",
                "manual_imports",
                "telegram_raw",
                "email_candidates",
                "linkedin_email_queue",
                "combined_json",
                "normalized",
                "combined_csv",
                "csv",
                "summary",
                "blocked",
                "role_review",
                "previous_combined_shortlist",
            }
        ],
    }
    manifest = []
    for path in sorted(backup_paths):
        if path.exists():
            shutil.copy2(path, archive / path.name)
            manifest.append({"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json(archive / "manifest.json", {"created_at": now.isoformat(), "files": manifest})
    write_json(archive / "repair_plan.json", state.model_dump(mode="json"))
    # Validate all closure targets before changing authoritative captures.
    capture_data = json.loads(paths["manual_imports"].read_text())
    captured_ids = {
        linkedin_job_id(str(item.get("record", item).get(field) or ""))
        for item in capture_data
        for field in ["page_url", "url", "canonical_url"]
    }
    if not set(closures) <= captured_ids:
        raise ValueError("A known closed vacancy has no authoritative capture")
    for job_id in closures:
        mark_linkedin_manual_expired(preferences, job_id, checked_at=AUDIT_DATE)
        update_queue_processed(
            preferences, job_id, {"pipeline_outcome": "expired", "blocker_reasons": [LINKEDIN_CLOSED_BLOCKER_REASON]}
        )
    write_json(paths["recovered_state"], state.model_dump(mode="json"))
    summary = rebuild_from_authoritative_sources(preferences, BatchStats())
    _, vacancies, _ = prepare_authoritative_vacancies(preferences)
    selected = sorted_shortlist(vacancies, 50)
    export_shortlist(vacancies, 50, paths["deduplicated_shortlist"])
    for source in ["headhunter", "linkedin", "telegram"]:
        export_source_shortlist(selected, source, 50, paths[f"{source}_shortlist"])
    assert paths["combined_shortlist"].read_bytes() == paths["deduplicated_shortlist"].read_bytes()
    assert all(not item.previously_exported and not item.blocker for item in selected)
    assert not any(vacancies_match(item, decision.identity) for item in selected for decision in state.exclusions)
    assert not any(vacancies_match(item, entry.identity) for item in selected for entry in state.history)
    plan.update(
        {
            "backup_directory": str(archive.relative_to(ROOT)),
            "remaining_shortlist": len(selected),
            "normalized_records": summary["normalized_records"],
            "previously_exported_count": summary["previously_exported_count"],
            "blocked_count": summary["blocked_count"],
            "new_candidates_availability": "not_rechecked",
        }
    )
    write_json(directory / "state_repair_summary.json", plan)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
