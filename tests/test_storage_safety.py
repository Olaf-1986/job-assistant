from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from job_assistant.capture import ManualCapturePayload, process_manual_capture
from job_assistant.cli import app
from job_assistant.config import load_preferences
from job_assistant.export import export_all
from job_assistant.linkedin_queue import manual_import_has_linkedin_job, mark_linkedin_manual_expired
from job_assistant.models import BatchStats
from job_assistant.paths import output_paths
from job_assistant.persistence import (
    prepare_authoritative_vacancies,
    read_headhunter_raw,
    read_linkedin_manual_raw,
    rebuild_from_authoritative_sources,
)
from job_assistant.publication import JOURNAL, PublicationError, publish_files, recover_publication
from job_assistant.raw_store import StoreError
from job_assistant.sources import load_statuses
from job_assistant.telegram_ingestion import fetch_telegram
from job_assistant.telegram_parser import parse_telegram_message
from job_assistant.utils import read_json_strict, write_json
from tests.fixtures.headhunter_records import REMOTE_BA
from tests.fixtures.telegram_messages import ORDINARY_VACANCY

runner = CliRunner()


@pytest.fixture
def preferences(tmp_path):
    preferences = load_preferences()
    preferences.outputs.directory = str(tmp_path)
    preferences.sources.email.enabled = False
    return preferences


def hh_raw(vacancy_id="old"):
    return [{"record": {**REMOTE_BA, "__source": "headhunter", "id": vacancy_id}}]


def seed_sources(preferences):
    paths = output_paths(preferences)
    write_json(paths["raw"], hh_raw())
    write_json(
        paths["manual_imports"],
        [
            {
                "record": {
                    "__source": "manual_capture",
                    "source_label": "linkedin",
                    "page_url": "https://www.linkedin.com/jobs/view/12",
                    "vacancy_title": "LinkedIn Analyst",
                    "visible_text": "Requirements analysis and API integration for stakeholders.",
                }
            }
        ],
    )
    write_json(
        paths["telegram_raw"], [{"record": parse_telegram_message(ORDINARY_VACANCY, preferences).target_records[0]}]
    )
    rebuild_from_authoritative_sources(preferences)
    return paths


@pytest.mark.parametrize("command", ["fetch", "fetch-all"])
@pytest.mark.parametrize("outcome", ["empty", "full", "error", "partial", "limit", "skipped"])
def test_hh_outcomes_preserve_other_sources_and_agree(monkeypatch, preferences, command, outcome):
    paths = seed_sources(preferences)
    before = {key: paths[key].read_bytes() for key in ("raw", "manual_imports", "telegram_raw")}
    raw = hh_raw("new") if outcome in {"full", "partial", "limit"} else []
    stats = BatchStats(
        errors=["fixture failure"] if outcome in {"error", "partial"} else [], complete=outcome != "limit"
    )
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr(
        "job_assistant.cli._fetch_source",
        lambda *a, **kw: (raw, stats, "already_successful_today" if outcome == "skipped" else None),
    )

    result = runner.invoke(app, [command])

    assert result.exit_code == 0, result.output
    stored = read_json_strict(paths["raw"])
    if outcome == "empty":
        assert stored == []
    elif outcome == "full":
        assert stored == hh_raw("new")
    else:
        assert paths["raw"].read_bytes() == before["raw"]
    for key in ("manual_imports", "telegram_raw"):
        assert paths[key].read_bytes() == before[key]
    sources = {item["source"] for item in read_json_strict(paths["combined_json"])}
    assert {"linkedin", "telegram"} <= sources
    assert ("headhunter" in sources) == (outcome != "empty")
    assert (load_statuses(preferences)["headhunter"].status == "success") == (outcome in {"empty", "full"})


