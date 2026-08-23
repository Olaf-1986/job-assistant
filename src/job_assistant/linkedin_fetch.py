from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .capture import ManualCapturePayload, process_manual_capture
from .config import Preferences
from .linkedin_queue import (
    PENDING_STATUSES,
    canonical_linkedin_url,
    linkedin_job_id,
    load_queue,
    update_queue_processed,
    validate_linkedin_queue,
)
from .role_relevance import title_prefilter_matches
from .utils import ROOT, normalize_space

PROFILE_DIR = ROOT / "data" / "browser_profiles" / "linkedin"
MIN_DESCRIPTION_LENGTH = 200
MIN_READY_MAIN_TEXT_LENGTH = 500
FALLBACK_READY_MAIN_TEXT_LENGTH = 2_500
STOP_REASONS = {"login_required", "captcha", "account_restricted"}
VACANCY_SECTION_MARKERS = (
    "requirements",
    "must-have",
    "must have",
    "qualifications",
    "prerequisites",
    "требования",
)
IRRELEVANT_SECTION_MARKERS = (
    "people also viewed",
    "jobs you may like",
    "recommended jobs",
    "similar jobs",
)
_LOGIN_PATHS = {
    "/authwall",
    "/checkpoint",
    "/checkpoint/challenge",
    "/login",
    "/uas/login",
}


class LinkedInFetchError(RuntimeError):
    pass


class LinkedInStopRun(LinkedInFetchError):
    pass


@dataclass
class LinkedInVacancy:
    job_id: str
    canonical_url: str
    title: str
    company: str | None
    location: str | None
    description: str
    document_title: str


@dataclass
class LinkedInPageContent:
    page_url: str
    body_text: str
    main_text: str
    document_title: str


def extract_linkedin_vacancy(
    html: str,
    page_url: str,
    document_title: str = "LinkedIn",
    candidate_title: str | None = None,
    main_text: str | None = None,
) -> LinkedInVacancy:
    soup = BeautifulSoup(html, "html.parser")
    page_text = normalize_space(soup.get_text(" "))
    reason = classify_linkedin_page(page_url, page_text)
    if reason in STOP_REASONS:
        raise LinkedInStopRun(reason)
    if reason == "expired":
        raise LinkedInFetchError("expired")

    job_id = linkedin_job_id(page_url)
    if not job_id:
        raise LinkedInFetchError("extraction_failed: missing LinkedIn job id")

    main_text = main_text if main_text is not None else _main_text_from_html(soup)
    title, company = _document_title_parts(document_title)
    candidate_title = normalize_space(candidate_title or "")
    if candidate_title and _contains_normalized(main_text, candidate_title):
        title = candidate_title
    description = _description_from_main_text(main_text)
    if not title:
        raise LinkedInFetchError("extraction_failed: missing title")
    if len(description) < MIN_DESCRIPTION_LENGTH:
        raise LinkedInFetchError("extraction_failed: missing substantive description")
    return LinkedInVacancy(
        job_id=job_id,
        canonical_url=canonical_linkedin_url(job_id),
        title=title,
        company=company,
        location=None,
        description=description,
        document_title=document_title,
    )


def _contains_normalized(text: str, phrase: str) -> bool:
    return normalize_space(phrase).casefold() in normalize_space(text).casefold()


def _document_title_parts(document_title: str) -> tuple[str, str | None]:
    parts = [normalize_space(part) for part in document_title.split("|") if normalize_space(part)]
    parts = [part for part in parts if part.casefold() != "linkedin"]
    title = parts[0] if parts else ""
    company = parts[1] if len(parts) > 1 else None
    return title, company


def _main_text_from_html(soup: BeautifulSoup) -> str:
    main = soup.find("main")
    if main is None:
        return ""
    for element in main.select(
        "nav, footer, aside, form, button, script, style, [role='navigation'], [role='complementary']"
    ):
        element.decompose()
    return main.get_text("\n")


def _description_from_main_text(main_text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in main_text.splitlines():
        line = normalize_space(raw_line)
        lowered = line.casefold()
        if not line or lowered in seen:
            continue
        if any(marker in lowered for marker in IRRELEVANT_SECTION_MARKERS):
            break
        if lowered in {"skip to search", "skip to content", "sign in", "join now", "show more", "see more"}:
            continue
        seen.add(lowered)
        lines.append(line)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if any(marker in line.casefold() for marker in VACANCY_SECTION_MARKERS)
        ),
        0,
    )
    return "\n".join(lines[start:])


def _main_text_is_ready(main_text: str) -> bool:
    text = normalize_space(main_text)
    if len(text) < MIN_READY_MAIN_TEXT_LENGTH:
        return False
    lowered = text.casefold()
    return any(marker in lowered for marker in VACANCY_SECTION_MARKERS) or len(text) >= FALLBACK_READY_MAIN_TEXT_LENGTH


def classify_linkedin_page(url: str, text: str) -> str | None:
    lowered_text = text.lower()
    lowered_url_path = urlsplit(url).path.rstrip("/").lower() or "/"
    if "captcha" in lowered_text or "security verification" in lowered_text or "quick security check" in lowered_text:
        return "captcha"
    if lowered_url_path in _LOGIN_PATHS or re.search(r"\b(?:sign|log) in to linkedin\b", lowered_text):
        return "login_required"
    lowered = f"{lowered_url_path}\n{lowered_text}"
    if "account restricted" in lowered or "temporarily restricted" in lowered:
        return "account_restricted"
    if (
        "no longer accepting applications" in lowered
        or "job is no longer available" in lowered
        or "this job is no longer available" in lowered
    ):
        return "expired"
    return None


