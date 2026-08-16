from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.export import sorted_shortlist
from job_assistant.filters import apply_filters
from job_assistant.normalize import normalize_records
from job_assistant.scoring import score_vacancies
from tests.fixtures.himalayas_records import BUSINESS_ANALYST, ONSITE_OUTSIDE_TBILISI, STRONG_JIRA_ADMIN


def test_shortlist_excludes_blocked_and_orders_deterministically():
    preferences = load_preferences()
    records = [
        {"query": "Jira Administrator", "record": STRONG_JIRA_ADMIN},
        {"query": "Business Analyst", "record": BUSINESS_ANALYST},
        {"query": "Business Analyst", "record": ONSITE_OUTSIDE_TBILISI},
    ]
    vacancies = score_vacancies(apply_filters(normalize_records(records, preferences), preferences), preferences)
    shortlist = sorted_shortlist(vacancies, 50)
    assert all(not item.blocker for item in shortlist)
    assert shortlist[0].score >= shortlist[1].score
    assert len(shortlist) == 2
