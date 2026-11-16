from datetime import UTC, datetime

import pytest

from job_assistant.config import load_preferences
from job_assistant.export import sorted_shortlist
from job_assistant.filters import GROSS_SALARY_WARNING, REMOTE_LOCATION_WARNING, apply_filters
from job_assistant.language import has_advanced_spanish_requirement
from job_assistant.location import classify_remote_eligibility
from job_assistant.models import NormalizedVacancy
from job_assistant.normalize import normalize_record
from job_assistant.telegram_parser import parse_telegram_message
from tests.fixtures.headhunter_records import REMOTE_BA
from tests.fixtures.telegram_messages import ORDINARY_VACANCY_TEXT, telegram_message


@pytest.mark.parametrize(
    "requirement",
    [
        "Spanish C1 required.",
        "Spanish: C2.",
        "C1/C2 Spanish is mandatory.",
        "Fluent in Spanish, both written and spoken.",
        "Fluent in\nSpanish, both written and spoken.",
        "Fluent in English and Spanish.",
        "English and Spanish fluency required.",
        "Native Spanish speaker required.",
        "Spanish fluency is essential.",
        "English B2 and Spanish C1 required.",
        "Spanish C1 required\nEnglish preferred.",
        "Spanish C1 required and English preferred.",
        "Обязательно свободное владение испанским языком.",
        "Испанский язык уровня C1 обязателен.",
        "Обязателен испанский язык на уровне C1; свободное владение для интервью.",
        "Español: C1 obligatorio.",
    ],
)
def test_advanced_spanish_requirement_is_a_shared_filter_blocker(requirement):
    vacancy = _vacancy(requirement)
    assert has_advanced_spanish_requirement(requirement)
    filtered = apply_filters([vacancy], load_preferences())[0]
    assert "advanced Spanish requirement exceeds supported proficiency" in filtered.blocker_reasons
    assert sorted_shortlist([filtered], 10) == []


@pytest.mark.parametrize(
    "requirement",
    [
        "Spanish B1 required.",
        "Spanish B2 required; English C1 required.",
        "Fluent English required; intermediate Spanish preferred.",
        "Fluent Spanish is optional.",
        "Spanish C1 is not required.",
        "Native Spanish is a nice-to-have.",
        "Spanish C2 preferred, English B2 required.",
        "Spanish C1 or English B2 required.",
        "English or fluent Spanish is required.",
        "Испанский C1 не требуется.",
        "Свободное владение испанским желательно.",
        "Español C1 deseable.",
        "We work with native Spanish speakers; English is required.",
    ],
)
def test_optional_intermediate_or_alternative_spanish_does_not_block(requirement):
    assert not has_advanced_spanish_requirement(requirement)
    filtered = apply_filters([_vacancy(requirement)], load_preferences())[0]
    assert not filtered.blocker
    assert sorted_shortlist([filtered], 10) == [filtered]


def _vacancy(extra="", **fields):
    return NormalizedVacancy(
        source="manual",
        title="Business Analyst",
        normalized_title="business analyst",
        description_text="Requirements analysis and stakeholder management. Work remotely with our team. " + extra,
        fetched_at=datetime.now(UTC),
        work_mode="remote",
        **fields,
    )


@pytest.mark.parametrize("location", ["Remote, Europe", "Remote — Europe", "Remote, EMEA", "Remote, CIS"])
def test_telegram_ambiguous_remote_regions_are_shortlisted_with_warning(location):
    preferences = load_preferences()
    text = ORDINARY_VACANCY_TEXT.replace("Remote worldwide", location)
    raw = parse_telegram_message(telegram_message(text), preferences).target_records[0]
    vacancy = apply_filters([normalize_record(raw, "synthetic", preferences)], preferences)[0]
    assert not vacancy.blocker
    assert vacancy.requires_manual_location_review
    assert REMOTE_LOCATION_WARNING in vacancy.warnings
    assert sorted_shortlist([vacancy], 10) == [vacancy]


def test_stored_telegram_europe_label_is_reclassified_without_refetch():
    preferences = load_preferences()
    text = ORDINARY_VACANCY_TEXT.replace("Remote worldwide", "Remote, Europe")
    raw = parse_telegram_message(telegram_message(text), preferences).target_records[0]
    raw["international_remote_eligibility"] = "explicit_restricted"
    vacancy = apply_filters([normalize_record(raw, "synthetic", preferences)], preferences)[0]
    assert not vacancy.blocker
    assert REMOTE_LOCATION_WARNING in vacancy.warnings
    assert sorted_shortlist([vacancy], 10) == [vacancy]


def test_description_restriction_overrides_stale_worldwide_metadata():
    vacancy = _vacancy(
        "Applicants must reside in an EU member state.",
        international_remote_eligibility="explicit_global",
    )
    filtered = apply_filters([vacancy], load_preferences())[0]
    assert "international remote eligibility restricted" in filtered.blocker_reasons
    assert sorted_shortlist([filtered], 10) == []


