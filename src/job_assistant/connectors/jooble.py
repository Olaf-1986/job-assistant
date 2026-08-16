from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from job_assistant.config import Preferences
from job_assistant.connectors.base import VacancyConnector
from job_assistant.models import BatchStats, RawRecord

LOGGER = logging.getLogger(__name__)


class JoobleConnector(VacancyConnector):
    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        stats = BatchStats()
        api_key = os.getenv("JOOBLE_API_KEY", "").strip()
        if not api_key:
            stats.errors.append("disabled_missing_credentials: JOOBLE_API_KEY is not set")
            return [], stats
        records: list[RawRecord] = []
        pages_to_fetch = int(getattr(self.preferences.sources.jooble, "pages_to_fetch", self.preferences.run.pages_per_query) or self.preferences.run.pages_per_query)
        result_on_page = int(getattr(self.preferences.sources.jooble, "result_on_page", 50) or 50)
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for query in self.preferences.all_queries:
                for location in ["Remote", "Tbilisi", "Georgia"]:
                    for page in range(1, pages_to_fetch + 1):
                        payload = {"keywords": query, "location": location, "page": page, "ResultOnPage": result_on_page, "companysearch": False}
                        stats.requests_made += 1
                        try:
                            response = client.post(f"https://jooble.org/api/{api_key}", json=payload)
                            response.raise_for_status()
                            data = response.json()
                        except (httpx.HTTPError, ValueError) as exc:
                            message = f"Jooble request failed query={query!r} location={location!r} page={page}: {exc}"
                            LOGGER.warning(message)
                            stats.errors.append(message)
                            continue
                        jobs = data.get("jobs", []) if isinstance(data, dict) else []
                        for job in jobs if isinstance(jobs, list) else []:
                            if isinstance(job, dict):
                                records.append({"query": query, "location": location, "record": {"__source": "jooble", **job}})
        stats.raw_records_received = len(records)
        return records, stats
