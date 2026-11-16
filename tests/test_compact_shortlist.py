from datetime import UTC, datetime

import pytest

from job_assistant.config import load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
from job_assistant.export import _shortlist_markdown, export_all, export_shortlist, sorted_shortlist
from job_assistant.filters import REMOTE_LOCATION_WARNING
from job_assistant.models import BatchStats, NormalizedVacancy


def vacancy(**updates):
    values = {
        "source": "linkedin",
        "title": "Business Analyst",
        "normalized_title": "business analyst",
        "company": "Synthetic Company",
        "fetched_at": datetime(2026, 11, 7, tzinfo=UTC),
        "publication_date": datetime(2026, 11, 6, 12, 30, tzinfo=UTC),
        "apply_url": "https://example.test/jobs/one",
        "work_mode": "remote",
        "detected_language": "en",
        "score": 45,
        "matched_signals": ["jira", "requirements analysis"],
        "score_breakdown": ["+25 Jira user", "+20 core BA/SA"],
    }
    return NormalizedVacancy(**(values | updates))


def test_card_is_four_lines_without_optional_details_and_keeps_match_evidence():
    markdown = _shortlist_markdown([vacancy(employment_type="full-time", role_relevance_breakdown=["target title"])])
    card = markdown.split("## 1.")[1].strip().splitlines()
    assert len(card) == 4
    assert "Employment type" not in markdown
    assert "Role relevance" not in markdown
    assert "Published: 2026-11-06" in markdown
    assert "T12:30" not in markdown
    assert "- Match: +25 Jira user; +20 core BA/SA | Signals: jira, requirements analysis" in markdown
    assert "- Vacancy URL: https://example.test/jobs/one" in markdown


def test_identical_warnings_are_hidden_without_mutating_vacancies():
    first = vacancy(warnings=["check Georgia eligibility", "salary needs review"])
    second = vacancy(warnings=["salary needs review", "check Georgia eligibility"])
    markdown = _shortlist_markdown([first, second], notes=["Application history unavailable"])
    assert "- Warnings:" not in markdown
    assert "check Georgia eligibility" not in markdown
    assert "salary needs review" not in markdown
    assert "Warnings (all vacancies)" not in markdown
    assert "Application history unavailable" in markdown
    assert first.warnings == ["check Georgia eligibility", "salary needs review"]
    assert second.warnings == ["salary needs review", "check Georgia eligibility"]


def test_distinct_warnings_stay_with_their_cards_including_an_unwarned_card():
    markdown = _shortlist_markdown(
        [vacancy(warnings=["verify Georgia"]), vacancy(warnings=[]), vacancy(warnings=["verify salary"])]
    )
    cards = markdown.split("## ")[1:]
    assert "- Warnings: verify Georgia" in cards[0]
    assert "Warnings:" not in cards[1]
    assert "- Warnings: verify salary" in cards[2]
    assert "Warnings (all vacancies)" not in markdown


def test_common_warning_is_calculated_from_exported_selection(tmp_path):
    path = tmp_path / "shortlist.md"
    export_shortlist([vacancy(score=50, warnings=["shared"]), vacancy(warnings=["shared"]), vacancy(score=1)], 2, path)
    markdown = path.read_text()
    assert "shared" not in markdown
    assert "Warnings" not in markdown


def test_single_card_keeps_warning_and_empty_selection_has_no_shared_warning():
    assert "- Warnings: verify Georgia" in _shortlist_markdown([vacancy(warnings=["verify Georgia"])])
    assert "Warnings" not in _shortlist_markdown([])


def test_location_and_telegram_provenance_remain_visible():
    markdown = _shortlist_markdown(
        [
            vacancy(
                source="telegram",
                location_restrictions=["Europe"],
                source_metadata={
                    "channel_username": "synthetic_jobs",
                    "message_url": "https://t.me/synthetic_jobs/123",
                },
            )
        ]
    )
    assert "Location restrictions: Europe" in markdown
    assert "- Telegram channel: @synthetic_jobs | Telegram post: https://t.me/synthetic_jobs/123" in markdown


def test_remote_review_enters_exports_and_counts_but_unknown_mode_and_rvc_review_do_not(tmp_path):
    preferences = load_preferences()
    preferences.outputs.directory = str(tmp_path)
    remote = vacancy(requires_manual_location_review=True, warnings=["verify Georgia"])
    unknown = vacancy(work_mode=None, requires_manual_location_review=True)
    unreviewed_rvc = vacancy(source="rvc", requires_manual_location_review=True)
    records = [remote, unknown, unreviewed_rvc]
    assert sorted_shortlist(records, 10) == [remote]
    summary = export_all([], records, preferences, BatchStats(), [])
    assert summary["shortlist_count"] == summary["eligible_count"] == 1
    assert "Warnings: verify Georgia" in (tmp_path / "combined_shortlist.md").read_text()


def test_salary_keeps_gross_visible_and_missing_basis_uses_net():
    gross = vacancy(salary_max=300000, salary_currency="RUB", salary_gross=True)
    net = vacancy(salary_max=300000, salary_currency="RUB")
    assert "Salary: 300000.0 RUB gross" in _shortlist_markdown([gross])
    assert "Salary: 300000.0 RUB net" in _shortlist_markdown([net])


def test_export_adds_warning_for_legacy_remote_review_flag():
    legacy = vacancy(requires_manual_location_review=True)
    assert sorted_shortlist([legacy], 10) == [legacy]
    assert REMOTE_LOCATION_WARNING in _shortlist_markdown([legacy])
    assert legacy.warnings == []


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("other_basis", [None, False])
def test_duplicate_salary_keeps_explicit_gross_in_both_source_orders(reverse, other_basis):
    records = [
        vacancy(source="linkedin", salary_max=300000, salary_currency="RUB", salary_gross=other_basis),
        vacancy(source="headhunter", salary_max=300000, salary_currency="RUB", salary_gross=True),
    ]
    merged, count = deduplicate_vacancies(records[::-1] if reverse else records)
    assert count == 1
    assert merged[0].salary_gross is True
    assert "Salary: 300000.0 RUB gross" in _shortlist_markdown(merged)
    assert any("conflicting gross/net" in warning for warning in merged[0].warnings) is (other_basis is False)


def test_duplicate_does_not_attach_tax_basis_to_a_different_salary():
    merged, _ = deduplicate_vacancies(
        [
            vacancy(salary_max=250000, salary_currency="RUB", salary_gross=False),
            vacancy(source="headhunter", salary_max=300000, salary_currency="RUB", salary_gross=True),
        ]
    )
    assert merged[0].salary_max == 250000
    assert merged[0].salary_gross is False
