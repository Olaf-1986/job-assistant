from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from job_assistant.config import HeadHunterSampleConfig, Preferences
from job_assistant.connectors.base import VacancyConnector
from job_assistant.models import BatchStats, RawRecord
from job_assistant.role_relevance import title_prefilter_matches

LOGGER = logging.getLogger(__name__)


class HeadHunterConnector(VacancyConnector):
    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences
        self.source_config = preferences.sources.headhunter

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        records: list[RawRecord] = []
        stats = BatchStats()
        token = os.getenv(self.source_config.access_token_env, "").strip()
        if self.source_config.require_authentication and not token:
            stats.errors.append(f"authentication_missing: set {self.source_config.access_token_env} after HeadHunter application approval")
            return [], stats
        headers = _build_headers(self.source_config.user_agent, self.source_config.user_agent_env, self.source_config.access_token_env)
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        samples = [sample for sample in self.source_config.samples if sample.enabled]
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            for sample in samples:
                for query in self.source_config.search_strings or self.preferences.all_queries:
                    for page in range(self.preferences.run.pages_per_query):
                        data = self._request_page(client, sample, query, page, stats)
                        if data is None:
                            continue
                        page_records = _extract_records(data, stats, sample.name, query, page)
                        stats.raw_records_received += len(page_records)
                        for record in page_records:
                            detail = self._request_detail(client, record, sample, query, stats) or record
                            records.append({"query": query, "sample": sample.name, "page": page, "record": detail})
                        if self.preferences.run.request_delay_seconds:
                            time.sleep(self.preferences.run.request_delay_seconds)
        return records, stats

    def _request_page(self, client: httpx.Client, sample: HeadHunterSampleConfig, query: str, page: int, stats: BatchStats) -> Any | None:
        params: dict[str, Any] = {
            "text": query,
            "page": page,
            "per_page": self.source_config.per_page,
            "search_field": sample.search_field,
        }
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
                transient = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or getattr(exc, "response", None) is not None and exc.response.status_code in {429, 500, 502, 503, 504}
                if not transient or attempt >= self.preferences.run.max_retries:
                    message = f"HeadHunter request failed for sample={sample.name!r} query={query!r} page={page}: {_format_http_error(exc)}"
                    LOGGER.warning(message)
                    stats.errors.append(message)
                    return None
                time.sleep(min(2**attempt, 8))
            except ValueError as exc:
                message = f"Invalid JSON from HeadHunter for sample={sample.name!r} query={query!r} page={page}: {exc}"
                LOGGER.warning(message)
                stats.errors.append(message)
                return None
        return None

    def _request_detail(self, client: httpx.Client, record: dict[str, Any], sample: HeadHunterSampleConfig, query: str, stats: BatchStats) -> Any | None:
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
            return detail if isinstance(detail, dict) else record
        except (httpx.HTTPError, ValueError) as exc:
            message = f"HeadHunter detail request failed for sample={sample.name!r} query={query!r} id={vacancy_id!r}: {_format_http_error(exc)}"
            LOGGER.warning(message)
            stats.errors.append(message)
            return record

    def fetch_vacancy_by_id(self, vacancy_id: str) -> tuple[dict[str, Any] | None, BatchStats]:
        stats = BatchStats()
        token = os.getenv(self.source_config.access_token_env, "").strip()
        if self.source_config.require_authentication and not token:
            stats.errors.append(f"authentication_missing: set {self.source_config.access_token_env} after HeadHunter application approval")
            return None, stats
        headers = _build_headers(self.source_config.user_agent, self.source_config.user_agent_env, self.source_config.access_token_env)
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        detail_url = str(self.source_config.endpoint).rstrip("/") + f"/{vacancy_id}"
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            try:
                stats.requests_made += 1
                response = client.get(detail_url)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict):
                    stats.raw_records_received = 1
                    return data, stats
                stats.errors.append(f"HeadHunter detail response for id={vacancy_id!r} was not an object")
            except (httpx.HTTPError, ValueError) as exc:
                message = f"HeadHunter detail request failed for email candidate id={vacancy_id!r}: {_format_http_error(exc)}"
                LOGGER.warning(message)
                stats.errors.append(message)
        return None, stats


def _build_headers(configured_user_agent: str, user_agent_env: str = "HH_USER_AGENT", access_token_env: str = "HH_ACCESS_TOKEN") -> dict[str, str]:
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
        return "captcha_required: HeadHunter requires OAuth or CAPTCHA resolution; configure HH_ACCESS_TOKEN, do not bypass CAPTCHA"
    if response.status_code in {401, 403}:
        return f"authentication_required: HeadHunter access is restricted; configure HH_ACCESS_TOKEN; response_body={body[:500]!r}"
    if len(body) > 500:
        body = f"{body[:500]}..."
    return f"{exc}; response_body={body!r}"


def _extract_records(data: Any, stats: BatchStats, sample: str, query: str, page: int) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
        records = [item for item in items if isinstance(item, dict)]
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                message = f"HeadHunter response warning for sample={sample!r} query={query!r} page={page}: malformed non-object item at index {index}"
                LOGGER.warning(message)
                stats.errors.append(message)
        return records
    message = f"HeadHunter response warning for sample={sample!r} query={query!r} page={page}: unexpected response shape; no items list found"
    LOGGER.warning(message)
    stats.errors.append(message)
    return []
