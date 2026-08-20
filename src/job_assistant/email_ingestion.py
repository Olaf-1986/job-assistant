from __future__ import annotations

import imaplib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import EmailMessage, Message
from email.policy import default
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from dotenv import dotenv_values

from .config import Preferences
from .connectors.headhunter import HeadHunterConnector
from .linkedin_queue import LEGACY_PENDING_STATUS, PENDING_STATUS, PENDING_STATUSES
from .models import BatchStats, RawRecord
from .paths import output_paths
from .utils import ROOT, canonical_url, normalize_space, read_json, write_json

LINKEDIN_RE = re.compile(r"/jobs/view/(\d+)")
HH_RE = re.compile(r"/vacancy/(\d+)")
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>'\")]+", re.IGNORECASE)


@dataclass
class EmailSyncResult:
    candidates: list[dict[str, Any]]
    headhunter_raw: list[RawRecord]
    linkedin_queue_count: int
    processed_message_count: int
    stats: BatchStats
    dry_run: bool = False


def sync_email_alerts(
    preferences: Preferences,
    since_days: int,
    dry_run: bool = False,
    full_refresh: bool = False,
    imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
    hh_lookup: Callable[[str], tuple[dict[str, Any] | None, BatchStats]] | None = None,
) -> EmailSyncResult:
    settings = load_email_settings()
    stats = BatchStats()
    state = _empty_state() if full_refresh else _load_state(preferences)
    existing_candidates = [] if full_refresh else _load_candidates(preferences)
    candidates_by_key = {_candidate_key(item): item for item in existing_candidates if _candidate_key(item)}
    headhunter_raw: list[RawRecord] = []
    processed_uids = set(state.get("processed_uids", {}).get(settings["mailbox"], []))
    processed_message_ids = set(state.get("processed_message_ids", []))
    client = None
    processed_count = 0
    try:
        client = imap_factory(settings["host"], settings["port"])
        client.login(settings["user"], settings["password"])
        status, _ = client.select(settings["mailbox"], readonly=True)
        if _bad_status(status):
            raise RuntimeError(
                f"email_select_failed: could not open configured mailbox {settings['mailbox']!r} read-only"
            )
        since = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = client.uid("SEARCH", None, "SINCE", since)
        if _bad_status(status):
            raise RuntimeError("email_search_failed: IMAP UID SEARCH failed")
        uids = _parse_search_uids(data)
        for uid in uids:
            if not full_refresh and uid in processed_uids:
                continue
            status, payload = client.uid("FETCH", uid, "(BODY.PEEK[])")
            if _bad_status(status):
                stats.errors.append(f"email_fetch_failed: uid={uid}")
                continue
            raw_message = _extract_fetch_bytes(payload)
            if raw_message is None:
                stats.errors.append(f"email_fetch_failed: empty body for uid={uid}")
                continue
            message = message_from_bytes(raw_message, policy=default)
            message_id = _message_id(message)
            if not full_refresh and message_id and message_id in processed_message_ids:
                processed_uids.add(uid)
                continue
            processed_count += 1
            for candidate in extract_candidates_from_message(message):
                key = _candidate_key(candidate)
                if not key or key in candidates_by_key:
                    continue
                if candidate["source"] == "headhunter" and not dry_run:
                    lookup = hh_lookup or HeadHunterConnector(preferences).fetch_vacancy_by_id
                    detail, lookup_stats = lookup(candidate["external_id"])
                    stats.requests_made += lookup_stats.requests_made
                    stats.raw_records_received += lookup_stats.raw_records_received
                    stats.errors.extend(lookup_stats.errors)
                    if detail is None:
                        candidate["status"] = "hh_lookup_failed"
                    else:
                        candidate["status"] = "imported"
                        headhunter_raw.append({"query": "email_alert", "record": detail})
                candidates_by_key[key] = candidate
            processed_uids.add(uid)
            if message_id:
                processed_message_ids.add(message_id)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass
    candidates = sorted(
        candidates_by_key.values(),
        key=lambda item: (str(item.get("received_at") or ""), item["source"], item["external_id"]),
        reverse=True,
    )
    if not dry_run:
        state["processed_uids"] = {
            **state.get("processed_uids", {}),
            settings["mailbox"]: sorted(processed_uids, key=_uid_sort_key),
        }
        state["processed_message_ids"] = sorted(processed_message_ids)
        _write_email_outputs(preferences, candidates, state)
    stats.raw_records_received += len(candidates)
    return EmailSyncResult(
        candidates=candidates,
        headhunter_raw=headhunter_raw,
        linkedin_queue_count=sum(
            1 for item in candidates if item["source"] == "linkedin" and item["status"] in PENDING_STATUSES
        ),
        processed_message_count=processed_count,
        stats=stats,
        dry_run=dry_run,
    )


