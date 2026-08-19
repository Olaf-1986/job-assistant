from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.normalize import html_to_text, normalize_records
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