@pytest.mark.parametrize("complete", [True, False])
def test_email_additions_use_new_empty_or_preserved_hh_snapshot(monkeypatch, preferences, complete):
    paths = seed_sources(preferences)
    preferences.sources.email.enabled = True
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli._fetch_source", lambda *a, **kw: ([], BatchStats(complete=complete), None))
    monkeypatch.setattr(
        "job_assistant.cli.sync_email_alerts",
        lambda *a, **kw: SimpleNamespace(headhunter_raw=hh_raw("email"), candidates=[], stats=BatchStats()),
    )

    result = runner.invoke(app, ["fetch-all"])

    assert result.exit_code == 0, result.output
    assert {item["record"]["id"] for item in read_json_strict(paths["raw"])} == (
        {"email"} if complete else {"old", "email"}
    )


@pytest.mark.parametrize("key", ["raw", "manual_imports", "telegram_raw"])
@pytest.mark.parametrize(
    "contents", [b'{"private-content":', b"\xff", b"null", b"{}", b"[null]", b'[{"record": []}]', b"[{}]"]
)
@pytest.mark.parametrize("replacement", [None, []])
def test_corrupt_authoritative_store_blocks_rebuild_without_changing_any_file(
    preferences, key, contents, replacement, caplog
):
    paths = seed_sources(preferences)
    paths[key].write_bytes(contents)
    before = {path: path.read_bytes() for path in paths["dir"].iterdir() if path.is_file()}

    for _ in range(2):
        with pytest.raises(StoreError) as error:
            rebuild_from_authoritative_sources(preferences, headhunter_raw=replacement)
        assert "private-content" not in str(error.value)
        assert "private-content" not in caplog.text
        assert {path: path.read_bytes() for path in paths["dir"].iterdir() if path.is_file()} == before
        assert not list(paths["dir"].glob("*.corrupt-*"))


@pytest.mark.parametrize(
    "key, record",
    [
        ("raw", {"id": "1"}),
        ("raw", {"name": []}),
        ("raw", {"name": "Analyst", "__source": "private-content"}),
        ("manual_imports", {"page_url": []}),
        ("manual_imports", {"page_url": "https://example.test", "__source": "private-content"}),
        ("manual_imports", {"page_url": "https://example.test", "visible_text": {}}),
        ("telegram_raw", {"title": "Analyst", "channel_id": 1}),
        ("telegram_raw", {"title": "Analyst", "channel_id": 1, "message_id": 2, "vacancy_index": []}),
    ],
)
def test_structurally_invalid_records_cannot_be_silently_dropped(preferences, key, record):
    paths = seed_sources(preferences)
    write_json(paths[key], [{"record": record}])
    previous_report = paths["combined_json"].read_bytes()
    previous_store = paths[key].read_bytes()
    with pytest.raises(StoreError) as error:
        rebuild_from_authoritative_sources(preferences, headhunter_raw=[])
    assert "private-content" not in str(error.value)
    assert paths["combined_json"].read_bytes() == previous_report
    assert paths[key].read_bytes() == previous_store


def test_first_run_missing_stores_is_valid(preferences):
    summary = rebuild_from_authoritative_sources(preferences)
    assert summary["normalized_records"] == 0
    assert read_headhunter_raw(preferences) == []


def test_headhunter_reader_rejects_id_only_records_before_any_normalizer_can_drop_them(preferences):
    path = output_paths(preferences)["raw"]
    write_json(path, [{"record": {"id": "fixture"}}])
    before = path.read_bytes()
    with pytest.raises(StoreError, match="Invalid HeadHunter raw store"):
        read_headhunter_raw(preferences)
    assert path.read_bytes() == before


def test_legacy_linkedin_wrapper_source_with_untagged_record_is_supported(preferences):
    paths = output_paths(preferences)
    write_json(
        paths["manual_imports"],
        [
            {
                "__source": "linkedin",
                "record": {"page_url": "https://www.linkedin.com/jobs/view/12", "visible_text": "Legacy capture"},
            }
        ],
    )
    assert read_linkedin_manual_raw(preferences)[0]["record"]["__source"] == "linkedin"
    assert rebuild_from_authoritative_sources(preferences)["normalized_records"] == 1


