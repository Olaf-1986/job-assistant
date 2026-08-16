from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from bs4 import BeautifulSoup

from .capture import ManualCapturePayload, process_manual_capture
from .config import Preferences
from .connectors.headhunter import _cheap_title_prefilter
from .linkedin_queue import PENDING_STATUSES, canonical_linkedin_url, linkedin_job_id, load_queue, update_queue_processed, validate_linkedin_queue
from .utils import ROOT, normalize_space

PROFILE_DIR = ROOT / "data" / "browser_profiles" / "linkedin"
MIN_DESCRIPTION_LENGTH = 200
STOP_REASONS = {"login_required", "captcha", "account_restricted"}


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


def extract_linkedin_vacancy(html: str, page_url: str, document_title: str = "LinkedIn") -> LinkedInVacancy:
    soup = BeautifulSoup(html, "html.parser")
    page_text = normalize_space(soup.get_text(" "))
    reason = classify_linkedin_page(page_url, page_text)
    if reason in STOP_REASONS:
        raise LinkedInStopRun(reason)
    if reason == "expired":
        raise LinkedInFetchError("expired")

    job_id = linkedin_job_id(page_url) or _job_id_from_html(soup)
    if not job_id:
        raise LinkedInFetchError("extraction_failed: missing LinkedIn job id")
    structured = _jobposting_from_jsonld(soup)
    title = _structured_text(structured.get("title")) if structured else None
    company = _organization_name(structured.get("hiringOrganization")) if structured else None
    location = _location_text(structured.get("jobLocation")) if structured else None
    description = _description_from_structured(structured) if structured else None

    title = title or _first_text(soup, ["h1", "[data-test-job-title]", ".job-details-jobs-unified-top-card__job-title", ".top-card-layout__title"])
    company = company or _first_text(soup, ["[data-test-job-company-name]", ".job-details-jobs-unified-top-card__company-name", ".topcard__org-name-link", ".top-card-layout__card .topcard__flavor"])
    location = location or _first_text(soup, ["[data-test-job-location]", ".job-details-jobs-unified-top-card__primary-description-container", ".topcard__flavor--bullet", ".job-search-card__location"])
    description = description or _first_text(soup, ["[data-test-job-description]", ".jobs-description__content", ".jobs-box__html-content", "#job-details", ".description__text"])

    title = normalize_space(title or "")
    description = normalize_space(description or "")
    if not title:
        raise LinkedInFetchError("extraction_failed: missing title")
    if len(description) < MIN_DESCRIPTION_LENGTH:
        raise LinkedInFetchError("extraction_failed: missing substantive description")
    return LinkedInVacancy(job_id=job_id, canonical_url=canonical_linkedin_url(job_id), title=title, company=normalize_space(company) if company else None, location=normalize_space(location) if location else None, description=description, document_title=document_title)


def classify_linkedin_page(url: str, text: str) -> str | None:
    lowered = f"{url}\n{text}".lower()
    if "/login" in lowered or "sign in" in lowered and "linkedin" in lowered or "join linkedin" in lowered:
        return "login_required"
    if "captcha" in lowered or "security verification" in lowered or "quick security check" in lowered:
        return "captcha"
    if "account restricted" in lowered or "temporarily restricted" in lowered:
        return "account_restricted"
    if "no longer accepting applications" in lowered or "job is no longer available" in lowered or "this job is no longer available" in lowered:
        return "expired"
    return None


def fetch_pending_linkedin(preferences: Preferences, limit: int = 5, dry_run: bool = False, pause_seconds: float = 1.0, page_fetcher: Callable[[str], tuple[str, str]] | None = None) -> dict[str, Any]:
    if limit < 1:
        raise LinkedInFetchError("limit must be at least 1")
    close_fetcher = False
    if page_fetcher is None:
        page_fetcher = _playwright_page_fetcher(headless=False)
        close_fetcher = True
    try:
        candidates = load_queue(preferences)
        validate_linkedin_queue(candidates)
        stats: dict[str, Any] = {"opened": 0, "imported": 0, "skipped_title": 0, "expired": 0, "extraction_failed": 0, "errors": []}
        processed = 0
        for item in candidates:
            if processed >= limit:
                break
            if not isinstance(item, dict) or item.get("source") != "linkedin" or str(item.get("status") or "") not in PENDING_STATUSES:
                continue
            job_id = str(item.get("external_id") or "")
            title = str(item.get("title") or "")
            if not _cheap_title_prefilter(title, preferences.role_relevance.relevant_title_keywords):
                stats["skipped_title"] += 1
                processed += 1
                if not dry_run and job_id:
                    update_queue_processed(preferences, job_id, {"pipeline_outcome": "extraction_failed", "blocker_reasons": ["title_prefilter_rejected"]})
                continue
            url = str(item.get("canonical_url") or "")
            processed += 1
            try:
                html, document_title = page_fetcher(url)
                stats["opened"] += 1
                vacancy = extract_linkedin_vacancy(html, url, document_title=document_title)
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
                        update_queue_processed(preferences, job_id, {"pipeline_outcome": "extraction_failed", "blocker_reasons": [reason]})
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


def _playwright_page_fetcher(headless: bool = False) -> Callable[[str], tuple[str, str]]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
    page = context.pages[0] if context.pages else context.new_page()

    class Fetcher:
        def __call__(self, url: str) -> tuple[str, str]:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            _expand_show_more(page)
            try:
                page.wait_for_timeout(500)
            except PlaywrightTimeoutError:
                pass
            return page.content(), page.title()

        def close(self) -> None:
            context.close()
            playwright.stop()

    return Fetcher()


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


def _jobposting_from_jsonld(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _jsonld_items(data):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if any(str(value).lower() == "jobposting" for value in types):
                return item
    return None


def _jsonld_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        items = [data]
        graph = data.get("@graph")
        if isinstance(graph, list):
            items.extend(item for item in graph if isinstance(item, dict))
        return items
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _description_from_structured(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    value = data.get("description")
    if not isinstance(value, str):
        return None
    return BeautifulSoup(value, "html.parser").get_text(" ")


def _structured_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _organization_name(value: Any) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
        return name if isinstance(name, str) else None
    return value if isinstance(value, str) else None


def _location_text(value: Any) -> str | None:
    locations = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for location in locations:
        if isinstance(location, str):
            parts.append(location)
        elif isinstance(location, dict):
            address = location.get("address")
            if isinstance(address, str):
                parts.append(address)
            elif isinstance(address, dict):
                parts.extend(str(address.get(key)) for key in ["addressLocality", "addressRegion", "addressCountry"] if address.get(key))
    return ", ".join(parts) or None


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            text = normalize_space(element.get_text(" "))
            if text:
                return text
    return None


def _job_id_from_html(soup: BeautifulSoup) -> str | None:
    for value in [soup.find("link", rel="canonical"), soup.find("meta", property="og:url")]:
        url = value.get("href") if value and value.name == "link" else value.get("content") if value else None
        job_id = linkedin_job_id(str(url or ""))
        if job_id:
            return job_id
    return None
