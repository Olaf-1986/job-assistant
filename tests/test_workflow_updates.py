from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from job_assistant.cli import app, qf_app
from job_assistant.config import load_preferences
from job_assistant.email_ingestion import sync_email_alerts
from job_assistant.headhunter_state import RESPONSE_METADATA, observed_hh_detail
from job_assistant.linkedin_types import LinkedInFetchError, LinkedInStopRun
from job_assistant.models import BatchStats
from job_assistant.paths import output_paths
from job_assistant.persistence import merge_headhunter_raw, rebuild_from_authoritative_sources
from job_assistant.publication import JOURNAL, PublicationError, recover_publication
from job_assistant.raw_store import StoreError
from job_assistant.telegram_ingestion import TelegramFetchResult, TelegramSourceReport
from job_assistant.telegram_parser import parse_telegram_message
from job_assistant.utils import read_json_strict, write_json
from tests.fixtures.headhunter_records import REMOTE_BA
from tests.fixtures.telegram_messages import ORDINARY_VACANCY
from tests.test_email_ingestion import FakeIMAP, fixture

runner = CliRunner()


@pytest.fixture
def preferences(tmp_path, monkeypatch):
    preferences = load_preferences()
    preferences.outputs.directory = str(tmp_path)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    return preferences


@pytest.mark.parametrize("command", ["email-sync", "fetch-all"])
@pytest.mark.parametrize("outcome", ["complete", "error", "incomplete", "missing_description"])
def test_email_hh_updates_same_id_only_after_complete_success(monkeypatch, preferences, command, outcome):
    paths = output_paths(preferences)
    old = {**REMOTE_BA, "description": "Previously successful full description."}
    fresh = {**old, "description": "New complete requirements and business analysis description."}
    if outcome == "missing_description":
        fresh.pop("description")
    write_json(paths["raw"], [{"record": old}])
    monkeypatch.setattr(
        "job_assistant.email_ingestion.load_email_settings",
        lambda: {
            "host": "fixture.invalid",
            "port": 993,
            "mailbox": "fixture",
            "user": "fixture",
            "password": "fixture",
        },
    )
    stats = BatchStats(complete=outcome != "incomplete", errors=["fixture failure"] if outcome == "error" else [])

    def email_sync(*args, **kwargs):
        return sync_email_alerts(
            preferences,
            since_days=7,
            full_refresh=True,
            imap_factory=lambda *args: FakeIMAP({"1": fixture("gmail_job_alert_multipart.eml")}),
            hh_lookup=lambda job_id: (fresh, stats),
        )

    monkeypatch.setattr("job_assistant.cli.sync_email_alerts", email_sync)
    monkeypatch.setattr("job_assistant.cli._fetch_source", lambda *a, **kw: ([{"record": old}], BatchStats(), None))
    for _ in range(2):
        result = runner.invoke(app, [command])
        assert result.exit_code == 0, result.output
        raw = read_json_strict(paths["raw"])
        assert len(raw) == 1
        assert raw[0]["record"]["description"] == (
            fresh["description"] if outcome == "complete" else old["description"]
        )


def seed_sources(preferences):
    paths = output_paths(preferences)
    now = datetime.now(UTC).isoformat()
    write_json(paths["raw"], [{"record": {**REMOTE_BA, "published_at": now}}])
    write_json(
        paths["manual_imports"],
        [
            {
                "record": {
                    "__source": "linkedin",
                    "page_url": "https://www.linkedin.com/jobs/view/9001",
                    "vacancy_title": "Business Analyst",
                    "company": "LinkedIn Fixture",
                    "published_at": now,
                    "visible_text": (
                        "Business analyst working remotely worldwide on requirements analysis "
                        "and stakeholder interviews. "
                        "Document functional requirements and acceptance criteria for a new software product."
                    ),
                }
            }
        ],
    )
    telegram = parse_telegram_message(ORDINARY_VACANCY, preferences).target_records[0]
    telegram["published_at"] = now
    write_json(paths["telegram_raw"], [{"record": telegram}])
    rebuild_from_authoritative_sources(preferences)
    return paths


