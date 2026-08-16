from __future__ import annotations

import logging
from typing import Any

import httpx

from job_assistant.config import Preferences
from job_assistant.connectors.base import VacancyConnector
from job_assistant.models import BatchStats, RawRecord

LOGGER = logging.getLogger(__name__)


class ArbeitnowConnector(VacancyConnector):
    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        stats = BatchStats()
        records: list[RawRecord] = []
        url: str | None = "https://www.arbeitnow.com/api/job-board-api"
        pages = int(getattr(self.preferences.sources.arbeitnow, "pages_to_fetch", 10) or 10)
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for _ in range(pages):
                if not url:
                    break
                stats.requests_made += 1
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    data: Any = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    message = f"Arbeitnow request failed url={url!r}: {exc}"
                    LOGGER.warning(message)
                    stats.errors.append(message)
                    break
                for job in data.get("data", []) if isinstance(data, dict) else []:
                    if isinstance(job, dict):
                        records.append({"query": "arbeitnow_feed", "record": {"__source": "arbeitnow", **job}})
                links = data.get("links", {}) if isinstance(data, dict) else {}
                url = links.get("next") if isinstance(links, dict) and isinstance(links.get("next"), str) else None
        stats.raw_records_received = len(records)
        return records, stats
