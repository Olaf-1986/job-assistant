import pytest

from job_assistant.config import load_preferences
from job_assistant.filters import apply_filters
from job_assistant.normalize import normalize_record
from tests.fixtures.headhunter_records import REMOTE_BA


def normalized_with_hiring_text(text, **fields):
    preferences = load_preferences()
    raw = {
        **REMOTE_BA,
        "__source": "headhunter",
        "description": (
            "<p>Business analyst to analyze requirements and document business processes. "
            f"Work remotely with our international team from anywhere. {text}</p>"
        ),
        "salary": {"from": None, "to": 225_000, "currency": "RUB", "gross": True},
        **fields,
    }
    vacancy = normalize_record(raw, "synthetic", preferences)
    return apply_filters([vacancy], preferences)[0]


@pytest.mark.parametrize(
    ("text", "country", "low_salary"),
    [
        ("The employer will hire you in Georgia.", "Georgia", False),
        ("We will employ you in Georgia.", "Georgia", False),
        ("You will be hired in Georgia.", "Georgia", False),
        ("Employment will be in Georgia.", "Georgia", False),
        ("Оформим вас в Грузии.", "Georgia", False),
        ("Вас оформят в Грузии.", "Georgia", False),
        ("Вы будете трудоустроены в Грузии.", "Georgia", False),
        ("Трудоустройство в Грузии.", "Georgia", False),
        ("The company will hire you in Russia.", "Russia", True),
        ("You will be employed in the Russian Federation.", "Russia", True),
        ("Оформим вас в России.", "Russia", True),
        ("We will hire you in Germany.", "Germany", False),
    ],
)
def test_explicit_hiring_country_in_natural_language_controls_salary_model(text, country, low_salary):
    vacancy = normalized_with_hiring_text(text)
    assert vacancy.hiring_country == country
    assert ("maximum monthly salary is at or below 200000 RUB equivalent" in vacancy.blocker_reasons) is low_salary


@pytest.mark.parametrize(
    "text",
    [
        "We work with customers in Georgia.",
        "The company has an office in Georgia.",
        "The employer will not hire you in Georgia.",
        "The employer will hire you in Tbilisi.",
        "The employer will hire you in Georgia or Russia.",
        "The employer will hire you in Georgia State.",
        "The employer will hire you in Georgia. You will be employed in Russia.",
    ],
)
def test_unrelated_negated_or_ambiguous_country_is_not_an_employment_country(text):
    vacancy = normalized_with_hiring_text(text)
    assert vacancy.hiring_country is None
    assert "maximum monthly salary is at or below 200000 RUB equivalent" in vacancy.blocker_reasons


def test_structured_hiring_country_precedes_description():
    vacancy = normalized_with_hiring_text("The employer will hire you in Georgia.", hiring_country="Russia")
    assert vacancy.hiring_country == "Russia"
    assert "maximum monthly salary is at or below 200000 RUB equivalent" in vacancy.blocker_reasons