def mock_qf_sources(monkeypatch, preferences, linkedin_error):
    preferences.sources.email.enabled = False
    monkeypatch.setattr(
        "job_assistant.cli._fetch_source", lambda *a, **kw: ([], BatchStats(), "already_successful_today")
    )
    calls = []

    def linkedin(*args, **kwargs):
        calls.append("linkedin")
        if isinstance(linkedin_error, BaseException):
            raise linkedin_error
        return linkedin_error or {
            key: 0
            for key in (
                "opened",
                "imported",
                "title_prefilter_rejected",
                "location_prefilter_rejected",
                "expired",
                "extraction_failed",
                "rechecked",
                "recheck_expired",
                "recheck_failed",
                "pending_outside_window",
            )
        }

    async def telegram(*args, **kwargs):
        calls.append("telegram")
        return TelegramFetchResult(dry_run=False, source_reports={"fixture": TelegramSourceReport(source="fixture")})

    monkeypatch.setattr("job_assistant.cli.fetch_pending_linkedin", linkedin)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", telegram)
    return calls


@pytest.mark.parametrize(
    "error",
    [
        LinkedInFetchError("fixture failure"),
        LinkedInStopRun("captcha_required"),
        RuntimeError("unexpected source failure"),
    ],
)
def test_qf_partial_linkedin_failure_continues_to_telegram_and_final_shortlist(monkeypatch, preferences, error):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, error)
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1
    assert calls == ["linkedin", "telegram"]
    summary = read_json_strict(paths["summary"])
    assert summary["status"] == "partial"
    assert {stage["stage"]: stage["status"] for stage in summary["stages"]} == {
        "headhunter": "skipped",
        "email": "skipped",
        "linkedin": "error",
        "telegram": "success",
    }
    assert str(error) in result.output
    assert summary["shortlist_source"] == "all"
    assert "hh.ru/vacancy" in paths["combined_shortlist"].read_text()
    assert "linkedin.com/jobs/view/9001" in paths["combined_shortlist"].read_text()
    assert "t.me/" in paths["combined_shortlist"].read_text()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), typer.Abort(), SystemExit(130)])
def test_qf_ctrl_c_stops_telegram_and_final_publication(monkeypatch, preferences, interruption):
    paths = seed_sources(preferences)
    mock_qf_sources(monkeypatch, preferences, interruption)
    original = paths["combined_shortlist"].read_bytes()
    calls = []
    monkeypatch.setattr("job_assistant.cli.shortlist", lambda *a, **kw: calls.append("shortlist"))

    async def telegram(*args, **kwargs):
        calls.append("telegram")
        raise AssertionError("Must stop before Telegram")

    monkeypatch.setattr("job_assistant.cli.fetch_telegram", telegram)
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code != 0
    assert calls == []
    assert paths["combined_shortlist"].read_bytes() == original


def hh_observation(description, received_at, origin="email"):
    record = observed_hh_detail({**REMOTE_BA, "description": description}, origin)
    record[RESPONSE_METADATA]["received_at"] = received_at
    return {"query": origin, "record": record}


@pytest.mark.parametrize("reverse", [False, True])
def test_hh_merge_uses_successful_receipt_time_not_list_order_or_publication_date(reverse):
    old = hh_observation("Old complete description", "2026-01-01T00:00:00+00:00", "search")
    fresh = hh_observation("Fresh complete description", "2026-01-02T00:00:00+00:00")
    fresh["record"]["published_at"] = "2000-01-01T00:00:00+00:00"
    stored, incoming = (fresh, old) if reverse else (old, fresh)
    assert merge_headhunter_raw([stored], [incoming, incoming]) == [fresh]
    assert merge_headhunter_raw([fresh], [fresh]) == [fresh]


