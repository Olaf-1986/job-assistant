from datetime import UTC, datetime

import pytest

from job_assistant.config import load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
from job_assistant.export import _salary
from job_assistant.filters import apply_filters
from job_assistant.location import detect_work_mode
from job_assistant.models import NormalizedVacancy
from job_assistant.normalize import normalize_record
from job_assistant.salary import net_amount
from tests.fixtures.headhunter_records import REMOTE_BA


def test_russian_gross_100_is_exactly_net_87_and_keeps_raw_amount():
    vacancy = NormalizedVacancy(
        source="manual",
        title="Business Analyst",
        normalized_title="business analyst",
        salary_max=100,
        salary_currency="RUB",
        salary_gross=True,
        fetched_at=datetime(2026, 11, 16, tzinfo=UTC),
    )
    assert net_amount(vacancy, vacancy.salary_max) == 87
    assert vacancy.salary_max == 100
    assert "~87 net; gross / (1 + 13/87)" in _salary(vacancy)


@pytest.mark.parametrize(
    ("country", "currency", "maximum", "minimum", "period", "blocked"),
    [
        (None, "RUR", 220_000, None, "month", True),
        (None, "RUB", 226_000, None, "month", True),
        (None, "RUB", 226_001, None, "month", True),
        (None, "RUB", 229_885, None, "month", True),
        (None, "RUB", 229_886, None, "month", False),
        (None, "RUB", 230_000, None, "month", False),
        ("Georgia", "RUB", 220_000, None, "month", False),
        ("Russia", "USD", 2500, None, "month", True),
        ("RU", "USD", 2700, None, "month", False),
        (None, "USD", 2500, None, "month", False),
        (None, "RUB", 2_640_000, None, "year", True),
        (None, "RUB", None, 169_499, "month", True),
        (None, "RUB", None, 169_500, "month", True),
        (None, "RUB", None, 170_000, "month", True),
        (None, "RUB", None, 172_413, "month", True),
        (None, "RUB", None, 172_414, "month", False),
        (None, "RUB", None, 173_000, "month", False),
    ],
)
def test_user_gross_assumption_controls_net_thresholds(country, currency, maximum, minimum, period, blocked):
    preferences = load_preferences()
    vacancy = normalize_record(
        {
            **REMOTE_BA,
            "__source": "headhunter",
            "hiring_country": country,
            "salary": {"from": minimum, "to": maximum, "currency": currency, "gross": True},
        },
        "synthetic",
        preferences,
    )
    vacancy.salary_period = period
    result = apply_filters([vacancy], preferences)[0]
    assert result.blocker is blocked
    assert (result.salary_min, result.salary_max) == (minimum, maximum)
    # Re-filtering must not deduct tax twice or change the displayed raw amount.
    assert apply_filters([result], preferences)[0].blocker is blocked


def test_explicit_hiring_country_in_description_overrides_currency_and_source():
    preferences = load_preferences()
    vacancy = normalize_record(
        {
            **REMOTE_BA,
            "__source": "headhunter",
            "description": (
                "<p>We need a business analyst to analyze requirements and document business processes. "
                "Work remotely with our international team from anywhere.</p><p>Hiring country: Georgia.</p>"
            ),
            "salary": {"from": None, "to": 220_000, "currency": "RUB", "gross": True},
        },
        "synthetic",
        preferences,
    )
    assert vacancy.hiring_country == "Georgia"
    assert not apply_filters([vacancy], preferences)[0].blocker
    assert "~220000" in _salary(vacancy) and "1% ignored" in _salary(vacancy)
    vacancy.hiring_country = "Россия"
    assert "~191400" in _salary(vacancy) and "gross / (1 + 13/87)" in _salary(vacancy)


def version(date, **updates):
    return NormalizedVacancy(
        **(
            {
                "source": "manual",
                "source_id": "same-opening",
                "title": "Business Analyst",
                "normalized_title": "business analyst",
                "publication_date": datetime.fromisoformat(date) if date else None,
                "fetched_at": datetime(2026, 11, 10, tzinfo=UTC),
                "description_text": (
                    "We need a business analyst to analyze requirements and document business processes."
                ),
                "work_mode": "remote",
                "description_completeness": "complete",
            }
            | updates
        )
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_posting_conditions_replace_older_longer_description_in_either_order(reverse):
    old = version(
        "2026-11-06T09:00:00Z",
        requirements_text="Spanish C1 is mandatory.",
        description_text="Analyze requirements and prepare process documentation. " * 20,
        salary_max=190_000,
        salary_currency="RUB",
        salary_gross=True,
    )
    new = version(
        "2026-11-06T13:01:00+04:00",
        requirements_text="English B2 is required.",
        salary_max=240_000,
        salary_currency="RUB",
        salary_gross=False,
    )
    vacancies, count = deduplicate_vacancies([new, old] if reverse else [old, new])
    assert count == 1
    result = apply_filters(vacancies, load_preferences())[0]
    assert not result.blocker
    assert result.requirements_text == "English B2 is required."
    assert result.salary_max == 240_000 and result.salary_gross is False
    assert result.description_text == new.description_text


@pytest.mark.parametrize("date", [None, "2026-11-06T13:00:00+04:00", "2026-11-06T12:00:00"])
@pytest.mark.parametrize("reverse", [False, True])
def test_missing_equal_or_naive_posting_datetime_keeps_conflicting_requirements(date, reverse):
    old = version("2026-11-06T09:00:00Z", requirements_text="Spanish C1 is mandatory.")
    new = version(date, requirements_text="English B2 is required.")
    result, _ = deduplicate_vacancies([new, old] if reverse else [old, new])
    filtered = apply_filters(result, load_preferences())[0]
    assert "advanced Spanish requirement exceeds supported proficiency" in filtered.blocker_reasons
    assert any("conflicting vacancy versions" in warning for warning in filtered.warnings)


@pytest.mark.parametrize("partial", [True, False])
def test_incomplete_or_email_received_date_cannot_remove_older_requirement(partial):
    old = version("2026-11-06T09:00:00Z", requirements_text="Spanish C1 is mandatory.")
    new = version(
        "2026-11-07T09:00:00Z",
        requirements_text="English B2 is required.",
        description_completeness="incomplete" if partial else "complete",
        source_metadata={} if partial else {"publication_date_basis": "received"},
    )
    result, _ = deduplicate_vacancies([old, new])
    assert apply_filters(result, load_preferences())[0].blocker


def test_newer_content_does_not_erase_confirmed_closure():
    old = version("2026-11-06T09:00:00Z", blocker=True, blocker_reasons=["HeadHunter vacancy is archived"])
    new = version("2026-11-07T09:00:00Z")
    result, _ = deduplicate_vacancies([old, new])
    assert "HeadHunter vacancy is archived" in result[0].blocker_reasons


@pytest.mark.parametrize(
    "text",
    [
        "Remote access to customer systems",
        "We work with remote clients.",
        "Distributed systems analysis",
        "This role is not remote.",
        "No remote work offered.",
        "Remote work is not available.",
        "Удалённый доступ к системам",
        "Удалёнка не предусмотрена.",
    ],
)
def test_remote_word_outside_work_arrangement_does_not_establish_remote(text):
    assert detect_work_mode(text, load_preferences()) == "unknown"


@pytest.mark.parametrize(
    "text",
    [
        "Remote Europe",
        "We work remotely.",
        "International remote team",
        "Remote access experience. Work from home.",
        "Работа удалённо.",
    ],
)
def test_positive_work_arrangements_remain_remote(text):
    assert detect_work_mode(text, load_preferences()) == "remote"
