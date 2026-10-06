from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from job_assistant.config import LanguagesConfig, load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
from job_assistant.export import sorted_shortlist
from job_assistant.filters import apply_filters
from job_assistant.language import detect_language
from job_assistant.models import NormalizedVacancy
from job_assistant.normalize import normalize_record

POLISH = (
    "Business Analyst. Requirements, responsibilities, experience, skills and analysis. "
    "Poszukujemy analityka do zespołu. Zakres obowiązków obejmuje współpracę z klientami, "
    "tworzenie dokumentacji oraz analizę wymagań biznesowych. Oferujemy pracę zdalną. "
    "Wymagamy znajomości SQL oraz doświadczenia w projektowaniu systemów."
)
POLISH_ASCII = (
    "Business Analyst. Requirements analysis with SQL and BPMN. "
    "Poszukujemy osoby do naszego zespolu. Bedziesz wspolpracowac z klientami, "
    "tworzyc dokumentacje oraz rozwijac procesy biznesowe. Wymagamy znajomosci systemow."
)


@pytest.mark.parametrize("text", [POLISH, POLISH_ASCII])
def test_polish_is_not_overridden_by_english_headings_or_technical_terms(text):
    assert detect_language(text) == "pl"


@pytest.mark.parametrize(
    "text",
    [
        "Exigences, expérience, responsabilités et compétences. Travail dans notre entreprise.",
        "Requisiti, esperienza e responsabilità. Cerchiamo conoscenza dei processi di lavoro.",
        "Experiência e competências para trabalhar no desenvolvimento da nossa empresa.",
        "Anforderungen und Erfahrung. Aufgaben mit unseren Kunden im Unternehmen.",
        "Vereisten en ervaring. Wij zoeken vaardigheden voor onze werkzaamheden.",
        "Požadavky a zkušenosti. Hledáme znalost procesů a nabízíme práci.",
        "Požiadavky a skúsenosti. Hľadáme znalosť procesov a ponúkame prácu.",
        "Изисквания и отговорности. Търсим опит и познания в нашия екип.",
        "Досвід та знання. Шукаємо аналітика для розробки нашої системи.",
        "Cerinte si experienta. Cautam cunostinte pentru echipa noastra.",
        "Persyaratan dan pengalaman. Kami mencari keterampilan untuk pekerjaan ini.",
        "Requirements and skills. Απαιτήσεις και εμπειρία για την ομάδα μας.",
        "Requirements and skills. საჭიროა გამოცდილება და ცოდნა სისტემების ანალიზში.",
        "Requirements and skills. 负责业务需求分析和系统设计，需要相关工作经验。",
        "Requirements and skills. تحليل المتطلبات وتطوير الأنظمة والعمل مع الفريق.",
        "",
        "SQL BPMN REST",
        "qwerty zxcvb asdfgh",
        "Requirements " + "qwerty zxcvb asdfgh " * 20,
    ],
)
def test_unsupported_and_unidentified_descriptions_fail_closed(text):
    assert detect_language(text) not in {"en", "ru", "es"}


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "Requirements analysis and stakeholder management. Work remotely with our team."),
        ("ru", "Анализ требований и документация. Работа с командой разработки, удаленно."),
        ("es", "Buscamos experiencia en análisis de requisitos y documentación de procesos de negocio."),
    ],
)
@pytest.mark.parametrize("source", ["linkedin", "headhunter", "telegram", "manual", "jobicy"])
def test_shared_filter_accepts_only_supported_description_languages(language, text, source):
    vacancy = NormalizedVacancy(
        source=source,
        title="Business Analyst",
        normalized_title="business analyst",
        description_text=text,
        detected_language="de",
        fetched_at=datetime.now(UTC),
        work_mode="remote",
    )
    filtered = apply_filters([vacancy], load_preferences())[0]
    assert filtered.detected_language == language
    assert not filtered.blocker
    assert sorted_shortlist([filtered], 10) == [filtered]


@pytest.mark.parametrize("source", ["linkedin", "headhunter", "telegram", "manual", "jobicy"])
@pytest.mark.parametrize("description", [POLISH, POLISH_ASCII, "", "gibberish xyzzy"])
def test_english_metadata_cannot_bypass_shared_description_blocker(source, description):
    vacancy = NormalizedVacancy(
        source=source,
        title="Business Analyst",
        normalized_title="business analyst",
        description_text=description,
        excerpt="Requirements analysis and stakeholder management",
        requirements_text="Requirements analysis and stakeholder management",
        detected_language="en",
        fetched_at=datetime.now(UTC),
        work_mode="remote",
    )
    filtered = apply_filters([vacancy], load_preferences())[0]
    assert filtered.blocker
    assert any(reason.startswith("unsupported description language:") for reason in filtered.blocker_reasons)
    assert sorted_shortlist([filtered], 10) == []


