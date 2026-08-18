from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.filters import apply_filters, apply_hard_blockers, contains_phrase
from job_assistant.language import detect_language, has_explicit_german_requirement
from job_assistant.location import detect_work_mode
from job_assistant.normalize import normalize_record
from tests.fixtures.vacancy_records import BUSINESS_ANALYST, FOREIGN_CITIZENSHIP, GEORGIAN_AUTH, HYBRID_TBILISI, ONSITE_OUTSIDE_TBILISI, PURE_DEVELOPER


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
        "jobDescription": "<p>Remote role. Applicants must be authorized to work in the location. The salary for this role is competitive.</p>",
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
    vacancy = normalize_record({**BUSINESS_ANALYST, "jobDescription": "<p>Requirements analysis. Fließend Deutsch erforderlich.</p>"}, "Business Analyst", preferences)
    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is True
    assert "explicit German language requirement" in filtered.blocker_reasons


def test_bare_atlassian_engineering_title_stays_blocked():
    preferences = load_preferences()
    vacancy = normalize_record({**BUSINESS_ANALYST, "jobTitle": "Atlassian Platform Engineer", "jobDescription": "<p>Build internal platform with Jira integrations.</p>"}, "Atlassian", preferences)
    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is True
    assert "pure developer or engineering role" in filtered.blocker_reasons


def test_adjacent_title_with_explicit_analyst_duties_requires_role_review():
    preferences = load_preferences()
    weak = normalize_record({**BUSINESS_ANALYST, "jobTitle": "Operations Specialist", "jobDescription": "<p>Requirements gathering with stakeholders.</p>"}, "Business Analyst", preferences)
    strong = normalize_record({**BUSINESS_ANALYST, "jobTitle": "Operations Specialist", "jobDescription": "<p>Requirements gathering, BPMN and API integrations.</p>"}, "Business Analyst", preferences)
    assert weak is not None
    assert strong is not None
    assert apply_filters([weak], preferences)[0].requires_manual_role_review is True
    reviewed = apply_filters([strong], preferences)[0]
    assert reviewed.blocker is False
    assert reviewed.requires_manual_role_review is True


def test_role_relevance_three_state_regressions():
    preferences = load_preferences()

    def classify(title: str, description: str):
        vacancy = normalize_record({**BUSINESS_ANALYST, "jobTitle": title, "jobDescription": f"<p>{description}</p>"}, "Business Analyst", preferences)
        assert vacancy is not None
        return apply_filters([vacancy], preferences)[0]

    ai_manager = classify("AI Operations Manager", "Requirements, Jira, SQL, API integrations and documentation.")
    developer = classify("Software Developer", "Business analysis, requirements gathering, user stories and BPMN.")
    operations = classify("Operations Analyst", "Elicit and analyze functional requirements with stakeholders.")
    finance = classify("Finance System Analyst", "Support financial production systems.")
    business = classify("Business Analyst", "Requirements gathering.")
    atlassian = classify("Atlassian Administrator", "Administer Jira workflows and permissions.")

    assert ai_manager.blocker and not ai_manager.requires_manual_role_review
    assert developer.blocker and not developer.requires_manual_role_review
    assert not operations.blocker and operations.requires_manual_role_review
    assert not finance.blocker and not finance.requires_manual_role_review
    assert not business.blocker and not business.requires_manual_role_review
    assert not atlassian.blocker and not atlassian.requires_manual_role_review