@pytest.mark.parametrize(
    "invalid", ["no_metadata", "incomplete", "no_description", "wrong_origin", "invalid_time", "naive_time"]
)
def test_unproven_or_incomplete_hh_observation_cannot_replace_successful_record(invalid):
    old = hh_observation("Old complete description", "2026-01-01T00:00:00+00:00")
    fresh = hh_observation("Fresh complete description", "2026-01-02T00:00:00+00:00")
    metadata = fresh["record"][RESPONSE_METADATA]
    if invalid == "no_metadata":
        fresh["record"].pop(RESPONSE_METADATA)
    elif invalid == "incomplete":
        metadata["complete"] = False
    elif invalid == "no_description":
        fresh["record"].pop("description")
    elif invalid == "wrong_origin":
        metadata["origin"] = "email_snippet"
    else:
        metadata["received_at"] = "invalid" if invalid == "invalid_time" else "2026-01-02T00:00:00"
    assert merge_headhunter_raw([old], [fresh]) == [old]
    assert merge_headhunter_raw([fresh], [old]) == [old]


def test_hh_legacy_and_wrapped_records_remain_compatible():
    old = {**REMOTE_BA, "description": "Old full description"}
    unproven = {**old, "description": "Unverified new description"}
    assert merge_headhunter_raw([old], [unproven]) == [old]
    fresh = hh_observation("Verified new description", "2026-01-02T00:00:00+00:00")
    assert merge_headhunter_raw([old], [fresh]) == [fresh]


def test_fetch_all_prefers_newer_search_detail_over_replayed_email(monkeypatch, preferences):
    paths = output_paths(preferences)
    search = hh_observation("New search description", "2026-01-02T00:00:00+00:00", "search")
    email = hh_observation("Replayed old email description", "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr("job_assistant.cli._fetch_source", lambda *a, **kw: ([search], BatchStats(), None))
    monkeypatch.setattr(
        "job_assistant.cli.sync_email_alerts",
        lambda *a, **kw: SimpleNamespace(headhunter_raw=[email], candidates=[], stats=BatchStats()),
    )
    for _ in range(2):
        result = runner.invoke(app, ["fetch-all"])
        assert result.exit_code == 0, result.output
        assert read_json_strict(paths["raw"])[0]["record"]["description"] == "New search description"
        assert len(read_json_strict(paths["raw"])) == 1


@pytest.mark.parametrize("source", ["all", "hh", "li", "tg"])
def test_qf_canonical_scope_and_separate_source_views(monkeypatch, preferences, source):
    paths = seed_sources(preferences)
    mock_qf_sources(monkeypatch, preferences, None)
    result = runner.invoke(app, ["qf", "1w", source, "25"])
    assert result.exit_code == 0, result.output
    summary = read_json_strict(paths["summary"])
    assert summary["status"] == "success"
    expected = {"hh": "headhunter", "li": "linkedin", "tg": "telegram"}
    assert summary["shortlist_source"] == expected.get(source, "all")
    canonical = paths["combined_shortlist"].read_text()
    for key, url in {"hh": "hh.ru/vacancy/", "li": "linkedin.com/jobs/view/9001", "tg": "t.me/"}.items():
        assert (url in canonical) == (source in ("all", key))
        assert url in paths[f"{expected[key]}_shortlist"].read_text()


def test_source_specific_qf_failure_publishes_only_selected_saved_fallback(monkeypatch, preferences):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, LinkedInStopRun("login_required"))
    result = runner.invoke(app, ["qf", "1w", "li", "25"])
    assert result.exit_code == 1
    assert calls == ["linkedin"]
    canonical = paths["combined_shortlist"].read_text()
    assert "linkedin.com/jobs/view/9001" in canonical
    assert "hh.ru/vacancy/" not in canonical and "t.me/" not in canonical
    summary = read_json_strict(paths["summary"])
    assert summary["status"] == "partial"
    assert summary["stages"] == [{"stage": "linkedin", "status": "error", "reason": "login_required"}]
    assert "partial failure (exit 1)" in result.output


def test_qf_disabled_sources_are_skipped_but_saved_data_is_available(monkeypatch, preferences):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, None)
    preferences.sources.linkedin.enabled = False
    preferences.sources.telegram.enabled = False
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 0, result.output
    assert calls == []
    summary = read_json_strict(paths["summary"])
    assert all(stage["status"] == "skipped" for stage in summary["stages"])
    assert "linkedin.com/jobs/view/9001" in paths["combined_shortlist"].read_text()
    assert "t.me/" in paths["combined_shortlist"].read_text()


