from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from job_assistant.config import HeadHunterSampleConfig, Preferences
from job_assistant.connectors.base import VacancyConnector
from job_assistant.headhunter_state import is_complete_hh_detail, observed_hh_detail
from job_assistant.models import BatchStats, RawRecord, with_raw_source
from job_assistant.role_relevance import title_prefilter_matches

LOGGER = logging.getLogger(__name__)


class HeadHunterConnector(VacancyConnector):
    def __init__(self, preferences: Preferences, since_days: int | None = None) -> None:
        self.preferences = preferences
        self.source_config = preferences.sources.headhunter
        self.since_days = since_days

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        records: list[RawRecord] = []
        stats = BatchStats(complete=False)
        token = os.getenv(self.source_config.access_token_env, "").strip()
        if self.source_config.require_authentication and not token:
            stats.errors.append(
                f"authentication_missing: set {self.source_config.access_token_env} "
                "after HeadHunter application approval"
            )
            return [], stats
        headers = _build_headers(
            self.source_config.user_agent, self.source_config.user_agent_env, self.source_config.access_token_env
        )
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        samples = [sample for sample in self.source_config.samples if sample.enabled]
        queries = self.source_config.search_strings or self.preferences.all_queries
        if not samples or not queries:
            stats.errors.append("HeadHunter search configuration is empty")
            return [], stats
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            for sample in samples:
                for query in queries:
                    pagination = None
                    seen_ids: set[str] = set()
                    for page in range(self.preferences.run.pages_per_query):
                        data = self._request_page(client, sample, query, page, stats)
                        if data is None:
                            stats.errors.append("Incomplete HeadHunter result: search page unavailable")
                            break
                        page_records = _extract_records(data, stats, sample.name, query, page)
                        current = _pagination(data, page, stats)
                        if current is None:
                            break
                        if pagination is not None and current != pagination:
                            stats.errors.append("Incomplete HeadHunter result: pagination changed during search")
                            break
                        pagination = current
                        pages, found, per_page = current
                        if len(page_records) != min(per_page, max(0, found - page * per_page)):
                            stats.errors.append("Incomplete HeadHunter result: unexpected page length")
                            break
                        stats.raw_records_received += len(page_records)
                        for record in page_records:
                            vacancy_id = str(record["id"])
                            if vacancy_id in seen_ids:
                                stats.errors.append(
                                    "Incomplete HeadHunter result: repeated vacancy across search pages"
                                )
                            seen_ids.add(vacancy_id)
                            detail = self._request_detail(client, record, sample, query, stats) or record
                            records.append(
                                {
                                    "query": query,
                                    "sample": sample.name,
                                    "page": page,
                                    "record": with_raw_source(detail, "headhunter", "HeadHunter API"),
                                }
                            )
                        if self.preferences.run.request_delay_seconds:
                            time.sleep(self.preferences.run.request_delay_seconds)
                        if page + 1 >= pages:
                            break
                    else:
                        stats.errors.append("Incomplete HeadHunter result: configured page limit reached")
        stats.complete = not stats.errors
        return records, stats

    def _request_page(
        self, client: httpx.Client, sample: HeadHunterSampleConfig, query: str, page: int, stats: BatchStats
    ) -> Any | None:
        params: dict[str, Any] = {
            "text": query,
            "page": page,
            "per_page": self.source_config.per_page,
            "search_field": sample.search_field,
        }
        if self.since_days is not None:
            params["period"] = self.since_days
        if sample.host:
            params["host"] = sample.host
        if sample.area is not None:
            params["area"] = sample.area
        if sample.areas:
            params["area"] = sample.areas
        if sample.schedule:
            params["schedule"] = sample.schedule
        if sample.work_formats:
            params["work_format"] = sample.work_formats
        for attempt in range(self.preferences.run.max_retries + 1):
            stats.requests_made += 1
            try:
                response = client.get(str(self.source_config.endpoint), params=params)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError("transient response", request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                transient = (
                    isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
                    or getattr(exc, "response", None) is not None
                    and exc.response.status_code in {429, 500, 502, 503, 504}
                )
                if not transient or attempt >= self.preferences.run.max_retries:
                    message = (
                        f"HeadHunter request failed for sample={sample.name!r} query={query!r} page={page}: "
                        f"{_format_http_error(exc)}"
                    )
                    LOGGER.warning(message)
                    stats.errors.append(message)
                    return None
                time.sleep(min(2**attempt, 8))
            except ValueError:
                message = "Invalid JSON from HeadHunter; contents omitted"
                LOGGER.warning(message)
                stats.errors.append(message)
                return None
        return None

    def _request_detail(
        self,
        client: httpx.Client,
        record: dict[str, Any],
        sample: HeadHunterSampleConfig,
        query: str,
        stats: BatchStats,
    ) -> Any | None:
        title = str(record.get("name") or "")
        if not title_prefilter_matches(title, self.preferences.role_relevance.relevant_title_keywords):
            return record
        vacancy_id = record.get("id")
        if not vacancy_id:
            return record
        detail_url = str(self.source_config.endpoint).rstrip("/") + f"/{vacancy_id}"
        try:
            stats.requests_made += 1
            response = client.get(detail_url)
            response.raise_for_status()
            detail = response.json()
            if not is_complete_hh_detail(detail, str(vacancy_id)):
                stats.errors.append("Incomplete HeadHunter result: malformed vacancy detail; contents omitted")
                return record
            return observed_hh_detail(detail, "search")
        except (httpx.HTTPError, ValueError) as exc:
            message = (
                f"HeadHunter detail request failed for sample={sample.name!r} query={query!r} id={vacancy_id!r}: "
                f"{_format_http_error(exc)}"
            )
            LOGGER.warning(message)
            stats.errors.append(message)
            return record

    def fetch_vacancy_by_id(self, vacancy_id: str) -> tuple[dict[str, Any] | None, BatchStats]:
        stats = BatchStats(complete=False)
        token = os.getenv(self.source_config.access_token_env, "").strip()
        if self.source_config.require_authentication and not token:
            stats.errors.append(
                f"authentication_missing: set {self.source_config.access_token_env} "
                "after HeadHunter application approval"
            )
            return None, stats
        headers = _build_headers(
            self.source_config.user_agent, self.source_config.user_agent_env, self.source_config.access_token_env
        )
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        detail_url = str(self.source_config.endpoint).rstrip("/") + f"/{vacancy_id}"
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            try:
                stats.requests_made += 1
                response = client.get(detail_url)
                response.raise_for_status()
                data = response.json()
                if is_complete_hh_detail(data, vacancy_id):
                    stats.raw_records_received = 1
                    stats.complete = True
                    return observed_hh_detail(data, "detail"), stats
                stats.errors.append("Malformed HeadHunter detail response; contents omitted")
            except (httpx.HTTPError, ValueError) as exc:
                message = (
                    f"HeadHunter detail request failed for email candidate id={vacancy_id!r}: {_format_http_error(exc)}"
                )
                LOGGER.warning(message)
                stats.errors.append(message)
        return None, stats


def _build_headers(
    configured_user_agent: str, user_agent_env: str = "HH_USER_AGENT", access_token_env: str = "HH_ACCESS_TOKEN"
) -> dict[str, str]:
    user_agent = os.getenv(user_agent_env, configured_user_agent).strip()
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
        "HH-User-Agent": user_agent,
    }
    access_token = os.getenv(access_token_env, "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _format_http_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    body = response.text.strip()
    lower_body = body.lower()
    if response.status_code in {401, 403} and "captcha" in lower_body:
        return (
            "captcha_required: HeadHunter requires OAuth or CAPTCHA resolution; "
            "configure HH_ACCESS_TOKEN, do not bypass CAPTCHA"
        )
    if response.status_code in {401, 403}:
        return (
            "authentication_required: HeadHunter access is restricted; configure HH_ACCESS_TOKEN; "
            f"http_status={response.status_code}"
        )
    return f"HeadHunter HTTP request failed; http_status={response.status_code}"


def _extract_records(data: Any, stats: BatchStats, sample: str, query: str, page: int) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        records = []
        for index, item in enumerate(items):
            if not (
                isinstance(item, dict)
                and isinstance(item.get("id"), (str, int))
                and not isinstance(item.get("id"), bool)
                and str(item["id"]).strip()
                and isinstance(item.get("name"), str)
                and item["name"].strip()
            ):
                message = (
                    f"HeadHunter response warning for sample={sample!r} query={query!r} page={page}: "
                    f"malformed item at index {index}; contents omitted"
                )
                LOGGER.warning(message)
                stats.errors.append(message)
            else:
                records.append(item)
        return records
    message = (
        f"HeadHunter response warning for sample={sample!r} query={query!r} page={page}: "
        "unexpected response shape; no items list found"
    )
    LOGGER.warning(message)
    stats.errors.append(message)
    return []


def _pagination(data: Any, page: int, stats: BatchStats) -> tuple[int, int, int] | None:
    fields = ("page", "pages", "found", "per_page")
    valid = isinstance(data, dict) and all(type(data.get(key)) is int for key in fields)
    if valid:
        pages, found, per_page = data["pages"], data["found"], data["per_page"]
        valid = (
            data["page"] == page
            and pages >= 0
            and found >= 0
            and per_page > 0
            and (pages in (0, 1) if found == 0 else pages == (found + per_page - 1) // per_page)
            and (page == 0 if found == 0 else page < pages)
        )
    if not valid:
        stats.errors.append("Incomplete HeadHunter result: missing, inconsistent or truncated pagination")
        return None
    return pages, found, per_page
