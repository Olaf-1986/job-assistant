from __future__ import annotations

import pytest

from job_assistant.config import load_preferences
from job_assistant.normalize import html_to_text, normalize_record, normalize_records
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