@pytest.mark.parametrize("operation", ["capture", "import", "expire", "duplicate", "telegram"])
def test_ingestion_paths_do_not_repair_or_replace_corrupt_raw_store(monkeypatch, preferences, operation, tmp_path):
    paths = seed_sources(preferences)
    key = "telegram_raw" if operation == "telegram" else "manual_imports"
    paths[key].write_text('{"private-content":', encoding="utf-8")
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    if operation == "import":
        fixture = tmp_path / "import.txt"
        fixture.write_text("Business Analyst\nRequirements analysis", encoding="utf-8")
        monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
        result = runner.invoke(
            app,
            [
                "import-manual",
                "--source",
                "linkedin",
                "--url",
                "https://www.linkedin.com/jobs/view/12",
                "--file",
                str(fixture),
            ],
        )
        assert result.exit_code == 1
        assert "LinkedIn manual store" in result.output
        assert "private-content" not in result.output
    else:
        with pytest.raises(StoreError):
            if operation == "expire":
                mark_linkedin_manual_expired(preferences, "12")
            elif operation == "duplicate":
                manual_import_has_linkedin_job(paths[key], "12")
            elif operation == "telegram":
                asyncio.run(fetch_telegram(preferences, reader=object()))
            else:
                payload = ManualCapturePayload(
                    page_url="https://www.linkedin.com/jobs/view/12",
                    source_label="linkedin",
                    visible_text="Requirements analysis " * 20,
                    document_title="Analyst",
                    hostname="www.linkedin.com",
                    captured_at="2026-10-01T00:00:00Z",
                )
                process_manual_capture(preferences, payload)
    assert {path: path.read_bytes() for path in before} == before


OUTPUT_KEYS = (
    "raw",
    "normalized",
    "combined_json",
    "csv",
    "combined_csv",
    "combined_shortlist",
    "blocked",
    "role_review",
    "headhunter_shortlist",
    "linkedin_shortlist",
    "telegram_shortlist",
    "summary",
)


@pytest.mark.parametrize("failed_key", OUTPUT_KEYS)
def test_failure_at_each_destination_is_detected_and_recovery_finishes_same_set(monkeypatch, preferences, failed_key):
    paths = seed_sources(preferences)
    raw, vacancies, _ = prepare_authoritative_vacancies(preferences, hh_raw("new"))
    old_summary = paths["summary"].read_bytes()
    replace = os.replace

    def fail_replace(src, dst):
        if Path(dst) == paths[failed_key]:
            raise PermissionError("private-content")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr("job_assistant.utils.os.replace", fail_replace)
        with pytest.raises(PublicationError, match="Incomplete output publication") as error:
            export_all(raw, vacancies, preferences, BatchStats(), [])
        assert "private-content" not in str(error.value)
        assert paths["summary"].read_bytes() == old_summary
        assert (paths["dir"] / JOURNAL).exists()
        # The next pipeline must recover BEFORE it can read partially replaced HH.
        with pytest.raises(PublicationError):
            prepare_authoritative_vacancies(preferences)

    manifest = read_json_strict(paths["dir"] / JOURNAL)
    expected = {
        paths["dir"] / entry["path"]: (paths["dir"] / manifest["stage"] / str(index)).read_bytes()
        for index, entry in enumerate(manifest["files"])
    }
    assert {paths[key] for key in OUTPUT_KEYS} == expected.keys()
    assert read_headhunter_raw(preferences) == hh_raw("new")
    assert {path: path.read_bytes() for path in expected} == expected
    assert not (paths["dir"] / JOURNAL).exists()
    assert recover_publication(paths["dir"]) is False


