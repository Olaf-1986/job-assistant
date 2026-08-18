from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile
import webbrowser
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Preferences
from .paths import output_paths
from .utils import read_json

LINKEDIN_JOB_RE = re.compile(r"/jobs/view/(\d+)")
LINKEDIN_CANONICAL_RE = re.compile(r"^https://www\.linkedin\.com/jobs/view/(\d+)$")
PENDING_STATUS = "pending"
LEGACY_PENDING_STATUS = "manual_capture_required"
OPENED_STATUS = "opened"
CAPTURED_STATUS = "captured"
PROCESSED_STATUS = "processed"
FAILED_STATUS = "failed"
EXPIRED_STATUS = "expired"
PENDING_STATUSES = {PENDING_STATUS, LEGACY_PENDING_STATUS}


class LinkedInQueueError(RuntimeError):
    pass


def linkedin_job_id(url: str | None) -> str | None:
    if not url:
        return None
    match = LINKEDIN_JOB_RE.search(url)
    return match.group(1) if match else None


def canonical_linkedin_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def load_queue(preferences: Preferences) -> list[dict[str, Any]]:
    path = output_paths(preferences)["email_candidates"]
    if not path.exists():
        return []
    data = read_json(path)
    if not isinstance(data, list):
        raise LinkedInQueueError(f"LinkedIn queue must be a JSON list: {path}")
    return data


