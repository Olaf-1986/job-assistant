from __future__ import annotations

import pytest

from job_assistant.config import load_preferences
from job_assistant.filters import apply_filters
from job_assistant.normalize import html_to_text, normalize_record, normalize_records
from job_assistant.scoring import score_vacancy
from tests.fixtures.headhunter_records import REMOTE_BA
from tests.fixtures.vacancy_records import BUSINESS_ANALYST, MALFORMED, RUSSIAN_ROLE


def test_html_to_text_preserves_paragraphs_and_bullets():
    text = html_to_text("<p>Intro</p><ul><li>One</li><li>Two</li></ul>")
    assert "Intro" in text
    assert "- One" in text
    assert "- Two" in text


def test_normalize_detects_language_and_skips_malformed():
    preferences = load_preferences()
    records = [
        {"query": "Business Analyst", "record": BUSINESS_ANALYST},
        {"query": "System Analyst", "record": RUSSIAN_ROLE},
        {"query": "Bad", "record": MALFORMED},
    ]
    normalized = normalize_records(records, preferences)
    assert len(normalized) == 2
    assert {item.detected_language for item in normalized} == {"en", "ru"}


def test_normalize_warns_on_invalid_salary_without_crashing():
    preferences = load_preferences()
    bad_salary = {**BUSINESS_ANALYST, "id": "bad-salary", "annualSalaryMin": "not-a-number"}
    normalized = normalize_records([{"query": "Business Analyst", "record": bad_salary}], preferences)
    assert len(normalized) == 1
    assert normalized[0].salary_min is None
    assert "invalid numeric value for annualSalaryMin" in normalized[0].warnings[0]


def test_linkedin_unsupported_language_is_blocked_before_role_and_location_filters():
    preferences = load_preferences()
    vacancy = normalize_record(
        {
            "__source": "linkedin",
            "page_url": "https://www.linkedin.com/jobs/view/9001",
            "document_title": "Business Analyst | Example Co | LinkedIn",
            "vacancy_title": "Business Analyst",
            "company": "Example Co",
            "visible_text": "Exigences, expérience, responsabilités et compétences. " * 10,
            "requirements_text": "Exigences, expérience, responsabilités et compétences.",
            "source_label": "linkedin",
        },
        "manual_capture",
        preferences,
    )

    assert vacancy is not None
    assert vacancy.detected_language == "fr"
    assert vacancy.blocker is True
    assert vacancy.blocker_reasons == ["unsupported LinkedIn job-content language: fr"]
    assert vacancy.work_mode is None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.role_relevance_breakdown == []
    assert filtered.blocker_reasons == ["unsupported LinkedIn job-content language: fr"]


def test_linkedin_full_description_is_used_for_blockers_but_requirements_text_has_scoring_priority():
    preferences = load_preferences()
    vacancy = normalize_record(
        {
            "__source": "linkedin",
            "page_url": "https://www.linkedin.com/jobs/view/9002",
            "document_title": "Business Analyst | Example Co | LinkedIn",
            "vacancy_title": "Business Analyst",
            "company": "Example Co",
            "visible_text": (
                "Business Analyst. Requirements analysis, BPMN and OpenAPI are required. "
                "Responsibilities include Jira administration and workflow maintenance. "
                "German required for customer communication."
            ),
            "selected_text": "Requirements analysis, BPMN and OpenAPI are required.",
            "requirements_text": "Requirements analysis, BPMN and OpenAPI are required.",
            "source_label": "linkedin",
        },
        "manual_capture",
        preferences,
    )

    assert vacancy is not None
    assert "German required" in (vacancy.description_text or "")
    assert vacancy.description_text != vacancy.requirements_text
    filtered = apply_filters([vacancy], preferences)[0]
    assert "explicit German language requirement" in filtered.blocker_reasons

    scored = score_vacancy(vacancy, preferences)
    assert "openapi" in scored.matched_signals
    assert "jira" not in scored.matched_signals


def test_linkedin_no_longer_accepting_is_an_initial_blocker():
    preferences = load_preferences()
    vacancy = normalize_record(
        {
            "__source": "linkedin",
            "page_url": "https://www.linkedin.com/jobs/view/9003",
            "document_title": "Business Analyst | Example Co | LinkedIn",
            "vacancy_title": "Business Analyst",
            "company": "Example Co",
            "visible_text": (
                "Business Analyst role with requirements analysis, responsibilities and stakeholder management. "
                "This job is no longer accepting applications."
            ),
            "source_label": "linkedin",
        },
        "manual_capture",
        preferences,
    )

    assert vacancy is not None
    assert vacancy.detected_language == "en"
    assert vacancy.blocker is True
    assert vacancy.blocker_reasons == ["LinkedIn vacancy is no longer accepting applications"]


def test_linkedin_no_longer_accepting_in_recommendations_is_not_a_blocker():
    preferences = load_preferences()
    vacancy = normalize_record(
        {
            "__source": "linkedin",
            "page_url": "https://www.linkedin.com/jobs/view/9004",
            "document_title": "Business Analyst | Example Co | LinkedIn",
            "vacancy_title": "Business Analyst",
            "company": "Example Co",
            "visible_text": (
                "Business Analyst role with requirements analysis, responsibilities and stakeholder management.\n"
                "People also viewed\n"
                "This recommended job is no longer accepting applications."
            ),
            "source_label": "linkedin",
        },
        "manual_capture",
        preferences,
    )

    assert vacancy is not None
    assert vacancy.blocker_reasons == []


def test_headhunter_tags_participate_in_role_filtering_and_scoring_without_changing_description():
    preferences = load_preferences()
    record = {
        **REMOTE_BA,
        "id": "tagged-vacancy",
        "name": "Implementation Consultant",
        "description": (
            "<p>Consulting role for delivery workshops, stakeholder collaboration and documentation.</p>"
            "<p>The position coordinates implementation planning and customer delivery.</p>"
        ),
        "key_skills": [
            {"name": "Requirements analysis"},
            {"name": "OpenAPI"},
            {"name": "BPMN"},
            {"name": "openapi"},
        ],
        "professional_roles": [{"name": "Business Analyst"}],
        "specializations": [{"name": "Systems analysis"}],
    }

    vacancy = normalize_record({"__source": "headhunter", **record}, "Business Analyst", preferences)

    assert vacancy is not None
    assert vacancy.categories == [
        "Requirements analysis",
        "OpenAPI",
        "BPMN",
        "Business Analyst",
        "Systems analysis",
    ]
    assert vacancy.source_metadata["analysis_tags"] == vacancy.categories
    assert "OpenAPI" not in (vacancy.description_text or "")

    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is False
    assert filtered.requires_manual_role_review is True
    assert filtered.role_relevance_breakdown == [
        "adjacent title with explicit BA/SA duties: ba_sa_requirements, modeling, systems_analysis"
    ]

    scored = score_vacancy(filtered, preferences)
    assert "openapi" in scored.matched_signals
    assert "bpmn" in scored.matched_signals


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({}, "missing required __source"),
        ({"__source": "telegram"}, "unknown __source: 'telegram'"),
    ],
)
def test_normalize_requires_known_explicit_source(record, message):
    with pytest.raises(ValueError, match=message):
        normalize_record(record, "test", load_preferences())


def test_normalize_rejects_conflicting_wrapper_and_record_sources():
    with pytest.raises(ValueError, match="Conflicting raw sources"):
        normalize_records(
            [{"__source": "headhunter", "query": "test", "record": {"__source": "linkedin"}}],
            load_preferences(),
        )