def fetch_pending_linkedin(
    preferences: Preferences,
    limit: int = 5,
    dry_run: bool = False,
    pause_seconds: float = 1.0,
    page_fetcher: Callable[[str], LinkedInPageContent | tuple[str, str]] | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise LinkedInFetchError("limit must be at least 1")
    close_fetcher = False
    if page_fetcher is None:
        page_fetcher = _playwright_page_fetcher(headless=False)
        close_fetcher = True
    try:
        candidates = load_queue(preferences)
        validate_linkedin_queue(candidates)
        stats: dict[str, Any] = {
            "opened": 0,
            "imported": 0,
            "skipped_title": 0,
            "expired": 0,
            "extraction_failed": 0,
            "errors": [],
        }
        processed = 0
        for item in candidates:
            if processed >= limit:
                break
            if (
                not isinstance(item, dict)
                or item.get("source") != "linkedin"
                or str(item.get("status") or "") not in PENDING_STATUSES
            ):
                continue
            job_id = str(item.get("external_id") or "")
            title = str(item.get("title") or "")
            if not title_prefilter_matches(title, preferences.role_relevance.relevant_title_keywords):
                stats["skipped_title"] += 1
                processed += 1
                if not dry_run and job_id:
                    update_queue_processed(
                        preferences,
                        job_id,
                        {"pipeline_outcome": "extraction_failed", "blocker_reasons": ["title_prefilter_rejected"]},
                    )
                continue
            url = str(item.get("canonical_url") or "")
            processed += 1
            try:
                result = page_fetcher(url)
                stats["opened"] += 1
                if isinstance(result, LinkedInPageContent):
                    vacancy = extract_linkedin_vacancy(
                        "",
                        result.page_url,
                        document_title=result.document_title,
                        candidate_title=title,
                        main_text=result.main_text,
                    )
                else:
                    html, document_title = result
                    vacancy = extract_linkedin_vacancy(html, url, document_title=document_title, candidate_title=title)
                if dry_run:
                    stats["imported"] += 1
                    continue
                _import_vacancy(preferences, vacancy)
                stats["imported"] += 1
            except LinkedInStopRun:
                raise
            except LinkedInFetchError as exc:
                reason = str(exc)
                if reason == "expired":
                    stats["expired"] += 1
                    if not dry_run and job_id:
                        update_queue_processed(preferences, job_id, {"pipeline_outcome": "expired"})
                else:
                    stats["extraction_failed"] += 1
                    stats["errors"].append(reason)
                    if not dry_run and job_id:
                        update_queue_processed(
                            preferences, job_id, {"pipeline_outcome": "extraction_failed", "blocker_reasons": [reason]}
                        )
            if pause_seconds:
                time.sleep(pause_seconds)
        return stats
    finally:
        if close_fetcher and hasattr(page_fetcher, "close"):
            page_fetcher.close()


def run_login() -> None:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        input("Log in to LinkedIn in the opened Chromium window, then press Enter here to close it.")
        context.close()


def _import_vacancy(preferences: Preferences, vacancy: LinkedInVacancy) -> dict[str, object]:
    payload = ManualCapturePayload(
        page_url=vacancy.canonical_url,
        document_title=vacancy.document_title,
        visible_text=vacancy.description,
        selected_text="",
        hostname="www.linkedin.com",
        captured_at=datetime.now(UTC).isoformat(),
        source_label="linkedin",
        vacancy_title=vacancy.title,
        company=vacancy.company,
    )
    return process_manual_capture(preferences, payload, auto_open_next=False)


def _playwright_page_fetcher(headless: bool = False) -> Callable[[str], LinkedInPageContent]:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
    page = context.pages[0] if context.pages else context.new_page()

    class Fetcher:
        def __call__(self, url: str) -> LinkedInPageContent:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            _wait_for_linkedin_page_or_stop(page)
            _expand_show_more(page)
            _wait_for_linkedin_page_or_stop(page)
            body_text = page.locator("body").inner_text(timeout=5_000)
            main_text = page.locator("main").first.inner_text(timeout=5_000)
            reason = classify_linkedin_page(page.url, body_text)
            if reason in STOP_REASONS:
                raise LinkedInStopRun(reason)
            if reason == "expired":
                raise LinkedInFetchError("expired")
            return LinkedInPageContent(page.url, body_text, main_text, page.title())

        def close(self) -> None:
            context.close()
            playwright.stop()

    return Fetcher()


def _wait_for_linkedin_page(page: Any) -> None:
    page.wait_for_function(
        """({ minimumLength, fallbackLength, markers }) => {
            const main = document.querySelector('main');
            if (!main) return false;
            const text = (main.innerText || '').trim();
            if (text.length < minimumLength) return false;
            const lowered = text.toLowerCase();
            return markers.some(marker => lowered.includes(marker)) || text.length >= fallbackLength;
        }""",
        arg={
            "minimumLength": MIN_READY_MAIN_TEXT_LENGTH,
            "fallbackLength": FALLBACK_READY_MAIN_TEXT_LENGTH,
            "markers": VACANCY_SECTION_MARKERS,
        },
        timeout=15_000,
    )


def _wait_for_linkedin_page_or_stop(page: Any) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        _wait_for_linkedin_page(page)
    except PlaywrightTimeoutError as exc:
        body_text = page.locator("body").inner_text(timeout=5_000)
        reason = classify_linkedin_page(page.url, body_text)
        if reason in STOP_REASONS:
            raise LinkedInStopRun(reason) from exc
        if reason == "expired":
            raise LinkedInFetchError("expired") from exc
        raise LinkedInFetchError("extraction_failed: page content did not become ready") from exc


def _expand_show_more(page: Any) -> None:
    patterns = ["Show more", "See more", "Показать еще", "Показать ещё"]
    for pattern in patterns:
        try:
            button = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE)).first
            if button.count():
                button.click(timeout=2_000)
                return
        except Exception:
            continue