@pytest.mark.parametrize("text", ["Documentation is only in English.", "Reports are used only for internal reporting."])
def test_non_geographic_only_phrases_do_not_restrict_remote_work(text):
    vacancy = apply_filters([_vacancy(text)], load_preferences())[0]
    assert not vacancy.blocker
    assert sorted_shortlist([vacancy], 10) == [vacancy]


@pytest.mark.parametrize("source", ["headhunter", "linkedin", "telegram", "manual"])
@pytest.mark.parametrize(
    ("location_text", "blocked"),
    [
        ("Remote, Europe", False),
        ("Remote, EMEA working hours", False),
        ("Remote, EU only", True),
        ("Remote; must be based in Germany", True),
    ],
)
def test_geography_policy_is_shared_across_sources(source, location_text, blocked):
    vacancy = _vacancy(location_text).model_copy(update={"source": source})
    filtered = apply_filters([vacancy], load_preferences())[0]
    assert filtered.blocker is blocked
    if blocked:
        assert "international remote eligibility restricted" in filtered.blocker_reasons
        assert sorted_shortlist([filtered], 10) == []
    else:
        assert filtered.requires_manual_location_review
        assert REMOTE_LOCATION_WARNING in filtered.warnings
        assert sorted_shortlist([filtered], 10) == [filtered]


@pytest.mark.parametrize(
    ("text", "location", "expected"),
    [
        ("Remote, EU only", "Georgia", "explicit_restricted"),
        ("Remote worldwide; must be based in Germany", "Georgia", "explicit_restricted"),
        ("Remote; must be based in Georgia", None, "explicit_allowed_location"),
        ("Remote, only from Georgia", None, "explicit_allowed_location"),
        ("Remote, EU working hours", "Remote, EU working hours", None),
        ("Remote, US timezone", "Remote, US timezone", None),
        ("Remote worldwide", None, "explicit_global"),
    ],
)
def test_location_requirement_precedence_and_timezone_distinction(text, location, expected):
    assert classify_remote_eligibility(text, "remote", location) == expected


@pytest.mark.parametrize("source", ["manual", "rvc"])
def test_location_uncertainty_does_not_relax_unknown_work_mode_or_rvc_review(source):
    vacancy = _vacancy(requires_manual_location_review=True).model_copy(
        update={"source": source, "work_mode": "remote" if source == "rvc" else "unknown"}
    )
    filtered = apply_filters([vacancy], load_preferences())[0]
    assert "manual location review required" in filtered.blocker_reasons
    assert sorted_shortlist([filtered], 10) == []


@pytest.mark.parametrize("gross", [True, False, None])
@pytest.mark.parametrize("amount", [200_000, 230_000])
def test_headhunter_salary_basis_survives_normalization_and_net_threshold_policy(gross, amount):
    preferences = load_preferences()
    raw = {
        **REMOTE_BA,
        "__source": "headhunter",
        "salary": {"from": 180_000, "to": amount, "currency": "RUR", "gross": gross},
    }
    vacancy = apply_filters([normalize_record(raw, "synthetic", preferences)], preferences)[0]
    assert vacancy.salary_gross is gross
    assert vacancy.blocker is (amount <= 200_000)
    assert GROSS_SALARY_WARNING not in vacancy.warnings


@pytest.mark.parametrize("basis", ["net", "gross"])
@pytest.mark.parametrize("source", ["manual", "linkedin", "telegram"])
def test_structured_import_salary_preserves_period_and_basis(source, basis):
    raw = {
        "__source": source,
        "title": "Business Analyst",
        "vacancy_title": "Business Analyst",
        "text": "Requirements analysis and stakeholder management. Work remotely with our team.",
        "visible_text": "Requirements analysis and stakeholder management. Work remotely with our team.",
        "raw_text": "Requirements analysis and stakeholder management. Work remotely with our team.",
        "channel_id": "synthetic",
        "message_id": 1,
        "salary_min": 2_000_000,
        "salary_max": 2_400_000,
        "salary_currency": "RUB",
        "salary_period": "year",
        "salary_basis": basis,
    }
    preferences = load_preferences()
    vacancy = apply_filters([normalize_record(raw, "synthetic", preferences)], preferences)[0]
    assert vacancy.salary_period == "year"
    assert vacancy.salary_gross is (basis == "gross")
    assert "maximum monthly salary is at or below 200000 RUB equivalent" in vacancy.blocker_reasons


def test_old_normalized_records_without_salary_basis_remain_readable():
    record = _vacancy(salary_max=230_000, salary_currency="RUB").model_dump()
    record.pop("salary_gross")
    vacancy = NormalizedVacancy.model_validate(record)
    assert vacancy.salary_gross is None
    assert not apply_filters([vacancy], load_preferences())[0].blocker