def write_queue(preferences: Preferences, candidates: list[dict[str, Any]]) -> None:
    path = output_paths(preferences)["email_candidates"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        import json

        json.dump(candidates, handle, ensure_ascii=False, indent=2, default=str)
    temp_path.replace(path)


def validate_linkedin_queue(candidates: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    errors: list[str] = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict) or item.get("source") != "linkedin":
            continue
        external_id = str(item.get("external_id") or "").strip()
        canonical_url = str(item.get("canonical_url") or "").strip()
        match = LINKEDIN_CANONICAL_RE.match(canonical_url)
        if not external_id or match is None or match.group(1) != external_id:
            errors.append(f"queue_id={index}: malformed LinkedIn URL/id external_id={external_id!r} canonical_url={canonical_url!r}")
            continue
        if external_id in seen:
            errors.append(f"queue_id={index}: duplicate LinkedIn job id {external_id}")
        seen.add(external_id)
    if errors:
        raise LinkedInQueueError("Invalid LinkedIn queue:\n" + "\n".join(errors))


def status_counts(candidates: list[dict[str, Any]]) -> Counter[str]:
    return Counter(_display_status(str(item.get("status") or "unknown")) for item in candidates if isinstance(item, dict) and item.get("source") == "linkedin")


def pending_linkedin_candidates(candidates: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    return [(index, item) for index, item in enumerate(candidates, start=1) if _is_pending_linkedin(item)]


def _is_pending_linkedin(item: Any) -> bool:
    return isinstance(item, dict) and item.get("source") == "linkedin" and str(item.get("status") or "") in PENDING_STATUSES


def _display_status(status: str) -> str:
    return PENDING_STATUS if status == LEGACY_PENDING_STATUS else status


def select_candidate(candidates: list[dict[str, Any]], selector: str) -> tuple[int, dict[str, Any]] | None:
    selector = selector.strip()
    if not selector:
        return None
    if selector.isdigit():
        queue_id = int(selector)
        if 1 <= queue_id <= len(candidates):
            item = candidates[queue_id - 1]
            if isinstance(item, dict) and item.get("source") == "linkedin":
                return queue_id, item
        for index, item in enumerate(candidates, start=1):
            if isinstance(item, dict) and item.get("source") == "linkedin" and str(item.get("external_id") or "") == selector:
                return index, item
    return None


def update_queue_after_capture(preferences: Preferences, page_url: str, captured_at: str | None = None) -> dict[str, Any]:
    candidates = load_queue(preferences)
    validate_linkedin_queue(candidates)
    job_id = linkedin_job_id(page_url)
    if not job_id:
        return {"matched": False, "job_id": None, "queue_id": None}
    for index, item in enumerate(candidates, start=1):
        if isinstance(item, dict) and item.get("source") == "linkedin" and str(item.get("external_id") or "") == job_id:
            if item.get("status") != PROCESSED_STATUS:
                item["status"] = CAPTURED_STATUS
                item["captured_at"] = captured_at or datetime.now(UTC).isoformat()
                item.pop("last_open_error", None)
                write_queue(preferences, candidates)
            return {"matched": True, "job_id": job_id, "queue_id": index, "candidate": item}
    return {"matched": False, "job_id": job_id, "queue_id": None}


def manual_import_has_linkedin_job(manual_imports_path: Path, job_id: str | None) -> bool:
    if not job_id or not manual_imports_path.exists():
        return False
    data = read_json(manual_imports_path)
    if not isinstance(data, list):
        return False
    canonical = canonical_linkedin_url(job_id)
    for item in data:
        record = item.get("record", item) if isinstance(item, dict) else {}
        if not isinstance(record, dict):
            continue
        if linkedin_job_id(str(record.get("page_url") or record.get("url") or "")) == job_id:
            return True
        if record.get("canonical_url") == canonical:
            return True
    return False


def open_url_default_browser(url: str) -> tuple[bool, str | None]:
    try:
        if _running_under_wsl():
            for command in (["wslview", url], ["powershell.exe", "-NoProfile", "-Command", "Start-Process", url], ["cmd.exe", "/C", "start", "", url]):
                if shutil.which(command[0]):
                    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                    return True, None
        return (True, None) if webbrowser.open(url) else (False, "default browser opener returned false")
    except Exception as exc:
        return False, str(exc)


def open_next_pending(preferences: Preferences, opener=None) -> dict[str, Any]:
    opener = opener or open_url_default_browser
    candidates = load_queue(preferences)
    validate_linkedin_queue(candidates)
    pending = pending_linkedin_candidates(candidates)
    if not pending:
        return {"opened": False, "queue_id": None, "url": None, "error": None}
    queue_id, item = pending[0]
    url = str(item.get("canonical_url") or "").strip()
    ok, error = opener(url)
    if not ok:
        item["last_open_error"] = error or "browser open failed"
        write_queue(preferences, candidates)
        return {"opened": False, "queue_id": queue_id, "url": url, "error": item["last_open_error"], "candidate": item}
    item["status"] = OPENED_STATUS
    item["opened_at"] = datetime.now(UTC).isoformat()
    item.pop("last_open_error", None)
    write_queue(preferences, candidates)
    return {"opened": True, "queue_id": queue_id, "url": url, "error": None, "candidate": item}


def update_queue_processed(preferences: Preferences, job_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
    candidates = load_queue(preferences)
    validate_linkedin_queue(candidates)
    for index, item in enumerate(candidates, start=1):
        if isinstance(item, dict) and item.get("source") == "linkedin" and str(item.get("external_id") or "") == job_id:
            item["status"] = PROCESSED_STATUS
            item["processed_at"] = datetime.now(UTC).isoformat()
            item["pipeline_outcome"] = outcome.get("pipeline_outcome")
            if outcome.get("score") is not None:
                item["score"] = outcome.get("score")
            if outcome.get("blocker_reasons") is not None:
                item["blocker_reasons"] = outcome.get("blocker_reasons")
            write_queue(preferences, candidates)
            return {"matched": True, "job_id": job_id, "queue_id": index, "candidate": item}
    return {"matched": False, "job_id": job_id, "queue_id": None}


def linkedin_pipeline_outcome(preferences: Preferences, job_id: str) -> dict[str, Any] | None:
    paths = output_paths(preferences)
    if not paths["combined_json"].exists():
        return None
    data = read_json(paths["combined_json"])
    if not isinstance(data, list):
        return None
    vacancy = _find_linkedin_vacancy(data, job_id)
    if vacancy is None:
        return {"pipeline_outcome": "extraction_failed"}
    blocked = bool(vacancy.get("blocker"))
    return {
        "pipeline_outcome": "blocked" if blocked else "shortlisted",
        "score": vacancy.get("score"),
        "blocker_reasons": vacancy.get("blocker_reasons") if blocked else [],
    }


def _find_linkedin_vacancy(records: list[Any], job_id: str) -> dict[str, Any] | None:
    canonical = canonical_linkedin_url(job_id)
    for item in records:
        if not isinstance(item, dict) or item.get("source") != "linkedin":
            continue
        urls = [item.get("source_url"), item.get("apply_url"), item.get("application_url"), *list(item.get("source_urls") or [])]
        if any(linkedin_job_id(str(url or "")) == job_id or str(url or "").rstrip("/") == canonical for url in urls):
            return item
    return None


def _running_under_wsl() -> bool:
    if "WSL_DISTRO_NAME" in os.environ:
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
