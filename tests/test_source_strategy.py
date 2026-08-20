from __future__ import annotations

import asyncio
import imaplib
import socket
import ssl
from pathlib import Path

import fastapi.routing
import httpx
import pytest
from typer.testing import CliRunner

from job_assistant.ats import discover_greenhouse_board_token, discover_lever_site
from job_assistant.capture import create_app
from job_assistant.cli import _fetch_all_sources, _fetch_source, app
from job_assistant.config import load_preferences
from job_assistant.linkedin_queue import open_next_pending
from job_assistant.models import BatchStats
from job_assistant.normalize import normalize_records
from job_assistant.persistence import rebuild_from_authoritative_sources
from job_assistant.sources import load_statuses
from job_assistant.utils import read_json, write_json
from tests.fixtures.headhunter_records import HEADHUNTER_RESPONSE

runner = CliRunner()


@pytest.fixture(autouse=True)
def run_fastapi_sync_endpoints_inline(monkeypatch):
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "run_in_threadpool", run_inline)


def post_to_app(app, url: str, base_url: str = "http://localhost", **kwargs) -> httpx.Response:
    async def post() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            return await asyncio.wait_for(client.post(url, **kwargs), timeout=5)

    return asyncio.run(post())


def temp_preferences(tmp_path: Path):
    preferences = load_preferences()
    return preferences.model_copy(
        update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")})}
    )


def test_fetch_all_runs_exactly_headhunter_even_if_optional_sources_enabled(tmp_path):
    preferences = temp_preferences(tmp_path)
    assert preferences.sources.linkedin.enabled is True
    sources_config = preferences.sources.model_copy(
        update={
            "jobicy": preferences.sources.jobicy.model_copy(update={"enabled": True}),
            "greenhouse": preferences.sources.greenhouse.model_copy(update={"enabled": True}),
        }
    )
    assert _fetch_all_sources(preferences.model_copy(update={"sources": sources_config})) == ["headhunter"]


def test_linkedin_fetch_is_actionable_without_starting_playwright(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "job_assistant.cli.fetch_pending_linkedin",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Playwright fetch must not start")),
    )
    monkeypatch.setattr(
        "job_assistant.cli.run_login",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Playwright login must not start")),
    )
    raw, stats, skipped = _fetch_source(temp_preferences(tmp_path), "linkedin", force=False)
    assert raw == []
    assert skipped == "manual_or_explicit_playwright"
    assert "capture-server" in stats.errors[0]
    assert "Chromium extension" in stats.errors[0]
    assert "linkedin-fetch" in stats.errors[0]
    assert "Playwright never starts through `fetch` or `fetch-all`" in stats.errors[0]