@pytest.mark.parametrize("source", ["linkedin", "manual"])
def test_normalization_cannot_trust_declared_english_over_polish_body(source):
    vacancy = normalize_record(
        {
            "__source": source,
            "manual_source": "linkedin",
            "vacancy_title": "Business Analyst",
            "title": "Business Analyst",
            "language": "en",
            "text": POLISH_ASCII,
            "url": "https://www.linkedin.com/jobs/view/12345",
        },
        "fixture",
        load_preferences(),
    )
    assert vacancy.detected_language == "pl"
    assert apply_filters([vacancy], load_preferences())[0].blocker


def test_spanish_linkedin_uses_configured_allowlist_before_role_filtering():
    vacancy = normalize_record(
        {
            "__source": "linkedin",
            "vacancy_title": "Business Analyst",
            "visible_text": "Buscamos experiencia en análisis de requisitos y documentación de procesos.",
        },
        "fixture",
        load_preferences(),
    )
    assert vacancy.detected_language == "es"
    assert not apply_filters([vacancy], load_preferences())[0].blocker


@pytest.mark.parametrize(
    ("text", "language", "blocked"),
    [
        ("", "unknown", True),
        ("Business Analyst\nRemote\nUse AI to assess how you fit\nLooking for talent?\nPost a job", "unknown", True),
        ("Business Analyst\nUse AI to assess how you fit\nAbout the job\n" + POLISH, "pl", True),
        (
            "Business Analyst\nAbout the job\nRequirements analysis and stakeholder management.\n"
            "Select language\nالعربية (Arabic)\n日本語 (Japanese)\nPolski (Polish)",
            "en",
            False,
        ),
        (
            "Acerca del empleo\nBuscamos experiencia en análisis de requisitos y documentación.\n"
            "Select language\nDeutsch (German)\n日本語 (Japanese)",
            "es",
            False,
        ),
    ],
)
def test_linkedin_language_uses_description_not_site_interface(text, language, blocked):
    vacancy = normalize_record(
        {"__source": "linkedin", "vacancy_title": "Business Analyst", "visible_text": text},
        "fixture",
        load_preferences(),
    )
    assert vacancy.detected_language == language
    assert apply_filters([vacancy], load_preferences())[0].blocker is blocked


def test_deduplication_cannot_hide_foreign_description_behind_english_label():
    english = NormalizedVacancy(
        source="headhunter",
        title="Business Analyst",
        normalized_title="business analyst",
        source_url="https://example.com/jobs/1",
        description_text="Requirements analysis and documentation.",
        detected_language="en",
        fetched_at=datetime.now(UTC),
    )
    polish = english.model_copy(update={"source": "linkedin", "description_text": POLISH})
    merged, count = deduplicate_vacancies([english, polish])
    assert count == 1
    filtered = apply_filters(merged, load_preferences())
    assert filtered[0].detected_language == "pl"
    assert sorted_shortlist(filtered, 10) == []


def test_language_settings_are_exactly_english_russian_spanish():
    assert set(load_preferences().languages.accepted) == {"en", "ru", "es"}


@pytest.mark.parametrize("language", ["pl", "de", "unknown"])
def test_configuration_rejects_unsupported_description_languages(language):
    with pytest.raises(ValidationError):
        LanguagesConfig(accepted=["en", language], russian_penalty=0)


@pytest.mark.parametrize("source", ["linkedin", "headhunter", "telegram", "manual", "jobicy"])
@pytest.mark.parametrize("requirement_field", ["description_text", "requirements_text"])
def test_english_description_with_mandatory_german_is_excluded(source, requirement_field):
    description = "Requirements analysis and stakeholder management. Work remotely with our team."
    vacancy = NormalizedVacancy(
        source=source,
        title="Business Analyst",
        normalized_title="business analyst",
        description_text=description,
        detected_language="en",
        fetched_at=datetime.now(UTC),
        work_mode="remote",
    )
    requirement = "Obligatory qualifications: Fluent in German, both written and spoken."
    setattr(vacancy, requirement_field, f"{getattr(vacancy, requirement_field) or ''}\n{requirement}")

    filtered = apply_filters([vacancy], load_preferences())[0]

    assert filtered.detected_language == "en"
    assert "explicit German language requirement" in filtered.blocker_reasons
    assert filtered.blocker
    assert sorted_shortlist([filtered], 10) == []