@pytest.mark.parametrize("failure", ["serialize", "stage_write", "stage_replace", "journal_replace"])
def test_preparation_failures_leave_previous_generation_intact(monkeypatch, preferences, failure):
    paths = seed_sources(preferences)
    before = {paths[key]: paths[key].read_bytes() for key in OUTPUT_KEYS}
    raw, vacancies, _ = prepare_authoritative_vacancies(preferences, hh_raw("new"))
    original_replace = os.replace
    original_write = __import__("job_assistant.publication", fromlist=["write_bytes"]).write_bytes

    def fail_replace(src, dst):
        if (failure == "stage_replace" and Path(dst).name == "2") or (
            failure == "journal_replace" and Path(dst).name == JOURNAL
        ):
            raise OSError("fixture disk failure")
        return original_replace(src, dst)

    def fail_write(path, payload):
        if failure == "stage_write" and path.name == "2":
            raise OSError("fixture disk full")
        return original_write(path, payload)

    monkeypatch.setattr("job_assistant.utils.os.replace", fail_replace)
    monkeypatch.setattr("job_assistant.publication.write_bytes", fail_write)
    if failure == "serialize":
        monkeypatch.setattr("job_assistant.export._csv_bytes", lambda *a: (_ for _ in ()).throw(ValueError("fixture")))
    with pytest.raises((PublicationError, ValueError)):
        export_all(raw, vacancies, preferences, BatchStats(), [])
    assert {path: path.read_bytes() for path in before} == before
    assert not (paths["dir"] / JOURNAL).exists()


@pytest.mark.parametrize("crash_at", ["staging", "journal", "raw", "markdown", "summary"])
def test_abrupt_process_exit_releases_lock_and_allows_recovery(tmp_path, crash_at):
    files = {
        tmp_path / "raw.json": b"old raw",
        tmp_path / "report.md": b"old markdown",
        tmp_path / "summary.json": b"old summary",
    }
    publish_files(tmp_path, files)
    script = """
import os, sys
from pathlib import Path
from job_assistant.publication import publish_files, JOURNAL
root = Path(sys.argv[1])
crash = sys.argv[2]
original = os.replace
def replace(src, dst):
    original(src, dst)
    name = Path(dst).name
    if ((crash == "staging" and name == "1") or
        (crash == "journal" and name == JOURNAL) or
        (crash == "raw" and name == "raw.json") or
        (crash == "markdown" and name == "report.md") or
        (crash == "summary" and name == "summary.json")):
        os._exit(73)
os.replace = replace
publish_files(root, {
    root / "raw.json": b"new raw", root / "report.md": b"new markdown", root / "summary.json": b"new summary"
})
"""
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHON_DOTENV_DISABLED": "1"}
    if "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), crash_at], cwd=tmp_path, env=env, timeout=15)
    assert result.returncode == 73
    if crash_at == "staging":
        assert {path: path.read_bytes() for path in files} == files
        assert recover_publication(tmp_path) is False
    else:
        assert (tmp_path / JOURNAL).exists()
        assert recover_publication(tmp_path) is True
        assert [path.read_bytes() for path in files] == [b"new raw", b"new markdown", b"new summary"]
        assert not (tmp_path / JOURNAL).exists()


def test_corrupt_staging_aborts_recovery_before_any_more_replacements(monkeypatch, tmp_path):
    original_replace = os.replace
    files = {tmp_path / "a": b"a", tmp_path / "b": b"b"}

    def fail_replace(src, dst):
        if Path(dst) == tmp_path / "b":
            raise OSError("fixture")
        original_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr("job_assistant.utils.os.replace", fail_replace)
        with pytest.raises(PublicationError):
            publish_files(tmp_path, files)
    manifest = read_json_strict(tmp_path / JOURNAL)
    (tmp_path / manifest["stage"] / "1").write_bytes(b"private-content")
    before = (tmp_path / "a").read_bytes()
    for _ in range(2):
        with pytest.raises(PublicationError, match="Invalid output publication") as error:
            recover_publication(tmp_path)
        assert "private-content" not in str(error.value)
        assert (tmp_path / "a").read_bytes() == before
        assert not (tmp_path / "b").exists()
        assert (tmp_path / JOURNAL).exists()


