from __future__ import annotations

import httpx
import pytest

from job_assistant.config import load_preferences
from job_assistant.connectors.headhunter import HeadHunterConnector, _build_headers, _extract_records
from job_assistant.filters import _active_company_exclusion, apply_filters
from job_assistant.headhunter_state import RESPONSE_METADATA, successful_observation
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
    assert "period" not in client.params


@pytest.mark.parametrize("since_days", [1, 2, 3, 7, 14, 21, 30])
def test_headhunter_sends_requested_publication_window(since_days):
    preferences = load_preferences()
    connector = HeadHunterConnector(preferences, since_days=since_days)
    sample = preferences.sources.headhunter.samples[0]
    stats = BatchStats()

    class FakeClient:
        params = None

        def get(self, url, params=None):
            self.params = params
            return httpx.Response(200, json={"items": []}, request=httpx.Request("GET", str(url)))

    client = FakeClient()
    connector._request_page(client, sample, "Business Analyst", 0, stats)  # type: ignore[arg-type]

    assert client.params["period"] == since_days


def test_andersen_exclusion_is_active_through_configured_date_and_then_expires():
    from datetime import date

    preferences = load_preferences()

    assert _active_company_exclusion("Andersen Lab", preferences, today=date(2026, 11, 8)) == (
        "company temporarily excluded through 2026-11-08: Andersen"
    )
    assert _active_company_exclusion("Andersen Lab", preferences, today=date(2026, 11, 9)) is None
    assert _active_company_exclusion("Different Company", preferences, today=date(2026, 11, 8)) is None


def test_permanent_company_exclusion_never_expires():
    from datetime import date

    preferences = load_preferences()

    assert _active_company_exclusion("Bank of Georgia Group", preferences, today=date(2099, 1, 1)) == (
        "company permanently excluded: Bank of Georgia"
    )


@pytest.mark.parametrize(
    "company",
    ["REIZ TECH", "Specific Group", "Keepgo", "Dotmatics", "Mad Brains", "Selecty", "Sibedge", "Научсофт"],
)
def test_requested_temporary_company_exclusions_expire_after_two_months(company):
    from datetime import date

    preferences = load_preferences()

    assert "temporarily excluded through 2026-11-06" in (
        _active_company_exclusion(company, preferences, today=date(2026, 11, 6)) or ""
    )
    assert _active_company_exclusion(company, preferences, today=date(2026, 11, 7)) is None


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


@pytest.fixture
def paginated_connector(monkeypatch):
    preferences = load_preferences()
    preferences.sources.headhunter.samples = preferences.sources.headhunter.samples[:1]
    preferences.sources.headhunter.search_strings = ["Business Analyst"]
    preferences.sources.headhunter.per_page = 2
    preferences.run.pages_per_query = 3
    preferences.run.request_delay_seconds = 0
    preferences.run.max_retries = 0
    original_client = httpx.Client
    requests = []

    def run(pages, detail_failure=None):
        def respond(request):
            requests.append(request)
            if request.url.path.endswith("/vacancies"):
                page = int(request.url.params["page"])
                value = pages[page]
            else:
                vacancy_id = request.url.path.rsplit("/", 1)[-1]
                value = (
                    detail_failure
                    if detail_failure is not None
                    else {"id": vacancy_id, "name": "Business Analyst", "description": "Requirements analysis."}
                )
            if isinstance(value, int):
                return httpx.Response(value, request=request)
            return httpx.Response(200, json=value, request=request)

        monkeypatch.setattr(
            "job_assistant.connectors.headhunter.httpx.Client",
            lambda **kw: original_client(**kw, transport=httpx.MockTransport(respond)),
        )
        return HeadHunterConnector(preferences).fetch()

    return preferences, requests, run


def search_page(page, found, ids):
    return {
        "page": page,
        "pages": (found + 1) // 2,
        "found": found,
        "per_page": 2,
        "items": [{"id": value, "name": "Business Analyst"} for value in ids],
    }


