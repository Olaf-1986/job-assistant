from __future__ import annotations

import logging
from typing import Any

import httpx

from job_assistant.config import Preferences
from job_assistant.connectors.base import VacancyConnector
from job_assistant.models import BatchStats, RawRecord

LOGGER = logging.getLogger(__name__)


class JobicyConnector(VacancyConnector):
    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences
        self.source_config = preferences.sources.jobicy

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        stats = BatchStats()
        headers = {"User-Agent": self.source_config.user_agent}
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
            data = self._request(client, stats)
        if data is None:
            return [], stats
        records = _extract_records(data, stats)
        stats.raw_records_received = len(records)
        return [{"query": "jobicy", "record": record} for record in records], stats

    def _request(self, client: httpx.Client, stats: BatchStats) -> Any | None:
        params = {"count": self.source_config.count}
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
                    message = f"Jobicy request failed: {exc}"
                    LOGGER.warning(message)
                    stats.errors.append(message)
                    return None
            except ValueError as exc:
                message = f"Invalid JSON from Jobicy: {exc}"
                LOGGER.warning(message)
                stats.errors.append(message)
                return None
        return None


def _extract_records(data: Any, stats: BatchStats) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        jobs = data["jobs"]
        records = [item for item in jobs if isinstance(item, dict)]
        for index, item in enumerate(jobs):
            if not isinstance(item, dict):
                message = f"Jobicy response warning: malformed non-object job at index {index}"
                LOGGER.warning(message)
                stats.errors.append(message)
        return records
    message = "Jobicy response warning: unexpected response shape; no jobs list found"
    LOGGER.warning(message)
    stats.errors.append(message)
    return []
