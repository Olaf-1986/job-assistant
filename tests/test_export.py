from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.export import export_all, sorted_shortlist
from job_assistant.models import BatchStats
from job_assistant.filters import apply_filters
from job_assistant.normalize import normalize_records
from job_assistant.scoring import score_vacancies
from tests.fixtures.vacancy_records import BUSINESS_ANALYST, ONSITE_OUTSIDE_TBILISI, STRONG_JIRA_ADMIN


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


def test_role_review_is_excluded_from_shortlist_and_exported_separately(tmp_path):
    preferences = load_preferences()
    preferences = preferences.model_copy(update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path)})})
    record = {**BUSINESS_ANALYST, "jobTitle": "Operations Analyst", "jobDescription": "<p>Elicit and analyze functional requirements with stakeholders.</p>"}
    vacancies = score_vacancies(apply_filters(normalize_records([{"query": "feed", "record": record}], preferences), preferences), preferences)

    summary = export_all([], vacancies, preferences, BatchStats(), ["feed"])

    assert vacancies[0].requires_manual_role_review
    assert sorted_shortlist(vacancies, 50) == []
    assert summary["role_review_count"] == 1
    assert "Operations Analyst" in (tmp_path / "role_review.md").read_text(encoding="utf-8")
