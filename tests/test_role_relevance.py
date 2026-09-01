from __future__ import annotations

import pytest

from job_assistant.config import load_preferences
from job_assistant.filters import apply_filters
from job_assistant.normalize import normalize_record
from job_assistant.role_relevance import title_prefilter_matches
from tests.fixtures.vacancy_records import BUSINESS_ANALYST

TARGET_TITLE_VARIANTS = (
    "Business Analyst",
    "Business Systems Analyst",
    "Business System Analyst",
    "System Analyst",
    "Systems Analyst",
    "Technical Business Analyst",
    "Requirements Analyst",
    "Business Requirements Analyst",
    "Functional Analyst",
    "Business Process Analyst",
    "Integration Analyst",
    "Бизнес-аналитик",
    "Бизнес аналитик",
    "Системный аналитик",
    "Бизнес-системный аналитик",
    "Бизнес системный аналитик",
    "Системный бизнес-аналитик",
    "Аналитик требований",
    "Аналитик по требованиям",
    "Функциональный аналитик",
    "Аналитик бизнес-процессов",
    "Аналитик бизнес процессов",
    "Аналитик информационных систем",
    "Аналитик ИТ-систем",
    "Интеграционный аналитик",
    "Аналитик интеграций",
)


def test_title_prefilter_matches_uses_phrase_boundaries():
    assert title_prefilter_matches("Internal Tools Engineer", ["intern"]) is False
    assert title_prefilter_matches("Business Analyst", ["business analyst"]) is True


@pytest.mark.parametrize("title", TARGET_TITLE_VARIANTS)
def test_target_title_variants_pass_headhunter_prefilter_and_role_relevance(title: str):
    preferences = load_preferences()

    assert title_prefilter_matches(title, preferences.role_relevance.relevant_title_keywords)
    vacancy = normalize_record({**BUSINESS_ANALYST, "jobTitle": title}, "Business Analyst", preferences)

    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is False
    assert filtered.role_relevance_breakdown == ["title matches configured target title"]


@pytest.mark.parametrize("title", ["senior business analyst", "LEAD Системный аналитик"])
def test_target_title_matching_is_case_insensitive_and_accepts_seniority_prefixes(title: str):
    preferences = load_preferences()

    assert title_prefilter_matches(title, preferences.role_relevance.relevant_title_keywords)


@pytest.mark.parametrize(
    "title",
    ["Data Analyst", "Financial Analyst", "Marketing Analyst", "Solution Architect", "Software Engineer"],
)
def test_non_target_analyst_and_engineering_titles_do_not_match_target_titles(title: str):
    preferences = load_preferences()

    assert not title_prefilter_matches(title, preferences.role_relevance.relevant_title_keywords)
    vacancy = normalize_record({**BUSINESS_ANALYST, "jobTitle": title}, "Business Analyst", preferences)

    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.role_relevance_breakdown != ["title matches configured target title"]


def test_product_manager_remains_eligible_but_low_priority():
    preferences = load_preferences()
    vacancy = normalize_record({**BUSINESS_ANALYST, "jobTitle": "Product Manager"}, "Product Manager", preferences)

    assert vacancy is not None
    filtered = apply_filters([vacancy], preferences)[0]
    assert filtered.blocker is False
    assert filtered.role_relevance_breakdown == ["title matches configured target title"]