@pytest.mark.parametrize("failed", ["headhunter", "email", "linkedin", "telegram"])
def test_qf_reports_non_raising_partial_source_results(monkeypatch, preferences, failed):
    paths = seed_sources(preferences)
    stats = {
        key: 0
        for key in (
            "opened",
            "imported",
            "title_prefilter_rejected",
            "location_prefilter_rejected",
            "expired",
            "extraction_failed",
            "rechecked",
            "recheck_expired",
            "recheck_failed",
            "pending_outside_window",
        )
    }
    if failed == "linkedin":
        stats["extraction_failed"] = 1
        stats["errors"] = ["fixture extraction failure"]
    calls = mock_qf_sources(monkeypatch, preferences, stats)
    if failed == "headhunter":
        monkeypatch.setattr("job_assistant.cli._fetch_source", lambda *a, **kw: ([], BatchStats(complete=False), None))
    elif failed == "email":
        preferences.sources.email.enabled = True
        monkeypatch.setattr(
            "job_assistant.cli.sync_email_alerts",
            lambda *a, **kw: SimpleNamespace(
                candidates=[], headhunter_raw=[], stats=BatchStats(errors=["fixture email error"])
            ),
        )
    elif failed == "telegram":

        async def telegram(*args, **kwargs):
            calls.append("telegram")
            return TelegramFetchResult(
                dry_run=False,
                source_reports={"fixture": TelegramSourceReport(source="fixture", error="fixture channel failure")},
            )

        monkeypatch.setattr("job_assistant.cli.fetch_telegram", telegram)
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1, result.output
    assert calls == ["linkedin", "telegram"]
    summary = read_json_strict(paths["summary"])
    assert summary["status"] == "partial"
    assert [stage["stage"] for stage in summary["stages"] if stage["status"] == "error"] == [failed]
    assert summary["errors"]
    assert "partial failure (exit 1)" in result.output


@pytest.mark.parametrize(
    "error", [StoreError("invalid store"), PublicationError("incomplete publication"), OSError("storage unavailable")]
)
def test_qf_storage_failure_stops_other_stages_and_final_build(monkeypatch, preferences, error):
    seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, error)
    monkeypatch.setattr("job_assistant.cli.shortlist", lambda *a, **kw: calls.append("shortlist"))
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code != 0
    assert calls == ["linkedin"]
    assert "qf stopped before final shortlist" in result.output
    assert "qf: partial" not in result.output


@pytest.mark.parametrize("store", ["raw", "manual_imports", "telegram_raw", "rvc_raw"])
def test_qf_damaged_store_is_fatal_and_does_not_overwrite_reports(monkeypatch, preferences, store):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, LinkedInFetchError("source unavailable"))
    paths[store].write_text("{broken fixture")
    before = {paths[key]: paths[key].read_bytes() for key in (store, "combined_shortlist", "summary")}
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1
    assert "Storage error" in result.output
    assert calls == []
    assert {path: path.read_bytes() for path in before} == before


def test_qf_final_publication_failure_retains_journal_and_never_reports_partial_success(monkeypatch, preferences):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, LinkedInStopRun("captcha_required"))
    original_replace = os.replace
    actual_rebuild = rebuild_from_authoritative_sources

    def fail_replace(src, dst):
        if Path(dst) == paths["combined_shortlist"]:
            raise PermissionError("private filesystem detail")
        return original_replace(src, dst)

    def rebuild(preferences, stats, **kwargs):
        # Let earlier source publications finish; fail halfway through the final set.
        if stats.stages:
            with monkeypatch.context() as patch:
                patch.setattr("job_assistant.utils.os.replace", fail_replace)
                return actual_rebuild(preferences, stats, **kwargs)
        return actual_rebuild(preferences, stats, **kwargs)

    monkeypatch.setattr("job_assistant.cli.rebuild_from_authoritative_sources", rebuild)
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1
    assert calls == ["linkedin", "telegram"]
    assert "Incomplete output publication" in result.output
    assert "private filesystem detail" not in result.output
    assert "qf: partial failure" not in result.output
    assert (paths["dir"] / JOURNAL).exists()
    assert "stages" not in read_json_strict(paths["summary"])
    assert recover_publication(paths["dir"]) is True
    assert read_json_strict(paths["summary"])["status"] == "partial"
    assert "linkedin.com/jobs/view/9001" in paths["combined_shortlist"].read_text()


