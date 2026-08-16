from __future__ import annotations

import logging

import httpx

from job_assistant.config import Preferences, load_target_companies
from job_assistant.connectors.base import VacancyConnector
from job_assistant.models import BatchStats, RawRecord

LOGGER = logging.getLogger(__name__)


class GreenhouseConnector(VacancyConnector):
    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        stats = BatchStats()
        companies = [item for item in load_target_companies().greenhouse.get("companies", []) if item.enabled]
        if not companies:
            stats.errors.append("empty_company_list: no enabled Greenhouse board tokens")
            return [], stats
        records: list[RawRecord] = []
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for company in companies:
                if not company.board_token:
                    continue
                stats.requests_made += 1
                url = f"https://boards-api.greenhouse.io/v1/boards/{company.board_token}/jobs?content=true"
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    data = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    message = f"Greenhouse request failed company={company.name!r}: {exc}"
                    LOGGER.warning(message)
                    stats.errors.append(message)
                    continue
                for job in data.get("jobs", []) if isinstance(data, dict) else []:
                    if isinstance(job, dict):
                        records.append({"query": "greenhouse_feed", "record": {"__source": "greenhouse", "__company": company.name, "__board_token": company.board_token, **job}})
        stats.raw_records_received = len(records)
        return records, stats
