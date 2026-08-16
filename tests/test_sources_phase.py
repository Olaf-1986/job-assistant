from __future__ import annotations

import httpx
from typer.testing import CliRunner

from job_assistant.cli import app
from job_assistant.config import load_preferences
from job_assistant.deduplicate import deduplicate_vacancies
from job_assistant.filters import apply_filters
from job_assistant.normalize import normalize_records
from job_assistant.scoring import score_vacancies
from job_assistant.sources import SourceStatus, already_succeeded_today


runner = CliRunner()


def test_default_sources_include_headhunter_and_manual_linkedin_only_active_sources():
    preferences = load_preferences()
    assert preferences.sources.headhunter.mode == "api_search"
    assert preferences.sources.jooble.requires_env == ["JOOBLE_API_KEY"]
    assert preferences.sources.greenhouse.enabled is False
    assert preferences.sources.lever.enabled is False
    assert preferences.sources.email.enabled is True
    assert preferences.sources.email.mode == "imap_read_only"
    assert preferences.sources.linkedin.mode == "manual_current_page_only"
    assert preferences.sources.linkedin.enabled is False


def test_jooble_incomplete_description_normalizes_for_manual_review():
    preferences = load_preferences()
    vacancies = normalize_records([
        {"query": "Business Analyst", "record": {"__source": "jooble", "id": 1, "title": "Business Analyst", "company": "Acme", "location": "Remote", "snippet": "Requirements and BPMN", "link": "https://example.test/job"}}
    ], preferences)
    assert vacancies[0].source == "jooble"
    assert vacancies[0].description_completeness == "incomplete"
    assert any("manual review" in warning for warning in vacancies[0].warnings)


def test_feed_source_normalizers_cover_arbeitnow_greenhouse_and_lever():
    preferences = load_preferences()
    records = [
        {"query": "arbeitnow_feed", "record": {"__source": "arbeitnow", "slug": "ba", "title": "Requirements Engineer", "company_name": "Werk", "location": "Berlin", "remote": True, "visa_sponsorship": False, "description": "<p>Requirements analysis and BPMN for APIs.</p>", "url": "https://arbeitnow.test/ba"}},
        {"query": "greenhouse_feed", "record": {"__source": "greenhouse", "__company": "Example", "__board_token": "example", "id": 2, "title": "System Analyst", "content": "<p>Requirements and UML documentation.</p>", "absolute_url": "https://boards.test/job", "offices": [{"name": "Tbilisi"}], "departments": [{"name": "Product"}]}},
        {"query": "lever_feed", "record": {"__source": "lever", "__company": "Example", "__site": "example", "__region": "eu", "id": "p1", "text": "Integration Analyst", "descriptionPlain": "API integrations and requirements analysis.", "hostedUrl": "https://jobs.lever.co/example/p1", "categories": {"location": "Remote", "commitment": "Full-time", "team": "Product"}}},
    ]
    vacancies = normalize_records(records, preferences)
    assert {vacancy.source for vacancy in vacancies} == {"arbeitnow", "greenhouse", "lever"}
    assert vacancies[0].source_metadata["remote"] is True
    assert vacancies[1].source_company_identifier == "example"
    assert vacancies[2].apply_url == "https://jobs.lever.co/example/p1"


def test_role_relevance_uses_two_distinct_description_signal_groups():
    preferences = load_preferences()
    vacancies = normalize_records([
        {"query": "feed", "record": {"__source": "jooble", "id": "x", "title": "Operations Specialist", "company": "Acme", "snippet": "Requirements analysis, BPMN, UML and API integration", "link": "https://example.test/x"}}
    ], preferences)
    filtered = apply_filters(vacancies, preferences)
    assert filtered[0].blocker is False
    assert "unambiguous" in filtered[0].role_relevance_breakdown[0]


def test_manual_import_command_does_not_make_http_requests(tmp_path, monkeypatch):
    def fail_request(*args, **kwargs):
        raise AssertionError("manual import must not make HTTP requests")

    monkeypatch.setattr(httpx.Client, "get", fail_request)
    monkeypatch.setattr(httpx.Client, "post", fail_request)
    preferences = load_preferences()
    preferences = preferences.model_copy(update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")})})
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    fixture = tmp_path / "vacancy.txt"
    fixture.write_text("Business Systems Analyst\nExample Company\nRequirements analysis, BPMN, API integrations and Jira.", encoding="utf-8")
    result = runner.invoke(app, ["import-manual", "--source", "linkedin", "--url", "https://linkedin.test/jobs/1", "--file", str(fixture), "--title", "Business Systems Analyst", "--company", "Example Company"])
    assert result.exit_code == 0, result.output


def test_daily_guard_and_force_warning(monkeypatch, tmp_path):
    preferences = load_preferences()
    status = SourceStatus(source="headhunter", enabled=True, mode="api_search", last_successful_run="2099-01-01T00:00:00+00:00")
    assert already_succeeded_today(status) is False
    today = __import__("datetime").date.today().isoformat()
    status.last_successful_run = f"{today}T00:00:00+00:00"
    assert already_succeeded_today(status) is True

    preferences = preferences.model_copy(update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")})})
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setenv("JOOBLE_API_KEY", "")
    result = runner.invoke(app, ["fetch", "--source", "jooble", "--force"])
    assert "disabled" in result.output


def test_cross_source_duplicate_merges_sources_but_distinct_locations_remain():
    preferences = load_preferences()
    raw = [
        {"query": "Business Analyst", "record": {"__source": "jooble", "id": "1", "title": "Business Analyst", "company": "Acme", "location": "Remote", "snippet": "Requirements BPMN", "link": "https://example.test/acme-ba"}},
        {"query": "greenhouse_feed", "record": {"__source": "greenhouse", "__company": "Acme", "__board_token": "acme", "id": "2", "title": "Business Analyst", "content": "<p>Requirements BPMN and API integrations with richer text.</p>", "absolute_url": "https://example.test/acme-ba", "offices": [{"name": "Remote"}]}},
        {"query": "lever_feed", "record": {"__source": "lever", "__company": "Acme", "__site": "acme", "id": "3", "text": "Business Analyst", "descriptionPlain": "Requirements BPMN", "hostedUrl": "https://example.test/acme-ba-tbilisi", "categories": {"location": "Tbilisi"}}},
    ]
    vacancies = score_vacancies(apply_filters(normalize_records(raw, preferences), preferences), preferences)
    result, duplicates = deduplicate_vacancies(vacancies)
    assert duplicates == 1
    assert len(result) == 2
    assert {"greenhouse", "jooble"}.issubset(set(result[0].sources))
