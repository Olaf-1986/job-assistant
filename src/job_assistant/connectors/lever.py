from __future__ import annotations

import logging

import httpx

from job_assistant.config import Preferences, load_target_companies
from job_assistant.connectors.base import VacancyConnector
from job_assistant.models import BatchStats, RawRecord

LOGGER = logging.getLogger(__name__)


class LeverConnector(VacancyConnector):
    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def fetch(self) -> tuple[list[RawRecord], BatchStats]:
        stats = BatchStats()
        companies = [item for item in load_target_companies().lever.get("companies", []) if item.enabled]
        if not companies:
            stats.errors.append("empty_company_list: no enabled Lever sites")
            return [], stats
        records: list[RawRecord] = []
        timeout = httpx.Timeout(self.preferences.run.request_timeout_seconds)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for company in companies:
                if not company.site:
                    continue
                host = "api.eu.lever.co" if company.region == "eu" else "api.lever.co"
                stats.requests_made += 1
                try:
                    response = client.get(f"https://{host}/v0/postings/{company.site}?mode=json")
                    response.raise_for_status()
                    data = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    message = f"Lever request failed company={company.name!r}: {exc}"
                    LOGGER.warning(message)
                    stats.errors.append(message)
                    continue
                for job in data if isinstance(data, list) else []:
                    if isinstance(job, dict):
                        records.append(
                            {
                                "query": "lever_feed",
                                "record": {
                                    "__source": "lever",
                                    "__company": company.name,
                                    "__site": company.site,
                                    "__region": company.region,
                                    **job,
                                },
                            }
                        )
        stats.raw_records_received = len(records)
        return records, stats
