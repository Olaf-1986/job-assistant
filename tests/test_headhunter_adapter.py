from __future__ import annotations

import httpx

from job_assistant.config import load_preferences
from job_assistant.connectors.headhunter import HeadHunterConnector, _build_headers, _extract_records
from job_assistant.filters import apply_filters
from job_assistant.models import BatchStats
from job_assistant.normalize import normalize_records
from job_assistant.scoring import score_vacancies
from tests.fixtures.headhunter_records import HEADHUNTER_RESPONSE


def test_headhunter_extracts_records_from_local_fixture():
    stats = BatchStats()
    records = _extract_records(HEADHUNTER_RESPONSE, stats, "remote", "Business Analyst", 0)
    assert len(records) == 2
    assert stats.errors == []


def test_headhunter_fetch_tags_raw_records_with_explicit_source(monkeypatch):
    preferences = load_preferences()
    connector = HeadHunterConnector(preferences)
    monkeypatch.setattr(connector, "_request_page", lambda *args: HEADHUNTER_RESPONSE)
    monkeypatch.setattr(connector, "_request_detail", lambda client, record, *args: record)

    records, _ = connector.fetch()

    assert records
    assert all(item["record"]["__source"] == "headhunter" for item in records)


def test_headhunter_headers_use_env_overrides(monkeypatch):
    monkeypatch.setenv("HH_USER_AGENT", "job-assistant/0.1 (contact: real@example.com)")
    monkeypatch.setenv("HH_ACCESS_TOKEN", "token-value")

    headers = _build_headers("job-assistant/0.1 (+local single-user batch)")

    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"] == "job-assistant/0.1 (contact: real@example.com)"
    assert headers["HH-User-Agent"] == "job-assistant/0.1 (contact: real@example.com)"
    assert headers["Authorization"] == "Bearer token-value"


def test_headhunter_normalizes_remote_and_tbilisi_fields():
    preferences = load_preferences()
    raw_records = [
        {
            "query": "Business Analyst",
            "sample": "remote",
            "record": {"__source": "headhunter", **HEADHUNTER_RESPONSE["items"][0]},
        },
        {
            "query": "Systems Analyst",
            "sample": "tbilisi_onsite_hybrid",
            "record": {"__source": "headhunter", **HEADHUNTER_RESPONSE["items"][1]},
        },
    ]
    vacancies = normalize_records(raw_records, preferences)
    remote, tbilisi = vacancies
    assert remote.source == "headhunter"
    assert remote.source_id == "123"
    assert remote.title == "Business Analyst"
    assert remote.company == "Acme"
    assert remote.location_restrictions == ["Россия"]
    assert remote.employment_type == "Полная занятость"
    assert remote.seniority == "От 3 до 6 лет"
    assert remote.salary_min == 3000
    assert remote.salary_max == 4500
    assert remote.salary_currency == "USD"
    assert remote.publication_date is not None
    assert remote.application_url == "https://hh.ru/vacancy/123"
    assert tbilisi.location_restrictions == ["Тбилиси"]
    assert tbilisi.detected_city == "Tbilisi"


def test_headhunter_ba_sa_records_pass_role_relevance_and_score():
    preferences = load_preferences()
    raw_records = [
        {"query": "Business Analyst", "sample": "remote", "record": {"__source": "headhunter", **record}}
        for record in HEADHUNTER_RESPONSE["items"]
    ]
    vacancies = score_vacancies(apply_filters(normalize_records(raw_records, preferences), preferences), preferences)
    assert all(not vacancy.blocker for vacancy in vacancies)
    assert all(vacancy.score > 0 for vacancy in vacancies)


def test_headhunter_remote_russian_systems_analyst_passes_title_and_location_filters():
    preferences = load_preferences()
    record = {
        **HEADHUNTER_RESPONSE["items"][0],
        "id": "russian-systems-analyst",
        "name": "Системный аналитик",
        "schedule": {"id": "fullDay", "name": "Полный день"},
        "work_format": ["REMOTE"],
        "snippet": {
            "requirement": "Анализ требований, BPMN и интеграции.",
            "responsibility": "Проектирование информационных систем.",
        },
    }

    vacancy = apply_filters(
        normalize_records(
            [{"query": "Системный аналитик", "sample": "remote", "record": {"__source": "headhunter", **record}}],
            preferences,
        ),
        preferences,
    )[0]

    assert vacancy.work_mode == "remote"
    assert vacancy.blocker is False
    assert "title is neither a configured target nor a reviewable adjacent role" not in vacancy.blocker_reasons
    assert "onsite or hybrid outside Tbilisi" not in vacancy.blocker_reasons


