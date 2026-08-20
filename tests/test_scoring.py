from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.normalize import normalize_record
from job_assistant.scoring import score_vacancy
from tests.fixtures.vacancy_records import (
    BUSINESS_ANALYST,
    CRYPTO_ROLE,
    INTERNSHIP,
    JIRA_USER,
    JUNIOR_ROLE,
    STRONG_JIRA_ADMIN,
)


def scored(raw):
    preferences = load_preferences()
    vacancy = normalize_record(raw, "Business Analyst", preferences)
    assert vacancy is not None
    return score_vacancy(vacancy, preferences)


def test_jira_admin_scores_40_not_jira_user_double_count():
    vacancy = scored(STRONG_JIRA_ADMIN)
    assert any("+40 Jira" in line for line in vacancy.score_breakdown)
    assert not any("+20 Jira" in line for line in vacancy.score_breakdown)


def test_jira_user_scores_20():
    vacancy = scored(JIRA_USER)
    assert any("+20 Jira" in line for line in vacancy.score_breakdown)


def test_jira_admin_requires_atlassian_platform_context():
    raw = {
        **BUSINESS_ANALYST,
        "jobDescription": "<p>Define roles, workflows and automation for marketing operations.</p>",
    }
    vacancy = scored(raw)
    assert not any("+40 Jira" in line for line in vacancy.score_breakdown)


def test_english_language_does_not_add_score_bonus():
    vacancy = scored(BUSINESS_ANALYST)
    assert not any("English" in line for line in vacancy.score_breakdown)
    assert vacancy.score > 0


def test_negative_adjustments():
    assert scored(JUNIOR_ROLE).score < scored(BUSINESS_ANALYST).score
    assert scored(INTERNSHIP).score < scored(BUSINESS_ANALYST).score
    assert any("-10 crypto" in line for line in scored(CRYPTO_ROLE).score_breakdown)