@pytest.mark.parametrize(
    "found, pages",
    [
        (0, [search_page(0, 0, [])]),
        (3, [search_page(0, 3, ["1", "2"]), search_page(1, 3, ["3"])]),
    ],
)
def test_complete_hh_pagination_stops_at_last_page(paginated_connector, found, pages):
    _, requests, run = paginated_connector
    raw, stats = run(pages)
    assert stats.complete
    assert stats.errors == []
    assert len(raw) == found
    assert all(successful_observation(item) is not None for item in raw)
    assert len([request for request in requests if request.url.path.endswith("/vacancies")]) == len(pages)


@pytest.mark.parametrize(
    "pages",
    [
        [search_page(0, 3, ["1", "2"]), 503],
        [search_page(0, 3, ["1", "2"]), search_page(1, 3, [])],
        [search_page(0, 3, ["1", "2"]), search_page(1, 3, ["1"])],
        [search_page(0, 3, ["1", "2"]), search_page(0, 3, ["3"])],
        [search_page(0, 3, ["1", "2"]), search_page(1, 4, ["3", "4"])],
        [{"items": []}],
        [{**search_page(0, 0, []), "found": True}],
        [{**search_page(0, 8, ["1", "2"]), "pages": 1}],
        [{**search_page(0, 1, ["1"]), "items": [None]}],
        [{**search_page(0, 1, ["1"]), "items": [{}]}],
    ],
)
def test_partial_or_inconsistent_hh_search_is_never_complete(paginated_connector, pages):
    _, _, run = paginated_connector
    _, stats = run(pages)
    assert not stats.complete
    assert stats.errors


def test_hh_configured_page_cap_is_incomplete(paginated_connector):
    preferences, requests, run = paginated_connector
    preferences.run.pages_per_query = 1
    raw, stats = run([search_page(0, 3, ["1", "2"])])
    assert len(raw) == 2
    assert not stats.complete
    assert any("page limit" in error for error in stats.errors)
    assert len(requests) == 3  # One page and two details; no implicit expansion of the cap.


@pytest.mark.parametrize(
    "detail",
    [
        500,
        [],
        {},
        {"id": "other", "name": "Business Analyst", "description": "text"},
        {"id": "1", "name": "Business Analyst"},
    ],
)
def test_hh_detail_failure_or_bad_shape_is_incomplete(paginated_connector, detail):
    _, _, run = paginated_connector
    raw, stats = run([search_page(0, 1, ["1"])], detail_failure=detail)
    assert len(raw) == 1
    assert not stats.complete
    assert stats.errors
    assert RESPONSE_METADATA not in raw[0]["record"]


@pytest.mark.parametrize("valid", [True, False])
def test_hh_detail_by_id_marks_only_successful_complete_observations(paginated_connector, valid):
    preferences, _, run = paginated_connector
    run([search_page(0, 1, ["1"])], detail_failure=None if valid else {"id": "1", "name": "Business Analyst"})
    record, stats = HeadHunterConnector(preferences).fetch_vacancy_by_id("1")
    assert stats.complete is valid
    if valid:
        assert record[RESPONSE_METADATA]["origin"] == "detail"
        assert successful_observation(record) is not None
    else:
        assert record is None
        assert stats.errors


def test_failure_in_one_hh_query_prevents_replacement_of_whole_snapshot(paginated_connector):
    preferences, _, run = paginated_connector
    preferences.sources.headhunter.search_strings = ["Business Analyst", "Systems Analyst"]

    # The first query succeeds and the second fails at page zero.
    class Pages:
        calls = 0

        def __getitem__(self, index):
            self.calls += 1
            return search_page(0, 1, ["1"]) if self.calls == 1 else 503

    raw, stats = run(Pages())
    assert len(raw) == 1
    assert not stats.complete


def test_hh_without_enabled_samples_is_a_skip(monkeypatch, tmp_path):
    from job_assistant.cli import _fetch_source
    from job_assistant.models import FetchOutcome, fetch_outcome

    preferences = load_preferences()
    preferences.outputs.directory = str(tmp_path)
    for sample in preferences.sources.headhunter.samples:
        sample.enabled = False
    monkeypatch.setattr(HeadHunterConnector, "fetch", lambda self: pytest.fail("Empty configuration must not run"))
    raw, stats, skipped = _fetch_source(preferences, "headhunter", force=True)
    assert raw == []
    assert fetch_outcome(stats, skipped) is FetchOutcome.skipped
