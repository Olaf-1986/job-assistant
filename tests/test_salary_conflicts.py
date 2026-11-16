import csv
import io
import json
from datetime import UTC, datetime
from itertools import permutations

import pytest

from job_assistant.config import load_preferences
from job_assistant.deduplicate import SALARY_CONFLICT_WARNING, deduplicate_vacancies
from job_assistant.export import _csv_bytes, _salary
from job_assistant.filters import LOW_MAX_SALARY_REASON, apply_filters
from job_assistant.models import NormalizedVacancy


def offer(source_id, amount, *, currency="RUB", gross=True, published=None):
    return NormalizedVacancy(
        source="manual",
        source_id=source_id,
        source_url="https://example.test/jobs/same",
        title="Business Analyst",
        normalized_title="business analyst",
        description_text="We need a business analyst to analyze requirements and document business processes.",
        work_mode="remote",
        international_remote_eligibility="global",
        salary_max=amount,
        salary_currency=currency,
        salary_gross=gross,
        publication_date=published,
        fetched_at=datetime(2026, 11, 10, tzinfo=UTC),
    )


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_salary_conflict_retains_every_offer_and_does_not_depend_on_source_order(order):
    offers = [offer("a", 220_000), offer("b", 260_000), offer("c", 200_000, gross=False)]
    merged, count = deduplicate_vacancies([offers[i] for i in order])
    result = apply_filters(merged, load_preferences())[0]

    assert count == 2
    assert not result.blocker
    assert SALARY_CONFLICT_WARNING in result.warnings
    assert {(v.salary_max, v.salary_gross, v.source_id) for v in result.salary_variants} == {
        (220_000, True, "a"),
        (260_000, True, "b"),
        (200_000, False, "c"),
    }
    rendered = _salary(result)
    assert all(str(amount) in rendered for amount in (220_000, 260_000, 200_000))
    assert rendered.count("[manual]") == 3


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_all_salary_offers_must_be_known_and_below_threshold_to_block(unknown, reverse):
    offers = [offer("a", 220_000), offer("b", 200_000, currency="XTS" if unknown else "RUB", gross=False)]
    merged, _ = deduplicate_vacancies(offers[::-1] if reverse else offers)
    result = apply_filters(merged, load_preferences())[0]
    assert result.blocker is not unknown
    assert SALARY_CONFLICT_WARNING in result.warnings


def test_salary_variants_survive_serialization_and_repeated_merge():
    first, _ = deduplicate_vacancies([offer("a", 220_000), offer("b", 260_000)])
    restored = NormalizedVacancy.model_validate_json(first[0].model_dump_json())
    merged, _ = deduplicate_vacancies([restored, offer("c", 210_000), offer("b", 260_000)])
    result = apply_filters(merged, load_preferences())[0]
    assert not result.blocker
    assert {v.source_id for v in result.salary_variants} == {"a", "b", "c"}
    assert len(result.salary_variants) == 3
    assert SALARY_CONFLICT_WARNING in result.warnings


def test_csv_retains_all_conflicting_salary_offers_with_their_sources():
    first = offer("low", 220_000)
    first.hiring_country = "Russia"
    first.salary_period = "month"
    merged, _ = deduplicate_vacancies([first, offer("high", 260_000)])
    row = next(csv.DictReader(io.StringIO(_csv_bytes(merged).decode("utf-8"))))
    variants = json.loads(row["salary_variants"])
    assert {v["source_id"]: v["salary_max"] for v in variants} == {"low": 220_000, "high": 260_000}
    original = next(v for v in variants if v["source_id"] == "low")
    assert original["salary_gross"] is True
    assert (original["salary_currency"], original["salary_period"], original["hiring_country"]) == (
        "RUB",
        "month",
        "Russia",
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("other_blocker", [None, "HeadHunter vacancy is archived", "application already submitted"])
def test_refilter_after_merge_recomputes_salary_only(reverse, other_blocker):
    preferences = load_preferences()
    low = apply_filters([offer("a", 220_000)], preferences)[0]
    assert LOW_MAX_SALARY_REASON in low.blocker_reasons
    if other_blocker:
        low.blocker_reasons.append(other_blocker)
    low = NormalizedVacancy.model_validate_json(low.model_dump_json())
    high = offer("b", 260_000)
    merged, _ = deduplicate_vacancies([high, low] if reverse else [low, high])
    result = apply_filters(merged, preferences)[0]
    assert LOW_MAX_SALARY_REASON not in result.blocker_reasons
    assert result.blocker is bool(other_blocker)
    if other_blocker:
        assert other_blocker in result.blocker_reasons
    assert SALARY_CONFLICT_WARNING in result.warnings


def test_refilter_after_merge_keeps_salary_block_when_all_offers_are_too_low():
    preferences = load_preferences()
    low = apply_filters([offer("a", 220_000)], preferences)[0]
    merged, _ = deduplicate_vacancies([low, offer("b", 200_000)])
    result = apply_filters(merged, preferences)[0]
    assert LOW_MAX_SALARY_REASON in result.blocker_reasons
    assert result.blocker


def test_long_source_id_is_preserved_in_json_but_not_repeated_in_markdown():
    long_id = "https://example.test/jobs/" + "long-id-" * 40
    first = offer(long_id, 220_000)
    first.hiring_country = "Russia"
    first.salary_period = "month"
    second = offer("b", 260_000)
    merged, _ = deduplicate_vacancies([first, second])
    restored = NormalizedVacancy.model_validate_json(merged[0].model_dump_json())
    original = next(v for v in restored.salary_variants if v.source_id == long_id)
    assert (original.salary_max, original.salary_gross, original.salary_currency) == (220_000, True, "RUB")
    assert (original.hiring_country, original.salary_period) == ("Russia", "month")
    assert long_id not in _salary(restored)
    assert "[manual, hiring country: Russia]" in _salary(restored)
    assert SALARY_CONFLICT_WARNING in restored.warnings


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_same_amount_with_three_basis_markers_keeps_each_source_in_any_order(order):
    offers = [offer("a", 220_000, gross=True), offer("b", 220_000, gross=False), offer("c", 220_000, gross=None)]
    offers[0].warnings.append("unrelated warning")
    merged, _ = deduplicate_vacancies([offers[i] for i in order])
    result = apply_filters(merged, load_preferences())[0]
    assert not result.blocker
    assert {(v.source_id, v.salary_gross) for v in result.salary_variants} == {("a", True), ("b", False), ("c", None)}
    assert "unrelated warning" in result.warnings


def test_confirmed_newer_posting_supersedes_old_salary_without_conflict():
    old = offer("a", 220_000, published=datetime(2026, 11, 6, tzinfo=UTC))
    new = offer("b", 260_000, published=datetime(2026, 11, 7, tzinfo=UTC))
    old = apply_filters([old], load_preferences())[0]
    assert LOW_MAX_SALARY_REASON in old.blocker_reasons
    merged, _ = deduplicate_vacancies([old, new])
    result = apply_filters(merged, load_preferences())[0]
    assert not result.blocker
    assert result.salary_max == 260_000
    assert not result.salary_variants
    assert SALARY_CONFLICT_WARNING not in result.warnings


def test_salary_from_one_source_survives_other_source_without_salary():
    merged, _ = deduplicate_vacancies([offer("a", None), offer("b", 260_000)])
    assert merged[0].salary_max == 260_000
    assert not merged[0].salary_variants
