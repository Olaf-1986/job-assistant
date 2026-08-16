from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.filters import apply_filters, apply_hard_blockers, contains_phrase
from job_assistant.language import detect_language, has_explicit_german_requirement
from job_assistant.location import detect_work_mode
from job_assistant.normalize import normalize_record
from tests.fixtures.himalayas_records import BUSINESS_ANALYST, FOREIGN_CITIZENSHIP, GEORGIAN_AUTH, HYBRID_TBILISI, ONSITE_OUTSIDE_TBILISI, PURE_DEVELOPER


def normalized(raw):
    preferences = load_preferences()
    vacancy = normalize_record(raw, "Business Analyst", preferences)
    assert vacancy is not None
    return apply_hard_blockers(vacancy, preferences)


def test_hard_blockers_cover_developer_location_and_foreign_citizenship():
    assert normalized(PURE_DEVELOPER).blocker is True
    assert normalized(ONSITE_OUTSIDE_TBILISI).blocker is True
    assert normalized(FOREIGN_CITIZENSHIP).blocker is True


def test_tbilisi_hybrid_and_georgian_work_auth_are_not_blocked():
    assert normalized(HYBRID_TBILISI).blocker is False
    georgia = normalized(GEORGIAN_AUTH)
    assert georgia.blocker is False
    assert georgia.warnings


def test_generic_work_auth_compensation_sentence_is_not_blocked():
    raw = {
        **BUSINESS_ANALYST,
        "description": "<p>Remote role. Applicants must be authorized to work in the location. The salary for this role is competitive.</p>",
    }
    vacancy = normalized(raw)
    assert vacancy.blocker is False


def test_token_boundaries_for_intern_and_ai():
    assert contains_phrase("internal tooling", "intern") is False
    assert contains_phrase("maintain data", "ai") is False
    assert contains_phrase("AI platform", "ai") is True


def test_unknown_work_mode_does_not_default_to_remote():
    preferences = load_preferences()
    assert detect_work_mode("Business analyst in product team", preferences) == "unknown"


def test_german_language_and_requirement_detection():
    assert detect_language("Aufgaben und Anforderungen mit Deutschkenntnisse") == "de"
    assert detect_language("Business Analyst in Tbilisi / Тбилиси. Requirements analysis and BPMN.") == "en"
    assert detect_language("Системный аналитик. Анализ требований, BPMN, документация и интеграции.") == "ru"
    assert has_explicit_german_requirement("Fließend Deutsch erforderlich") is True


def test_explicit_german_requirement_blocks():
    preferences = load_preferences()
    vacancy = normalize_record({**BUSINESS_ANALYST, "description": "<p>Requirements analysis. Fließend Deutsch erforderlich.</p>"}, "Business Analyst", preferences)
    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is True
    assert "explicit German language requirement" in filtered.blocker_reasons


def test_bare_atlassian_engineering_title_stays_blocked():
    preferences = load_preferences()
    vacancy = normalize_record({**BUSINESS_ANALYST, "title": "Atlassian Platform Engineer", "description": "<p>Build internal platform with Jira integrations.</p>"}, "Atlassian", preferences)
    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is True
    assert "pure developer or engineering role" in filtered.blocker_reasons


def test_description_only_relevance_requires_two_strong_groups():
    preferences = load_preferences()
    weak = normalize_record({**BUSINESS_ANALYST, "title": "Operations Specialist", "description": "<p>Requirements gathering with stakeholders.</p>"}, "Business Analyst", preferences)
    strong = normalize_record({**BUSINESS_ANALYST, "title": "Operations Specialist", "description": "<p>Requirements gathering, BPMN and API integrations.</p>"}, "Business Analyst", preferences)
    assert weak is not None
    assert strong is not None
    assert apply_filters([weak], preferences)[0].blocker is True
    assert apply_filters([strong], preferences)[0].blocker is False
