from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_assistant.config import load_preferences
from job_assistant.deduplicate import deduplicate_vacancies, vacancies_match
from job_assistant.models import NormalizedVacancy
from job_assistant.normalize import normalize_record
from tests.fixtures.vacancy_records import BUSINESS_ANALYST


def test_deduplicate_preserves_queries_and_richest_description():
    preferences = load_preferences()
    first = normalize_record(BUSINESS_ANALYST, "Business Analyst", preferences)
    second_raw = {
        **BUSINESS_ANALYST,
        "jobDescription": "<p>Requirements gathering.</p><p>Extra rich text with BPMN and documentation.</p>",
    }
    second = normalize_record(second_raw, "Systems Analyst", preferences)
    assert first and second
    result, duplicates = deduplicate_vacancies([first, second])
    assert duplicates == 1
    assert len(result) == 1
    assert result[0].source_queries == ["Business Analyst", "Systems Analyst"]
    assert "Extra rich" in (result[0].description_text or "")


def test_deduplicate_merges_all_groups_connected_by_cross_source_bridge():
    shared_description = "business analysis requirements process documentation " * 3
    headhunter = _vacancy("headhunter", "hh-1", "https://hh.example.test/1", shared_description)
    linkedin = _vacancy(
        "linkedin",
        "li-1",
        "https://www.linkedin.com/jobs/view/123",
        "different but substantive LinkedIn description " * 3,
    )
    telegram = _vacancy(
        "telegram",
        "1001:10:0",
        "https://www.linkedin.com/jobs/view/123",
        shared_description,
    )

    result, duplicates = deduplicate_vacancies([headhunter, linkedin, telegram])

    assert duplicates == 2
    assert len(result) == 1
    assert result[0].sources == ["headhunter", "linkedin", "telegram"]
    assert result[0].source_ids == {
        "headhunter": ["hh-1"],
        "linkedin": ["li-1"],
        "telegram": ["1001:10:0"],
    }


def test_deduplicate_merges_near_identical_repost_with_different_urls_and_no_location():
    description = "Analyze requirements, integrations, APIs, and process documentation. " * 20
    first = _vacancy("linkedin", "li-1", "https://www.linkedin.com/jobs/view/100", description)
    second = _vacancy(
        "linkedin",
        "li-2",
        "https://www.linkedin.com/jobs/view/200",
        f"{description}Updated posting.",
    )
    first.company = second.company = "Example Group"

    result, duplicates = deduplicate_vacancies([first, second])

    assert vacancies_match(first, second)
    assert duplicates == 1
    assert len(result) == 1
    assert result[0].source_ids["linkedin"] == ["li-1", "li-2"]


def test_deduplicate_keeps_same_company_title_with_materially_different_descriptions():
    first = _vacancy(
        "linkedin",
        "li-1",
        "https://www.linkedin.com/jobs/view/100",
        "Analyze financial reporting, budgets, forecasts, and accounting controls. " * 10,
    )
    second = _vacancy(
        "linkedin",
        "li-2",
        "https://www.linkedin.com/jobs/view/200",
        "Analyze APIs, event streams, integration contracts, and distributed systems. " * 10,
    )
    first.company = second.company = "Example Group"

    result, duplicates = deduplicate_vacancies([first, second])

    assert not vacancies_match(first, second)
    assert duplicates == 0
    assert len(result) == 2


def test_deduplicate_keeps_near_identical_text_when_title_differs():
    description = "Analyze requirements, integrations, APIs, and process documentation. " * 20
    first = _vacancy("linkedin", "li-1", "https://www.linkedin.com/jobs/view/100", description)
    second = _vacancy("linkedin", "li-2", "https://www.linkedin.com/jobs/view/200", description + "Update")
    first.company = second.company = "Example Group"
    second.title = "Senior Business Analyst"
    second.normalized_title = "senior business analyst"

    result, duplicates = deduplicate_vacancies([first, second])

    assert duplicates == 0
    assert len(result) == 2


def test_deduplicate_matches_near_identical_description_when_sections_are_reordered():
    introduction = "Analyze business requirements and stakeholder needs. " * 20
    responsibilities = "Define API contracts and document system integrations. " * 20
    first = _vacancy(
        "linkedin",
        "li-1",
        "https://www.linkedin.com/jobs/view/100",
        introduction + responsibilities,
    )
    second = _vacancy(
        "linkedin",
        "li-2",
        "https://www.linkedin.com/jobs/view/200",
        responsibilities + introduction + "Updated posting.",
    )
    first.company = second.company = "Example Group"

    result, duplicates = deduplicate_vacancies([first, second])

    assert duplicates == 1
    assert len(result) == 1


@pytest.mark.parametrize("blocked_first", [False, True])
@pytest.mark.parametrize(
    "reason",
    ["LinkedIn vacancy is no longer accepting applications", "unsupported LinkedIn job-content language: fr"],
)
def test_deduplicate_preserves_initial_blockers_in_either_order(blocked_first, reason):
    first = _vacancy("headhunter", "hh-1", "https://example.test/job", "Requirements analysis")
    blocked = _vacancy("linkedin", "li-1", "https://example.test/job", "Requirements analysis")
    blocked.blocker = True
    blocked.blocker_reasons = [reason]

    result, duplicates = deduplicate_vacancies([blocked, first] if blocked_first else [first, blocked])

    assert duplicates == 1
    assert result[0].blocker is True
    assert result[0].blocker_reasons == [reason]


def test_deduplicate_matches_persisted_source_id_alias_after_url_changes():
    previous = _vacancy("headhunter", "hh-1", "https://example.test/old", "Old description")
    previous.source_ids = {"headhunter": ["hh-1"], "linkedin": ["li-1"]}
    restored = NormalizedVacancy.model_validate_json(previous.model_dump_json())
    current = _vacancy("linkedin", "li-1", "https://example.test/new", "Updated description")

    assert vacancies_match(restored, current)
    result, duplicates = deduplicate_vacancies([restored, current])

    assert duplicates == 1
    assert len(result) == 1
    assert result[0].source_ids == {"headhunter": ["hh-1"], "linkedin": ["li-1"]}


def _vacancy(source: str, source_id: str, application_url: str, description: str) -> NormalizedVacancy:
    return NormalizedVacancy(
        source=source,
        source_id=source_id,
        title="Business Analyst",
        normalized_title="business analyst",
        description_text=description,
        application_url=application_url,
        fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
