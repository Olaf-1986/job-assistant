from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.connectors.jobicy import _extract_records
from job_assistant.filters import apply_filters
from job_assistant.models import BatchStats
from job_assistant.normalize import normalize_records
from job_assistant.scoring import score_vacancies
from tests.fixtures.jobicy_records import JOBICY_RESPONSE


def test_jobicy_extracts_records_from_local_fixture():
    stats = BatchStats()
    records = _extract_records(JOBICY_RESPONSE, stats)
    assert len(records) == 2
    assert stats.errors == []


def test_jobicy_normalizes_core_fields_from_local_fixture():
    preferences = load_preferences()
    raw_records = [{"query": "jobicy", "record": record} for record in JOBICY_RESPONSE["jobs"]]
    vacancies = normalize_records(raw_records, preferences)
    atlassian = vacancies[0]
    assert atlassian.source == "jobicy"
    assert atlassian.source_id == "1001"
    assert atlassian.title == "Atlassian Consultant"
    assert atlassian.company == "Acme Tools"
    assert "Jira administration" in atlassian.description_text
    assert atlassian.location_restrictions == ["Anywhere"]
    assert atlassian.employment_type == "Full-Time"
    assert atlassian.seniority == "Senior"
    assert atlassian.salary_min == 70000
    assert atlassian.salary_max == 90000
    assert atlassian.salary_currency == "USD"
    assert atlassian.publication_date is not None
    assert atlassian.application_url == "https://jobicy.com/jobs/1001-atlassian-consultant"


def test_jobicy_role_relevance_keeps_ba_sa_for_scoring():
    preferences = load_preferences()
    raw_records = [{"query": "jobicy", "record": record} for record in JOBICY_RESPONSE["jobs"]]
    vacancies = score_vacancies(apply_filters(normalize_records(raw_records, preferences), preferences), preferences)
    relevant, business_analyst = vacancies
    assert relevant.blocker is False
    assert relevant.score > 0
    assert business_analyst.blocker is False
    assert business_analyst.score > 0