def load_email_settings(env_path: Path | None = None) -> dict[str, Any]:
    values = dotenv_values(env_path or ROOT / ".env")
    host = str(os.getenv("EMAIL_IMAP_HOST") or values.get("EMAIL_IMAP_HOST") or "imap.gmail.com")
    port = int(str(os.getenv("EMAIL_IMAP_PORT") or values.get("EMAIL_IMAP_PORT") or "993"))
    user = str(os.getenv("EMAIL_IMAP_USER") or values.get("EMAIL_IMAP_USER") or "").strip()
    password = str(os.getenv("EMAIL_IMAP_APP_PASSWORD") or values.get("EMAIL_IMAP_APP_PASSWORD") or "").strip()
    mailbox = (
        str(os.getenv("EMAIL_IMAP_MAILBOX") or values.get("EMAIL_IMAP_MAILBOX") or "JobAlerts").strip() or "JobAlerts"
    )
    if not user or not password:
        raise RuntimeError("email_credentials_missing: set EMAIL_IMAP_USER and EMAIL_IMAP_APP_PASSWORD in .env")
    return {"host": host, "port": port, "user": user, "password": password, "mailbox": mailbox}


def extract_candidates_from_message(message: Message) -> list[dict[str, Any]]:
    message_id = _message_id(message)
    received_at = _received_at(message)
    subject = str(message.get("subject") or "")
    parts = _message_text_parts(message)
    anchors: dict[str, str] = {}
    texts: list[str] = []
    for content_type, text in parts:
        texts.append(text)
        if content_type == "text/html":
            anchors.update(_html_anchors(text))
    joined = "\n".join(texts)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_url in _extract_urls(joined):
        parsed = vacancy_url(raw_url)
        if parsed is None:
            continue
        source, external_id, canonical = parsed
        key = (source, external_id)
        if key in seen:
            continue
        seen.add(key)
        metadata_text = _context_for_url(joined, raw_url)
        anchor_title = anchors.get(raw_url) or anchors.get(canonical)
        candidates.append(
            {
                "source": source,
                "external_id": external_id,
                "title": _extract_field(metadata_text, "title") or anchor_title or _subject_title(subject),
                "company": _extract_field(metadata_text, "company"),
                "location": _extract_field(metadata_text, "location"),
                "canonical_url": canonical,
                "received_at": received_at,
                "message_id": message_id,
                "status": PENDING_STATUS if source == "linkedin" else "pending_hh_lookup",
            }
        )
    return candidates


def vacancy_url(raw_url: str) -> tuple[str, str, str] | None:
    raw_url = unescape(raw_url).strip().rstrip(".,);]")
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    path = parsed.path
    linkedin = LINKEDIN_RE.search(path)
    if linkedin and (host == "linkedin.com" or host.endswith(".linkedin.com")):
        external_id = linkedin.group(1)
        return "linkedin", external_id, f"https://www.linkedin.com/jobs/view/{external_id}"
    hh = HH_RE.search(path)
    if hh and _is_headhunter_host(host):
        external_id = hh.group(1)
        canonical = urlunparse((parsed.scheme or "https", host, f"/vacancy/{external_id}", "", "", ""))
        return "headhunter", external_id, canonical
    return None


def _write_email_outputs(preferences: Preferences, candidates: list[dict[str, Any]], state: dict[str, Any]) -> None:
    paths = output_paths(preferences)
    write_json(paths["email_candidates"], candidates)
    write_json(paths["email_state"], state)
    paths["linkedin_email_queue"].write_text(_linkedin_queue_markdown(candidates), encoding="utf-8")