def test_qf_help_describes_scope_and_partial_exit():
    result = runner.invoke(app, ["qf", "--help"])
    assert result.exit_code == 0
    compact = " ".join(result.output.split())
    assert "valid saved records remain available" in compact
    assert "partial failure exits 1" in compact
    assert "combined_shortlist.md" in result.output


@pytest.mark.parametrize("failed", ["headhunter", "email"])
def test_qf_hh_email_exception_does_not_skip_independent_stages(monkeypatch, preferences, failed):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, None)
    preferences.sources.email.enabled = True

    def hh(*args, **kwargs):
        calls.append("headhunter")
        if failed == "headhunter":
            raise RuntimeError("fixture HH source failure")
        return [], BatchStats(), "already_successful_today"

    def email(*args, **kwargs):
        calls.append("email")
        if failed == "email":
            raise ConnectionError("fixture email connection failure")
        return SimpleNamespace(candidates=[], headhunter_raw=[], stats=BatchStats())

    monkeypatch.setattr("job_assistant.cli._fetch_source", hh)
    monkeypatch.setattr("job_assistant.cli.sync_email_alerts", email)
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1, result.output
    assert calls == ["headhunter", "email", "linkedin", "telegram"]
    summary = read_json_strict(paths["summary"])
    assert summary["status"] == "partial"
    assert [stage["stage"] for stage in summary["stages"] if stage["status"] == "error"] == [failed]


def test_qf_email_output_failure_is_fatal_not_a_source_error(monkeypatch, preferences):
    paths = seed_sources(preferences)
    calls = mock_qf_sources(monkeypatch, preferences, None)
    preferences.sources.email.enabled = True
    monkeypatch.setattr(
        "job_assistant.email_ingestion.load_email_settings",
        lambda: {
            "host": "fixture.invalid",
            "port": 993,
            "mailbox": "fixture",
            "user": "fixture",
            "password": "fixture",
        },
    )

    def fail_write(*args):
        raise PermissionError("private write details")

    monkeypatch.setattr("job_assistant.email_ingestion._write_email_outputs", fail_write)
    monkeypatch.setattr(
        "job_assistant.cli.sync_email_alerts",
        lambda *a, **kw: sync_email_alerts(preferences, since_days=7, imap_factory=lambda *args: FakeIMAP({})),
    )
    before = paths["summary"].read_bytes()
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1
    assert calls == []
    assert "Email output write failed" in result.output
    assert "private write details" not in result.output
    assert "partial failure" not in result.output
    assert paths["summary"].read_bytes() == before


def test_linkedin_stop_state_write_failure_is_fatal(monkeypatch, preferences):
    seed_sources(preferences)
    error = LinkedInStopRun("rate_limited")
    error.__cause__ = PermissionError("private state path")
    calls = mock_qf_sources(monkeypatch, preferences, error)
    result = runner.invoke(app, ["qf", "1w", "all", "25"])
    assert result.exit_code == 1
    assert calls == ["linkedin"]
    assert "protective stop state could not be saved" in result.output
    assert "private state path" not in result.output
    assert "partial failure" not in result.output


@pytest.mark.parametrize("failed", [False, True])
def test_standalone_qf_entrypoint_preserves_partial_exit_and_scope(monkeypatch, preferences, failed):
    paths = seed_sources(preferences)
    mock_qf_sources(monkeypatch, preferences, LinkedInStopRun("captcha_required") if failed else None)
    monkeypatch.setattr("sys.argv", ["qf", "1w", "li", "25"])
    with pytest.raises(SystemExit) as result:
        qf_app()
    assert result.value.code == int(failed)
    summary = read_json_strict(paths["summary"])
    assert summary["status"] == ("partial" if failed else "success")
    assert summary["shortlist_source"] == "linkedin"
