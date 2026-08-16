from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
from job_assistant.normalize import normalize_record
from tests.fixtures.himalayas_records import BUSINESS_ANALYST


def test_deduplicate_preserves_queries_and_richest_description():
    preferences = load_preferences()
    first = normalize_record(BUSINESS_ANALYST, "Business Analyst", preferences)
    second_raw = {**BUSINESS_ANALYST, "description": "<p>Requirements gathering.</p><p>Extra rich text with BPMN and documentation.</p>"}
    second = normalize_record(second_raw, "Systems Analyst", preferences)
    assert first and second
    result, duplicates = deduplicate_vacancies([first, second])
    assert duplicates == 1
    assert len(result) == 1
    assert result[0].source_queries == ["Business Analyst", "Systems Analyst"]
    assert "Extra rich" in (result[0].description_text or "")