def _linkedin_queue_markdown(candidates: list[dict[str, Any]]) -> str:
    lines = ["# LinkedIn Email Queue", ""]
    linkedin = [item for item in candidates if item["source"] == "linkedin" and item["status"] in PENDING_STATUSES]
    for item in linkedin:
        title = item.get("title") or "LinkedIn vacancy"
        company = item.get("company") or "Unknown company"
        lines.extend(
            [
                f"## {title} - {company}",
                f"- URL: [{item['canonical_url']}]({item['canonical_url']})",
                f"- Location: {item.get('location') or 'n/a'}",
                f"- Received: {item.get('received_at') or 'n/a'}",
                f"- Status: {PENDING_STATUS if item.get('status') == LEGACY_PENDING_STATUS else item.get('status')}",
                "",
            ]
        )
    return "\n".join(lines)


def _load_state(preferences: Preferences) -> dict[str, Any]:
    path = output_paths(preferences)["email_state"]
    if not path.exists():
        return _empty_state()
    data = read_json(path, _empty_state())
    return data if isinstance(data, dict) else _empty_state()


def _load_candidates(preferences: Preferences) -> list[dict[str, Any]]:
    path = output_paths(preferences)["email_candidates"]
    if not path.exists():
        return []
    data = read_json(path, [])
    return data if isinstance(data, list) else []


def _empty_state() -> dict[str, Any]:
    return {"processed_uids": {}, "processed_message_ids": []}


def _message_text_parts(message: Message) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            if content_type in {"text/plain", "text/html"}:
                parts.append((content_type, _part_text(part)))
    else:
        content_type = message.get_content_type()
        if content_type in {"text/plain", "text/html"}:
            parts.append((content_type, _part_text(message)))
    return parts


def _part_text(part: Message) -> str:
    if isinstance(part, EmailMessage):
        content = part.get_content()
        return content if isinstance(content, str) else str(content)
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return str(part.get_payload() or "")


def _html_anchors(html: str) -> dict[str, str]:
    anchors: dict[str, str] = {}
    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, re.IGNORECASE | re.DOTALL):
        href = unescape(match.group(1))
        text = normalize_space(re.sub(r"<[^>]+>", " ", unescape(match.group(2))))
        if text:
            anchors[href] = text
            parsed = vacancy_url(href)
            if parsed:
                anchors[parsed[2]] = text
    return anchors


def _extract_urls(text: str) -> list[str]:
    urls = [unescape(match.group(1)) for match in HREF_RE.finditer(text)]
    urls.extend(unescape(match.group(0)) for match in URL_RE.finditer(text))
    return urls


def _context_for_url(text: str, raw_url: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if raw_url in line or canonical_url(raw_url) in line:
            return "\n".join(lines[max(0, index - 4) : index + 5])
    return text[:1000]


def _extract_field(text: str, field: str) -> str | None:
    match = re.search(rf"\b{re.escape(field)}\s*:\s*(.+)", text, re.IGNORECASE)
    if not match:
        return None
    value = normalize_space(match.group(1))
    value = re.split(r"\s{2,}|\s+[A-Z][a-z]+:\s+", value, maxsplit=1)[0]
    return value[:300] if value else None


def _subject_title(subject: str) -> str | None:
    cleaned = normalize_space(subject)
    return cleaned[:300] if cleaned else None


def _received_at(message: Message) -> str | None:
    raw = message.get("date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(str(raw))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return None


def _message_id(message: Message) -> str | None:
    value = str(message.get("message-id") or "").strip()
    return value or None


def _is_headhunter_host(host: str) -> bool:
    return host == "hh.ru" or host.endswith(".hh.ru") or host.startswith("hh.") or host.startswith("headhunter.")


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, str] | None:
    source = candidate.get("source")
    external_id = candidate.get("external_id")
    if not source or not external_id:
        return None
    return str(source), str(external_id)


def _parse_search_uids(data: Any) -> list[str]:
    if not data:
        return []
    first = data[0]
    if isinstance(first, bytes):
        return [item for item in first.decode("ascii", errors="ignore").split() if item]
    if isinstance(first, str):
        return [item for item in first.split() if item]
    return []


def _extract_fetch_bytes(payload: Any) -> bytes | None:
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _bad_status(status: Any) -> bool:
    if isinstance(status, bytes):
        status = status.decode("ascii", errors="ignore")
    return str(status).upper() != "OK"


def _uid_sort_key(uid: str) -> tuple[int, str]:
    return (int(uid), uid) if uid.isdigit() else (0, uid)