def test_partial_file_write_during_publication_keeps_old_file_and_recoverable_set(monkeypatch, preferences):
    import tempfile

    paths = seed_sources(preferences)
    before = paths["combined_json"].read_bytes()
    original_temp = tempfile.NamedTemporaryFile

    def faulty_temp(*args, **kwargs):
        handle = original_temp(*args, **kwargs)
        if kwargs.get("dir") == paths["dir"] and kwargs.get("prefix") == f".{paths['combined_json'].name}.":
            original_write = handle.write

            def partial_write(payload):
                original_write(payload[:5])
                raise OSError("fixture disk full")

            handle.write = partial_write
        return handle

    with monkeypatch.context() as patch:
        patch.setattr("job_assistant.utils.tempfile.NamedTemporaryFile", faulty_temp)
        with pytest.raises(PublicationError, match="Incomplete output publication"):
            rebuild_from_authoritative_sources(preferences, headhunter_raw=[])
        assert paths["combined_json"].read_bytes() == before
        assert (paths["dir"] / JOURNAL).exists()
    recover_publication(paths["dir"])
    assert read_json_strict(paths["raw"]) == []
    assert "headhunter" not in {item["source"] for item in read_json_strict(paths["combined_json"])}


def test_deduplicated_report_and_baseline_recover_together(monkeypatch, preferences):
    paths = seed_sources(preferences)
    write_json(paths["previous_combined_shortlist"], [])
    paths["deduplicated_shortlist"].write_text("previous report", encoding="utf-8")
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    original_replace = os.replace

    def fail_replace(src, dst):
        if Path(dst) == paths["previous_combined_shortlist"]:
            raise OSError("fixture")
        original_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr("job_assistant.utils.os.replace", fail_replace)
        result = runner.invoke(app, ["shortlist-deduplicated"])
        assert result.exit_code == 1
        assert "Incomplete output publication" in " ".join(result.output.split())
        assert read_json_strict(paths["previous_combined_shortlist"]) == []
    result = runner.invoke(app, ["recover-output"])
    assert result.exit_code == 0
    assert "recovered" in result.output
    assert read_json_strict(paths["previous_combined_shortlist"])
    assert not (paths["dir"] / JOURNAL).exists()


def test_corrupt_journal_stops_rebuild_with_sanitized_cli_error(monkeypatch, preferences):
    paths = seed_sources(preferences)
    (paths["dir"] / JOURNAL).write_text('{"private-content":', encoding="utf-8")
    before = {paths[key]: paths[key].read_bytes() for key in OUTPUT_KEYS}
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    result = runner.invoke(app, ["shortlist"])
    assert result.exit_code == 1
    assert "Invalid output publication" in " ".join(result.output.split())
    assert "private-content" not in result.output
    assert {path: path.read_bytes() for path in before} == before


def test_unreadable_store_is_not_treated_as_first_run(monkeypatch, preferences):
    paths = seed_sources(preferences)
    original_read = Path.read_text

    def denied(path, *args, **kwargs):
        if path == paths["raw"]:
            raise PermissionError("private-content")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(StoreError, match="Unreadable HeadHunter raw store") as error:
        rebuild_from_authoritative_sources(preferences, headhunter_raw=[])
    assert "private-content" not in str(error.value)


def test_publication_lock_rejects_overlapping_writers(tmp_path):
    from job_assistant.publication import _publication_lock

    with _publication_lock(tmp_path):
        with pytest.raises(PublicationError, match="busy"):
            publish_files(tmp_path, {tmp_path / "result": b"fixture"})
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize("command", ["fetch", "fetch-all"])
def test_failed_publication_does_not_mark_hh_success(monkeypatch, preferences, command):
    seed_sources(preferences)
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    monkeypatch.setattr("job_assistant.cli._fetch_source", lambda *a, **kw: ([], BatchStats(), None))
    monkeypatch.setattr(
        "job_assistant.cli.rebuild_from_authoritative_sources",
        lambda *a, **kw: (_ for _ in ()).throw(PublicationError("fixture")),
    )
    result = runner.invoke(app, [command])
    assert result.exit_code == 1
    assert load_statuses(preferences)["headhunter"].last_successful_run is None
