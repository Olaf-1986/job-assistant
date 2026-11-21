from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from job_assistant.cli import app
from job_assistant.config import load_preferences
from job_assistant.export import export_all
from job_assistant.models import BatchStats, NormalizedVacancy
from job_assistant.paths import output_paths
from job_assistant.persistence import rebuild_from_authoritative_sources
from job_assistant.publication import PublicationError
from job_assistant.recovered_state import RecoveredExclusion, RecoveredHistoryEntry, RecoveredState
from job_assistant.telegram_ingestion import TelegramFetchResult
from job_assistant.utils import read_json_strict, write_json
from tests.fixtures.headhunter_records import REMOTE_BA

runner = CliRunner()


@pytest.fixture
def prefs(tmp_path, monkeypatch):
    preferences = load_preferences()
    preferences.outputs.directory = str(tmp_path)
    preferences.run.shortlist_size = 2
    monkeypatch.setattr("job_assistant.cli.load_preferences", lambda: preferences)
    return preferences


def job(number, source="headhunter", **fields):
    return NormalizedVacancy(
        **(
            {
                "source": source,
                "source_id": str(number),
                "source_url": f"https://example.test/{source}/{number}",
                "title": f"Analyst {number}",
                "normalized_title": f"analyst {number}",
                "score": 100 - number,
                "fetched_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
            | fields
        )
    )


def publish(preferences, jobs, **options):
    return export_all([], jobs, preferences, BatchStats(), [], **options)


def urls(path):
    return [
        line.removeprefix("- Vacancy URL: ")
        for line in path.read_text().splitlines()
        if line.startswith("- Vacancy URL: ")
    ]


def test_three_publications_backfill_then_empty_and_opt_in_is_local(prefs, monkeypatch):
    paths = output_paths(prefs)
    jobs = [job(i) for i in range(1, 7)]
    monkeypatch.setattr("job_assistant.cli.prepare_authoritative_vacancies", lambda *a, **kw: ([], jobs, 0))

    publish(prefs, jobs)
    assert urls(paths["combined_shortlist"]) == [v.source_url for v in jobs[:2]]
    result = runner.invoke(app, ["shortlist-deduplicated"])
    assert result.exit_code == 0, result.output
    assert urls(paths["deduplicated_shortlist"]) == [v.source_url for v in jobs[2:4]]
    publish(prefs, jobs)
    assert urls(paths["combined_shortlist"]) == [v.source_url for v in jobs[4:]]
    history = paths["shortlist_history"].read_bytes()

    assert publish(prefs, jobs)["shortlist_count"] == 0
    assert urls(paths["combined_shortlist"]) == []
    assert paths["shortlist_history"].read_bytes() == history
    result = runner.invoke(app, ["shortlist-deduplicated", "--include-seen"])
    assert result.exit_code == 0, result.output
    assert urls(paths["deduplicated_shortlist"]) == [v.source_url for v in jobs[:2]]
    assert paths["shortlist_history"].read_bytes() == history
    assert publish(prefs, jobs)["shortlist_count"] == 0
    assert len(read_json_strict(paths["combined_json"])) == len(jobs)


def test_source_projection_and_repost_share_global_history(prefs):
    paths = output_paths(prefs)
    text = "Research customer needs and document product requirements with stakeholders. " * 10
    merged = job(
        1,
        sources=["headhunter", "linkedin"],
        source_urls=["https://www.linkedin.com/jobs/view/71"],
        company="Acme",
        description_text=text,
    )
    auxiliary_only = job(9, "telegram")
    publish(prefs, [merged, auxiliary_only], shortlist_source="linkedin")
    assert urls(paths["combined_shortlist"]) == merged.source_urls
    assert urls(paths["telegram_shortlist"]) == [auxiliary_only.source_url]

    # Each alias is the only evidence in a later, independently acquired record.
    old_hh = job(40, "telegram", source_url=merged.source_url)
    old_li = job(71, "linkedin", source_url=None)
    repost = job(41, "telegram", title=merged.title, company="ACME", description_text=text + "Updated.")
    for seen in [old_hh, old_li, repost]:
        assert publish(prefs, [seen])["shortlist_count"] == 0
    unrelated = job(42, "telegram", title=merged.title, company="Acme", description_text="Completely different work")
    publish(prefs, [unrelated, auxiliary_only], shortlist_source="telegram")
    assert set(urls(paths["combined_shortlist"])) == {unrelated.source_url, auxiliary_only.source_url}


def test_bootstrap_checks_ranked_card_urls_and_preserves_recovery(prefs):
    paths = output_paths(prefs)
    past, recovered, unseen, blocked = [job(i) for i in range(1, 5)]
    backup = paths["dir"] / "state_repair_20260922"
    backup.mkdir()
    (backup / "deduplicated_shortlist.before-filter-2026-09-22.md").write_text(
        f"# Job Shortlist\n\n## 1. Old Analyst - Example\n- Application URL: {past.source_url}\n"
    )
    other_report = f"# Job Shortlist\n\n## 1. Other - Example\n- Vacancy URL: {unseen.source_url}\n"
    paths["telegram_shortlist"].write_text(other_report)
    (paths["dir"] / "shortlist_format_preview.md").write_text(other_report)
    write_json(paths["combined_json"], [v.model_dump(mode="json") for v in [past, unseen, blocked]])
    write_json(paths["previous_combined_shortlist"], [v.model_dump(mode="json") for v in [past, unseen]])
    state = RecoveredState(
        reconstructed_at=recovered.fetched_at,
        artifacts=[],
        history=[RecoveredHistoryEntry(identity=recovered, evidence=["historical.md#2"])],
        exclusions=[
            RecoveredExclusion(identity=blocked, reason="closed", evidence=["audit"], observed_date="2026-01-01")
        ],
    )
    write_json(paths["recovered_state"], state.model_dump(mode="json"))
    legacy_files = {paths[key]: paths[key].read_bytes() for key in ["recovered_state", "previous_combined_shortlist"]}

    # The historical URL corroborates the old ID from the baseline even though
    # the current record has a different URL and title.
    moved = job(1, title="Renamed Analyst", source_url="https://example.test/moved")
    publish(prefs, [unseen, moved, recovered])
    assert urls(paths["combined_shortlist"]) == [unseen.source_url]
    assert {path: path.read_bytes() for path in legacy_files} == legacy_files
    assert "id:headhunter:2" in read_json_strict(paths["shortlist_history"])["keys"]


def test_include_seen_preserves_blockers_and_records_new_cards(prefs):
    paths = output_paths(prefs)
    raw = [{"record": REMOTE_BA}]
    write_json(paths["raw"], raw)
    original = runner.invoke(app, ["shortlist"])
    assert original.exit_code == 0 and urls(paths["combined_shortlist"])
    repeated = runner.invoke(app, ["shortlist", "--source", "hh", "--include-seen"])
    assert repeated.exit_code == 0 and urls(paths["combined_shortlist"]) == [REMOTE_BA["alternate_url"]]
    blocked = job(1, source_url=REMOTE_BA["alternate_url"], blocker=True)
    assert publish(prefs, [blocked], include_seen=True)["shortlist_count"] == 0
    assert urls(paths["combined_shortlist"]) == []
    fresh = job(8)
    publish(prefs, [fresh], include_seen=True)
    assert publish(prefs, [fresh])["shortlist_count"] == 0


@pytest.mark.parametrize("command", ["shortlist", "shortlist-deduplicated"])
@pytest.mark.parametrize("bad", [b"{", b"{}", b'{"schema_version":1,"keys":["broken"],"reposts":{}}'])
def test_corrupt_history_blocks_even_opt_in_and_preserves_reports(prefs, command, bad):
    paths = output_paths(prefs)
    publish(prefs, [job(1)])
    paths["shortlist_history"].write_bytes(bad)
    before = {path: path.read_bytes() for path in paths.values() if path.is_file()}
    result = runner.invoke(app, [command, "--include-seen"])
    assert result.exit_code == 1
    assert "Invalid shortlist history" in result.output
    assert {path: path.read_bytes() for path in paths.values() if path.is_file()} == before


def test_qf_nested_rebuilds_publish_only_final_selection(prefs, monkeypatch):
    paths = output_paths(prefs)
    prefs.sources.email.enabled = False
    write_json(paths["raw"], [{"record": REMOTE_BA}])
    monkeypatch.setattr("job_assistant.cli._fetch_source", lambda *a, **kw: ([], BatchStats(), "skipped"))

    def check_intermediate():
        assert not paths["shortlist_history"].exists()
        assert not paths["combined_shortlist"].exists()
        assert read_json_strict(paths["combined_json"])
        rebuild_from_authoritative_sources(prefs)
        assert not paths["shortlist_history"].exists()

    def linkedin(*args, **kwargs):
        check_intermediate()
        return {
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
        check_intermediate()
        return TelegramFetchResult(dry_run=False)

    monkeypatch.setattr("job_assistant.cli.fetch_pending_linkedin", linkedin)
    monkeypatch.setattr("job_assistant.cli.fetch_telegram", telegram)
    result = runner.invoke(app, ["qf", "1w", "all", "2"])
    assert result.exit_code == 0, result.output
    assert urls(paths["combined_shortlist"]) == [REMOTE_BA["alternate_url"]]
    assert runner.invoke(app, ["shortlist"]).exit_code == 0
    assert urls(paths["combined_shortlist"]) == []


def test_stale_publisher_cannot_overwrite_newer_delivery(prefs, monkeypatch):
    from job_assistant import export

    publish_files = export.publish_files

    def intervening_run(*args, **kwargs):
        monkeypatch.setattr(export, "publish_files", publish_files)
        publish(prefs, [job(2)])
        publish_files(*args, **kwargs)

    monkeypatch.setattr(export, "publish_files", intervening_run)
    with pytest.raises(PublicationError, match="history changed"):
        publish(prefs, [job(1)])
    assert publish(prefs, [job(1), job(2)])["shortlist_count"] == 1
    assert urls(output_paths(prefs)["combined_shortlist"]) == [job(1).source_url]


def test_return_of_older_source_snapshot_after_an_intervening_delivery(prefs):
    paths = output_paths(prefs)
    second = {
        **REMOTE_BA,
        "id": "456",
        "name": "Senior Business Analyst",
        "alternate_url": "https://hh.ru/vacancy/456",
        "employer": {"name": "Second Co"},
        "description": "Analyze software requirements and document stakeholder needs.",
    }
    for record, expected in [
        (REMOTE_BA, [REMOTE_BA["alternate_url"]]),
        (second, [second["alternate_url"]]),
        (REMOTE_BA, []),
    ]:
        rebuild_from_authoritative_sources(prefs, headhunter_raw=[{"record": record}])
        assert urls(paths["combined_shortlist"]) == expected
        assert read_json_strict(paths["raw"])[0]["record"]["id"] == record["id"]


def test_missing_initialized_history_requires_restore(prefs):
    paths = output_paths(prefs)
    publish(prefs, [job(1)])
    report = paths["combined_shortlist"].read_bytes()
    paths["shortlist_history"].unlink()
    result = runner.invoke(app, ["shortlist"])
    assert result.exit_code == 1 and "history is missing" in result.output
    assert paths["combined_shortlist"].read_bytes() == report
    assert not paths["shortlist_history"].exists()


def test_opt_in_allows_recovered_history_but_respects_recovered_exclusions(prefs):
    from job_assistant.normalize import normalize_records

    paths = output_paths(prefs)
    raw = [{"record": {**REMOTE_BA, "__source": "headhunter"}}]
    write_json(paths["raw"], raw)
    identity = normalize_records(raw, prefs)[0]
    state = RecoveredState(
        reconstructed_at=identity.fetched_at,
        artifacts=[],
        history=[RecoveredHistoryEntry(identity=identity, evidence=["saved-shortlist.md#1"])],
        exclusions=[],
    )
    write_json(paths["recovered_state"], state.model_dump(mode="json"))
    assert runner.invoke(app, ["shortlist"]).exit_code == 0
    assert not urls(paths["combined_shortlist"])
    assert runner.invoke(app, ["shortlist", "--include-seen"]).exit_code == 0
    assert urls(paths["combined_shortlist"]) == [identity.source_url]

    state.exclusions.append(
        RecoveredExclusion(
            identity=identity,
            reason="confirmed closed",
            evidence=["audit"],
            observed_date="2026-11-01",
        )
    )
    write_json(paths["recovered_state"], state.model_dump(mode="json"))
    result = runner.invoke(app, ["shortlist-deduplicated", "--include-seen"])
    assert result.exit_code == 0 and not urls(paths["deduplicated_shortlist"])