def test_headhunter_scoring_uses_keyword_near_end_of_full_description():
    preferences = load_preferences()
    filler = " ".join(["ordinary delivery context"] * 80)
    record = {
        **HEADHUNTER_RESPONSE["items"][0],
        "snippet": {"requirement": "Business Analyst work.", "responsibility": "Stakeholder collaboration."},
        "description": (
            f"<p>Business Analyst role with requirements analysis.</p><p>{filler}</p>"
            "<p>The final paragraph mentions OpenAPI.</p>"
        ),
    }

    vacancy = score_vacancies(
        apply_filters(
            normalize_records(
                [{"query": "Business Analyst", "sample": "remote", "record": {"__source": "headhunter", **record}}],
                preferences,
            ),
            preferences,
        ),
        preferences,
    )[0]

    assert vacancy.blocker is False
    assert "openapi" in vacancy.matched_signals
    assert any("api" in item for item in vacancy.score_breakdown)


def test_headhunter_requires_auth_before_batch(monkeypatch):
    monkeypatch.delenv("HH_ACCESS_TOKEN", raising=False)
    preferences = load_preferences()
    preferences = preferences.model_copy(
        update={
            "sources": preferences.sources.model_copy(
                update={
                    "headhunter": preferences.sources.headhunter.model_copy(update={"require_authentication": True})
                }
            )
        }
    )
    raw, stats = HeadHunterConnector(preferences).fetch()
    assert raw == []
    assert stats.errors == ["authentication_missing: set HH_ACCESS_TOKEN after HeadHunter application approval"]


def test_headhunter_auth_is_optional_by_default():
    assert load_preferences().sources.headhunter.require_authentication is False


def test_headhunter_sends_configured_search_and_work_format_params():
    preferences = load_preferences()
    connector = HeadHunterConnector(preferences)
    sample = preferences.sources.headhunter.samples[0]
    stats = BatchStats()

    class FakeClient:
        params = None

        def get(self, url, params=None):
            self.params = params
            return httpx.Response(200, json={"items": []}, request=httpx.Request("GET", str(url)))

    client = FakeClient()
    connector._request_page(client, sample, "Business Analyst", 0, stats)  # type: ignore[arg-type]

    assert client.params["text"] == "Business Analyst"
    assert client.params["page"] == 0
    assert client.params["per_page"] == preferences.sources.headhunter.per_page
    assert client.params["search_field"] == "name"
    assert client.params["host"] == "hh.ru"
    assert client.params["schedule"] == "remote"
    assert client.params["work_format"] == ["REMOTE"]


def test_headhunter_tbilisi_sample_sends_area_and_work_formats():
    preferences = load_preferences()
    connector = HeadHunterConnector(preferences)
    sample = preferences.sources.headhunter.samples[1]
    stats = BatchStats()

    class FakeClient:
        params = None

        def get(self, url, params=None):
            self.params = params
            return httpx.Response(200, json={"items": []}, request=httpx.Request("GET", str(url)))

    client = FakeClient()
    connector._request_page(client, sample, "Systems Analyst", 0, stats)  # type: ignore[arg-type]

    assert client.params["host"] == "headhunter.ge"
    assert client.params["area"] == 2758
    assert client.params["work_format"] == ["ON_SITE", "HYBRID"]


def test_russia_only_work_location_blocks_but_russian_citizenship_alone_does_not():
    preferences = load_preferences()
    base = HEADHUNTER_RESPONSE["items"][0]
    russia_only = {
        **base,
        "description": "<p>Business Analyst. Requirements analysis. Remote work only from Russia.</p>",
    }
    citizenship = {
        **base,
        "description": "<p>Business Analyst. Requirements analysis. Russian citizenship required.</p>",
    }
    filtered = apply_filters(
        normalize_records(
            [
                {"query": "Business Analyst", "record": {"__source": "headhunter", **russia_only}},
                {"query": "Business Analyst", "record": {"__source": "headhunter", **citizenship}},
            ],
            preferences,
        ),
        preferences,
    )

    assert filtered[0].blocker is True
    assert "work location restricted to Russia" in filtered[0].blocker_reasons
    assert filtered[1].blocker is False
