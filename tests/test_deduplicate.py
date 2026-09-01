from __future__ import annotations

from datetime import UTC, datetime

from job_assistant.config import load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
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