def test_fetch_all_cli_never_starts_linkedin_playwright(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    automatic_sources: list[str] = []

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr(
        "job_assistant.cli.fetch_pending_linkedin",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Playwright fetch must not start")),
    )
    monkeypatch.setattr(
        "job_assistant.cli.run_login",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Playwright login must not start")),
    )

    def fake_fetch_source(preferences, source, force):
        automatic_sources.append(source)
        return [], __import__("job_assistant.models", fromlist=["BatchStats"]).BatchStats(), None

    class EmailResult:
        candidates = []
        headhunter_raw = []
        stats = __import__("job_assistant.models", fromlist=["BatchStats"]).BatchStats()

    monkeypatch.setattr("job_assistant.cli._fetch_source", fake_fetch_source)
    monkeypatch.setattr("job_assistant.cli.sync_email_alerts", lambda *args, **kwargs: EmailResult())
    monkeypatch.setattr("job_assistant.cli.mark_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("job_assistant.cli.rebuild_from_authoritative_sources", lambda *args, **kwargs: {})

    result = runner.invoke(app, ["fetch-all", "--force"])

    assert result.exit_code == 0, result.output
    assert automatic_sources == ["headhunter"]


def test_fetch_all_skips_email_sync_when_email_is_disabled(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    disabled_sources = preferences.sources.model_copy(
        update={"email": preferences.sources.email.model_copy(update={"enabled": False})}
    )
    preferences = preferences.model_copy(update={"sources": disabled_sources})
    sync_calls: list[object] = []

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr(
        "job_assistant.connectors.headhunter.HeadHunterConnector.fetch",
        lambda self: ([{"query": "email-disabled", "record": HEADHUNTER_RESPONSE["items"][0]}], BatchStats()),
    )
    monkeypatch.setattr("job_assistant.cli.sync_email_alerts", lambda *args, **kwargs: sync_calls.append(args))

    result = runner.invoke(app, ["fetch-all", "--force"])

    assert result.exit_code == 0, result.output
    assert sync_calls == []
    combined = read_json(preferences.outputs.output_dir() / preferences.outputs.combined_json_file)
    assert [(item["source"], item["source_id"]) for item in combined] == [
        ("headhunter", str(HEADHUNTER_RESPONSE["items"][0]["id"]))
    ]


def test_linkedin_source_status_is_explicit_queue_processing(tmp_path):
    status = load_statuses(temp_preferences(tmp_path))["linkedin"]
    assert status.enabled is True
    assert status.mode == "manual_or_explicit_playwright"
    assert status.priority == "explicit_queue_processing"


def test_capture_endpoint_authentication_payload_and_explicit_title_company(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    capture_app = create_app()
    payload = {
        "page_url": "https://linkedin.test/jobs/1",
        "document_title": "Fallback Browser Title",
        "vacancy_title": "LinkedIn System Analyst",
        "company": "LinkedIn Acme",
        "visible_text": "Requirements analysis, BPMN and API integrations.",
        "selected_text": "",
        "hostname": "linkedin.test",
        "captured_at": "2026-01-01T00:00:00Z",
        "source_label": "linkedin",
    }
    assert post_to_app(capture_app, "/api/v1/manual-capture", json=payload).status_code == 401
    bad_payload = {**payload, "page_url": "file:///tmp/x"}
    assert (
        post_to_app(
            capture_app, "/api/v1/manual-capture", json=bad_payload, headers={"x-job-assistant-token": "secret"}
        ).status_code
        == 422
    )
    assert (
        post_to_app(
            capture_app, "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
        ).status_code
        == 200
    )

    combined = read_json(preferences.outputs.output_dir() / preferences.outputs.combined_json_file)
    assert combined[0]["source"] == "linkedin"
    assert combined[0]["title"] == "LinkedIn System Analyst"
    assert combined[0]["company"] == "LinkedIn Acme"


def test_linkedin_queue_capture_matches_by_job_id_and_updates_status(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(
        candidates_path,
        [
            {
                "source": "linkedin",
                "external_id": "111",
                "title": "LinkedIn Analyst",
                "company": "Queue Co",
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/111",
                "received_at": "2026-01-01T00:00:00+00:00",
                "message_id": "m1",
                "status": "pending",
            }
        ],
    )
    monkeypatch.setattr(
        "job_assistant.capture.open_next_pending",
        lambda prefs: {"opened": False, "queue_id": None, "url": None, "error": None},
    )

    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/111/?trk=email",
        "document_title": "LinkedIn Analyst",
        "vacancy_title": "LinkedIn Analyst",
        "company": "Queue Co",
        "visible_text": "Business Analyst requirements analysis BPMN API integrations.",
        "selected_text": "",
        "hostname": "www.linkedin.com",
        "captured_at": "2026-01-02T00:00:00Z",
        "source_label": "linkedin",
    }
    result = post_to_app(
        create_app(), "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )

    assert result.status_code == 200
    assert result.json()["queue"]["matched"] is True
    candidates = read_json(candidates_path)
    assert candidates[0]["status"] == "processed"
    assert candidates[0]["captured_at"] == "2026-01-02T00:00:00Z"
    assert candidates[0]["pipeline_outcome"] in {"shortlisted", "blocked"}
    manual = read_json(preferences.outputs.output_dir() / preferences.outputs.manual_imports_file)
    assert len(manual) == 1


def test_linkedin_queue_duplicate_capture_does_not_append_again(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    write_json(
        preferences.outputs.output_dir() / preferences.outputs.email_candidates_file,
        [
            {
                "source": "linkedin",
                "external_id": "222",
                "title": "LinkedIn Analyst",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/222",
                "received_at": None,
                "message_id": "m2",
                "status": "pending",
            }
        ],
    )
    monkeypatch.setattr(
        "job_assistant.capture.open_next_pending",
        lambda prefs: {"opened": False, "queue_id": None, "url": None, "error": None},
    )
    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/222",
        "document_title": "LinkedIn Analyst",
        "vacancy_title": "LinkedIn Analyst",
        "company": "Acme",
        "visible_text": "Business Analyst requirements analysis BPMN API integrations.",
        "selected_text": "",
        "hostname": "www.linkedin.com",
        "captured_at": "2026-01-02T00:00:00Z",
        "source_label": "linkedin",
    }
    capture_app = create_app()

    first = post_to_app(
        capture_app, "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )
    second = post_to_app(
        capture_app, "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )

    assert first.json()["status"] == "saved"
    assert second.json()["status"] == "duplicate"
    manual = read_json(preferences.outputs.output_dir() / preferences.outputs.manual_imports_file)
    assert len(manual) == 1


def test_linkedin_queue_unmatched_capture_preserves_queue_and_imports(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    candidates = [
        {
            "source": "linkedin",
            "external_id": "333",
            "title": "Queued",
            "company": None,
            "location": None,
            "canonical_url": "https://www.linkedin.com/jobs/view/333",
            "received_at": None,
            "message_id": "m3",
            "status": "pending",
        }
    ]
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(candidates_path, candidates)
    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/999",
        "document_title": "Other Analyst",
        "vacancy_title": "Other Analyst",
        "company": "Acme",
        "visible_text": "Business Analyst requirements analysis BPMN API integrations.",
        "selected_text": "",
        "hostname": "www.linkedin.com",
        "captured_at": "2026-01-02T00:00:00Z",
        "source_label": "linkedin",
    }

    result = post_to_app(
        create_app(), "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )

    assert result.status_code == 200
    assert result.json()["queue"]["matched"] is False
    assert read_json(candidates_path) == candidates
    assert len(read_json(preferences.outputs.output_dir() / preferences.outputs.manual_imports_file)) == 1


def test_linkedin_queue_capture_invokes_pipeline(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    write_json(
        preferences.outputs.output_dir() / preferences.outputs.email_candidates_file,
        [
            {
                "source": "linkedin",
                "external_id": "444",
                "title": "Queued",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/444",
                "received_at": None,
                "message_id": "m4",
                "status": "pending",
            }
        ],
    )
    monkeypatch.setattr(
        "job_assistant.capture.open_next_pending",
        lambda prefs: {"opened": False, "queue_id": None, "url": None, "error": None},
    )
    calls = []
    monkeypatch.setattr(
        "job_assistant.capture.rebuild_from_authoritative_sources",
        lambda prefs, stats: calls.append((prefs, stats)) or {},
    )
    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/444",
        "document_title": "Queued",
        "vacancy_title": "Queued",
        "company": "Acme",
        "visible_text": "Business Analyst requirements analysis BPMN API integrations.",
        "selected_text": "",
        "hostname": "www.linkedin.com",
        "captured_at": "2026-01-02T00:00:00Z",
        "source_label": "linkedin",
    }

    result = post_to_app(
        create_app(), "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )

    assert result.status_code == 200
    assert len(calls) == 1
    assert calls[0][1].raw_records_received == 1


def test_linkedin_capture_processed_blocked_outcome_and_opens_next(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(
        candidates_path,
        [
            {
                "source": "linkedin",
                "external_id": "555",
                "title": "Software Developer",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/555",
                "received_at": None,
                "message_id": "m5",
                "status": "opened",
            },
            {
                "source": "linkedin",
                "external_id": "666",
                "title": "Next",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/666",
                "received_at": None,
                "message_id": "m6",
                "status": "pending",
            },
        ],
    )
    monkeypatch.setattr("job_assistant.linkedin_queue.open_url_default_browser", lambda url: (True, None))
    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/555",
        "document_title": "Software Developer",
        "vacancy_title": "Software Developer",
        "company": "Acme",
        "visible_text": "Python developer engineer coding implementation.",
        "selected_text": "",
        "hostname": "www.linkedin.com",
        "captured_at": "2026-01-02T00:00:00Z",
        "source_label": "linkedin",
    }

    result = post_to_app(
        create_app(), "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["next_opened"] is True
    assert body["next_queue_id"] == 2
    candidates = read_json(candidates_path)
    assert candidates[0]["status"] == "processed"
    assert candidates[0]["pipeline_outcome"] == "blocked"
    assert candidates[0]["blocker_reasons"]
    assert candidates[1]["status"] == "opened"


def test_linkedin_capture_preserves_processed_result_when_next_open_fails(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)
    token_path = tmp_path / "token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(
        candidates_path,
        [
            {
                "source": "linkedin",
                "external_id": "777",
                "title": "LinkedIn Analyst",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/777",
                "received_at": None,
                "message_id": "m7",
                "status": "pending",
            },
            {
                "source": "linkedin",
                "external_id": "888",
                "title": "Next",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/888",
                "received_at": None,
                "message_id": "m8",
                "status": "pending",
            },
        ],
    )
    monkeypatch.setattr("job_assistant.linkedin_queue.open_url_default_browser", lambda url: (False, "mock failure"))
    payload = {
        "page_url": "https://www.linkedin.com/jobs/view/777",
        "document_title": "LinkedIn Analyst",
        "vacancy_title": "LinkedIn Analyst",
        "company": "Acme",
        "visible_text": "Business Analyst requirements analysis BPMN API integrations.",
        "selected_text": "",
        "hostname": "www.linkedin.com",
        "captured_at": "2026-01-02T00:00:00Z",
        "source_label": "linkedin",
    }

    result = post_to_app(
        create_app(), "/api/v1/manual-capture", json=payload, headers={"x-job-assistant-token": "secret"}
    )

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["next_opened"] is False
    assert "next_open_failed" in body["warning"]
    candidates = read_json(candidates_path)
    assert candidates[0]["status"] == "processed"
    assert candidates[1]["status"] == "pending"
    assert candidates[1]["last_open_error"] == "mock failure"


def test_linkedin_queue_command_rejects_malformed_entries(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    original = [
        {
            "source": "linkedin",
            "external_id": "",
            "title": "Broken",
            "company": None,
            "location": None,
            "canonical_url": "https://www.linkedin.com/jobs/view/",
            "received_at": None,
            "message_id": "m5",
            "status": "pending",
        }
    ]
    write_json(candidates_path, original)

    result = runner.invoke(app, ["linkedin-queue", "--status"])

    assert result.exit_code == 1
    assert "Invalid LinkedIn queue" in result.output
    assert read_json(candidates_path) == original


def test_linkedin_queue_next_open_opens_first_pending_and_marks_opened(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    opened = []
    monkeypatch.setattr(
        "job_assistant.linkedin_queue.open_url_default_browser", lambda url: opened.append(url) or (True, None)
    )
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(
        candidates_path,
        [
            {
                "source": "linkedin",
                "external_id": "111",
                "title": "First",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/111",
                "received_at": None,
                "message_id": "m1",
                "status": "pending",
            },
            {
                "source": "linkedin",
                "external_id": "222",
                "title": "Second",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/222",
                "received_at": None,
                "message_id": "m2",
                "status": "pending",
            },
        ],
    )

    result = runner.invoke(app, ["linkedin-queue", "--next", "--open"])

    assert result.exit_code == 0, result.output
    assert opened == ["https://www.linkedin.com/jobs/view/111"]
    candidates = read_json(candidates_path)
    assert candidates[0]["status"] == "opened"
    assert candidates[0]["opened_at"]
    assert "Opened https://www.linkedin.com/jobs/view/111" in result.output


def test_linkedin_queue_open_requires_next(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    write_json(preferences.outputs.output_dir() / preferences.outputs.email_candidates_file, [])

    result = runner.invoke(app, ["linkedin-queue", "--open"])

    assert result.exit_code == 1
    assert "--open is only supported" in result.output


def test_linkedin_queue_open_failure_remains_pending_retryable(tmp_path):
    preferences = temp_preferences(tmp_path)
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(
        candidates_path,
        [
            {
                "source": "linkedin",
                "external_id": "111",
                "title": "First",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/111",
                "received_at": None,
                "message_id": "m1",
                "status": "pending",
            }
        ],
    )

    result = open_next_pending(preferences, opener=lambda url: (False, "mock open failed"))

    candidates = read_json(candidates_path)
    assert result["opened"] is False
    assert candidates[0]["status"] == "pending"
    assert candidates[0]["last_open_error"] == "mock open failed"


def test_linkedin_queue_open_no_pending(tmp_path):
    preferences = temp_preferences(tmp_path)
    write_json(
        preferences.outputs.output_dir() / preferences.outputs.email_candidates_file,
        [
            {
                "source": "linkedin",
                "external_id": "111",
                "title": "First",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/111",
                "received_at": None,
                "message_id": "m1",
                "status": "opened",
            }
        ],
    )

    result = open_next_pending(preferences, opener=lambda url: (_ for _ in ()).throw(AssertionError("should not open")))

    assert result == {"opened": False, "queue_id": None, "url": None, "error": None}


def test_legacy_manual_capture_required_is_pending_compatible(tmp_path):
    preferences = temp_preferences(tmp_path)
    candidates_path = preferences.outputs.output_dir() / preferences.outputs.email_candidates_file
    write_json(
        candidates_path,
        [
            {
                "source": "linkedin",
                "external_id": "111",
                "title": "Legacy",
                "company": None,
                "location": None,
                "canonical_url": "https://www.linkedin.com/jobs/view/111",
                "received_at": None,
                "message_id": "m1",
                "status": "manual_capture_required",
            }
        ],
    )

    result = open_next_pending(preferences, opener=lambda url: (True, None))

    assert result["opened"] is True
    assert read_json(candidates_path)[0]["status"] == "opened"


def test_manual_capture_normalization_uses_explicit_title_company(tmp_path):
    preferences = temp_preferences(tmp_path)
    vacancies = normalize_records(
        [
            {
                "query": "manual_capture",
                "record": {
                    "__source": "manual_capture",
                    "source_label": "linkedin",
                    "page_url": "https://linkedin.test/jobs/1",
                    "document_title": "Browser Title",
                    "vacancy_title": "System Analyst",
                    "company": "Acme",
                    "visible_text": "Requirements and UML documentation",
                    "hostname": "linkedin.test",
                    "captured_at": "now",
                },
            }
        ],
        preferences,
    )
    assert vacancies[0].source == "linkedin"
    assert vacancies[0].title == "System Analyst"
    assert vacancies[0].company == "Acme"
    assert vacancies[0].imported_manually is True


def test_greenhouse_and_lever_identifier_discovery():
    assert discover_greenhouse_board_token("https://boards.greenhouse.io/acme/jobs/123") == "acme"
    assert discover_lever_site("https://jobs.lever.co/acme/123") == ("acme", "global")
    assert discover_lever_site("https://jobs.lever.co/acme/eu/123") == ("acme", "eu")


def test_no_global_greenhouse_or_lever_searches(tmp_path):
    statuses = load_statuses(temp_preferences(tmp_path))
    assert statuses["greenhouse"].enabled is False
    assert statuses["lever"].enabled is False


def test_rebuild_uses_only_headhunter_raw_and_linkedin_captures(tmp_path):
    preferences = temp_preferences(tmp_path)
    paths = {
        "raw": preferences.outputs.output_dir() / preferences.outputs.raw_file,
        "manual": preferences.outputs.output_dir() / preferences.outputs.manual_imports_file,
        "combined": preferences.outputs.output_dir() / preferences.outputs.combined_json_file,
    }
    hh_raw = [{"query": "Business Analyst", "sample": "remote", "record": HEADHUNTER_RESPONSE["items"][0]}]
    write_json(paths["raw"], hh_raw)
    write_json(
        paths["manual"],
        [
            {
                "query": "manual_capture",
                "record": {
                    "__source": "manual_capture",
                    "source_label": "linkedin",
                    "page_url": "https://linkedin.test/jobs/2",
                    "document_title": "Old",
                    "vacancy_title": "LinkedIn Analyst",
                    "company": "LinkedIn Co",
                    "visible_text": "Requirements analysis, BPMN and API integrations.",
                    "hostname": "linkedin.test",
                    "captured_at": "now",
                },
            },
            {
                "query": "manual_capture",
                "record": {
                    "__source": "manual_capture",
                    "source_label": "other",
                    "page_url": "https://other.test/jobs/1",
                    "document_title": "Other Analyst",
                    "visible_text": "Requirements BPMN API",
                    "hostname": "other.test",
                    "captured_at": "now",
                },
            },
        ],
    )
    write_json(
        paths["combined"],
        [
            {"source": "wellfound_browser", "title": "Stale Fixture", "source_url": "https://wellfound.com/jobs/1"},
            {"source": "habr_browser", "title": "Stale Habr"},
        ],
    )

    rebuild_from_authoritative_sources(preferences)

    combined = read_json(paths["combined"])
    assert {item["source"] for item in combined} == {"headhunter", "linkedin"}
    assert all("wellfound" not in str(item).lower() and "habr" not in str(item).lower() for item in combined)


def test_headhunter_refresh_preserves_linkedin_capture(tmp_path):
    preferences = temp_preferences(tmp_path)
    manual_path = preferences.outputs.output_dir() / preferences.outputs.manual_imports_file
    combined_path = preferences.outputs.output_dir() / preferences.outputs.combined_json_file
    write_json(
        manual_path,
        [
            {
                "query": "manual_capture",
                "record": {
                    "__source": "manual_capture",
                    "source_label": "linkedin",
                    "page_url": "https://linkedin.test/jobs/2",
                    "document_title": "Old",
                    "vacancy_title": "LinkedIn Analyst",
                    "company": "LinkedIn Co",
                    "visible_text": "Requirements analysis, BPMN and API integrations.",
                    "hostname": "linkedin.test",
                    "captured_at": "now",
                },
            }
        ],
    )

    rebuild_from_authoritative_sources(
        preferences,
        headhunter_raw=[{"query": "Business Analyst", "sample": "remote", "record": HEADHUNTER_RESPONSE["items"][0]}],
    )

    sources = {item["source"] for item in read_json(combined_path)}
    assert sources == {"headhunter", "linkedin"}


def test_failed_headhunter_run_does_not_replace_valid_linkedin_data(tmp_path):
    preferences = temp_preferences(tmp_path)
    manual_path = preferences.outputs.output_dir() / preferences.outputs.manual_imports_file
    combined_path = preferences.outputs.output_dir() / preferences.outputs.combined_json_file
    write_json(
        manual_path,
        [
            {
                "query": "manual_capture",
                "record": {
                    "__source": "manual_capture",
                    "source_label": "linkedin",
                    "page_url": "https://linkedin.test/jobs/2",
                    "document_title": "Old",
                    "vacancy_title": "LinkedIn Analyst",
                    "company": "LinkedIn Co",
                    "visible_text": "Requirements analysis, BPMN and API integrations.",
                    "hostname": "linkedin.test",
                    "captured_at": "now",
                },
            }
        ],
    )
    rebuild_from_authoritative_sources(preferences)

    before = read_json(combined_path)
    assert {item["source"] for item in before} == {"linkedin"}
    raw, stats, skipped = _fetch_source(preferences, "linkedin", force=False)
    assert raw == []
    assert skipped == "manual_or_explicit_playwright"
    assert read_json(combined_path) == before


def test_failed_headhunter_refresh_preserves_authoritative_stores(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    raw_path = preferences.outputs.output_dir() / preferences.outputs.raw_file
    manual_path = preferences.outputs.output_dir() / preferences.outputs.manual_imports_file
    combined_path = preferences.outputs.output_dir() / preferences.outputs.combined_json_file
    hh_raw = [{"query": "Business Analyst", "sample": "remote", "record": HEADHUNTER_RESPONSE["items"][0]}]
    write_json(raw_path, hh_raw)
    write_json(
        manual_path,
        [
            {
                "query": "manual_capture",
                "record": {
                    "__source": "manual_capture",
                    "source_label": "linkedin",
                    "page_url": "https://linkedin.test/jobs/2",
                    "document_title": "Old",
                    "vacancy_title": "LinkedIn Analyst",
                    "company": "LinkedIn Co",
                    "visible_text": "Requirements analysis, BPMN and API integrations.",
                    "hostname": "linkedin.test",
                    "captured_at": "now",
                },
            }
        ],
    )
    rebuild_from_authoritative_sources(preferences)
    before = read_json(combined_path)

    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)

    def failed_fetch(self):
        from job_assistant.models import BatchStats

        return [], BatchStats(errors=["mock_headhunter_failure"])

    monkeypatch.setattr("job_assistant.connectors.headhunter.HeadHunterConnector.fetch", failed_fetch)
    result = runner.invoke(
        __import__("job_assistant.cli", fromlist=["app"]).app, ["fetch", "--source", "headhunter", "--force"]
    )

    assert result.exit_code == 0, result.output
    after = read_json(combined_path)
    assert [(item["source"], item["title"], item.get("company")) for item in after] == [
        (item["source"], item["title"], item.get("company")) for item in before
    ]
    assert read_json(raw_path) == hh_raw


@pytest.mark.parametrize(
    "email_error",
    [
        imaplib.IMAP4.error("imap failure password=SYNTHETIC"),
        ssl.SSLError("ssl failure secret=SYNTHETIC"),
        socket.gaierror(-2, "network failure token=SYNTHETIC"),
    ],
)
def test_fetch_all_isolates_email_failures_and_finishes_pipeline(monkeypatch, tmp_path, email_error):
    preferences = temp_preferences(tmp_path)
    combined_path = preferences.outputs.output_dir() / preferences.outputs.combined_json_file
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr(
        "job_assistant.connectors.headhunter.HeadHunterConnector.fetch",
        lambda self: (
            [{"query": "Business Analyst", "sample": "remote", "record": HEADHUNTER_RESPONSE["items"][0]}],
            BatchStats(requests_made=1, raw_records_received=1),
        ),
    )
    monkeypatch.setattr(
        "job_assistant.cli.sync_email_alerts",
        lambda *args, **kwargs: (_ for _ in ()).throw(email_error),
    )

    result = runner.invoke(app, ["fetch-all", "--force"])

    assert result.exit_code == 0, result.output
    after = read_json(combined_path)
    assert [(item["source"], item["source_id"]) for item in after] == [
        ("headhunter", str(HEADHUNTER_RESPONSE["items"][0]["id"]))
    ]
    assert "errors" in result.output
    assert "email request=imap_sync" in result.output
    assert "SYNTHETIC" not in result.output
    assert "=REDACTED" in result.output
    email_status = load_statuses(preferences)["email"]
    assert email_status.status == "error"
    assert email_status.last_error is not None
    assert "SYNTHETIC" not in email_status.last_error
    assert "=REDACTED" in email_status.last_error


@pytest.mark.parametrize(
    "email_error",
    [
        imaplib.IMAP4.error("imap failure password=SYNTHETIC"),
        ssl.SSLError("ssl failure secret=SYNTHETIC"),
        socket.gaierror(-2, "network failure token=SYNTHETIC"),
    ],
)
def test_email_sync_cli_sanitizes_ordinary_email_failures(monkeypatch, tmp_path, email_error):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr(
        "job_assistant.cli.sync_email_alerts",
        lambda *args, **kwargs: (_ for _ in ()).throw(email_error),
    )

    result = runner.invoke(app, ["email-sync", "--since-days", "30"])

    assert result.exit_code == 1
    assert "Email sync failed:" in result.output
    assert "SYNTHETIC" not in result.output
    assert "=REDACTED" in result.output
    assert "Traceback" not in result.output


def test_email_sync_cli_dry_run_does_not_require_live_services(monkeypatch, tmp_path):
    preferences = temp_preferences(tmp_path)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)

    class Result:
        candidates = []
        headhunter_raw = []
        linkedin_queue_count = 0
        processed_message_count = 0
        stats = __import__("job_assistant.models", fromlist=["BatchStats"]).BatchStats()
        dry_run = True

    monkeypatch.setattr("job_assistant.cli.sync_email_alerts", lambda *args, **kwargs: Result())
    result = runner.invoke(app, ["email-sync", "--since-days", "30", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "dry-run:" in result.output
    assert "True" in result.output
