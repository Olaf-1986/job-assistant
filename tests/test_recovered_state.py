from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from job_assistant.config import load_preferences
from job_assistant.export import export_source_shortlist, sorted_shortlist
from job_assistant.linkedin_queue import mark_linkedin_manual_expired
from job_assistant.models import NormalizedVacancy
from job_assistant.normalize import normalize_records
from job_assistant.paths import output_paths
from job_assistant.persistence import prepare_authoritative_vacancies, rebuild_from_authoritative_sources
from job_assistant.recovered_state import (
    RecoveredExclusion,
    RecoveredHistoryEntry,
    RecoveredState,
    apply_recovered_state,
)
from job_assistant.utils import read_json, write_json
from tests.fixtures.headhunter_records import REMOTE_BA


def preferences_at(tmp_path):
    preferences = load_preferences()
    return preferences.model_copy(
        update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path)})}
    )


def state(**overrides):
    return RecoveredState(
        reconstructed_at=datetime(2026, 9, 22, tzinfo=UTC),
        artifacts=[],
        history=overrides.get("history", []),
        exclusions=overrides.get("exclusions", []),
    )


def test_recovered_exclusion_survives_remote_raw_refresh_without_rewriting_raw(tmp_path):
    preferences = preferences_at(tmp_path)
    paths = output_paths(preferences)
    raw = [{"query": "saved", "record": {"__source": "headhunter", **REMOTE_BA}}]
    identity = normalize_records(raw, preferences)[0]
    write_json(paths["raw"], raw)
    recovery = state(
        exclusions=[
            RecoveredExclusion(
                identity=identity,
                reason="confirmed office attendance outside Tbilisi",
                evidence=["saved-audit#1"],
                observed_date="2026-09-22",
                work_mode="onsite",
            )
        ]
    )
    write_json(paths["recovered_state"], recovery.model_dump(mode="json"))

    for _ in range(2):
        _, vacancies, _ = prepare_authoritative_vacancies(preferences, headhunter_raw=raw)
        assert vacancies[0].blocker is True
        assert vacancies[0].work_mode == "onsite"
        assert "confirmed office attendance outside Tbilisi" in vacancies[0].blocker_reasons
        assert sorted_shortlist(vacancies, 50) == []
    assert read_json(paths["raw"]) == raw


def test_recovered_history_suppresses_every_export_but_preserves_candidate_store(tmp_path):
    preferences = preferences_at(tmp_path)
    paths = output_paths(preferences)
    raw = [{"query": "saved", "record": {"__source": "headhunter", **REMOTE_BA}}]
    identity = normalize_records(raw, preferences)[0]
    write_json(paths["raw"], raw)
    write_json(
        paths["recovered_state"],
        state(
            history=[
                RecoveredHistoryEntry(
                    identity=identity,
                    evidence=["shortlist-snapshot.md"],
                )
            ]
        ).model_dump(mode="json"),
    )

    summary = rebuild_from_authoritative_sources(preferences)
    _, vacancies, _ = prepare_authoritative_vacancies(preferences)
    source_path = paths["headhunter_shortlist"]
    assert export_source_shortlist(vacancies, "headhunter", 50, source_path) == 0
    assert summary["shortlist_count"] == summary["eligible_count"] == 0
    assert summary["previously_exported_count"] == 1
    assert len(read_json(paths["combined_json"])) == 1
    assert read_json(paths["combined_json"])[0]["previously_exported"] is True
    assert vacancies[0].blocker is False  # Previously delivered does not mean an invalid vacancy.


def test_missing_old_history_is_unknown_not_never_seen_and_does_not_add_candidates(tmp_path):
    preferences = preferences_at(tmp_path)
    paths = output_paths(preferences)
    known = NormalizedVacancy(
        source="linkedin",
        source_id="known",
        title="Known",
        normalized_title="known",
        fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    other = known.model_copy(update={"source_id": "other", "title": "Other", "normalized_title": "other"})
    write_json(
        paths["recovered_state"],
        state(
            history=[
                RecoveredHistoryEntry(
                    identity=known,
                    evidence=["saved.md"],
                )
            ]
        ).model_dump(mode="json"),
    )

    candidates = [other]
    apply_recovered_state(candidates, preferences)

    assert len(candidates) == 1
    assert other.previously_exported is False
    assert other.source_metadata["recovered_history"]["prior_delivery"] == "unknown"
    assert "older delivery history is unknown" in "; ".join(other.warnings)
    assert read_json(paths["recovered_state"])["snapshot_mtime_is_delivery_time"] is False
    assert read_json(paths["recovered_state"])["history"][0]["exported_at"] is None


def test_invalid_recovery_state_aborts_rebuild_without_overwriting_exports(tmp_path):
    preferences = preferences_at(tmp_path)
    paths = output_paths(preferences)
    write_json(paths["recovered_state"], {"schema_version": 999})
    write_json(paths["combined_json"], [{"preserve": True}])

    with pytest.raises(ValidationError):
        rebuild_from_authoritative_sources(preferences)

    assert read_json(paths["combined_json"]) == [{"preserve": True}]


@pytest.mark.parametrize(
    "invalid_state",
    [
        "{malformed",
        "[]",
        '{"schema_version": 1, "reconstructed_at": "not-a-date", "artifacts": [], "history": [], "exclusions": []}',
    ],
    ids=["malformed-json", "wrong-top-level", "invalid-schema"],
)
def test_invalid_recovered_state_remains_in_place_and_blocks_every_rebuild(tmp_path, invalid_state):
    preferences = preferences_at(tmp_path)
    paths = output_paths(preferences)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["raw"].write_text("[]", encoding="utf-8")
    paths["recovered_state"].write_text(invalid_state, encoding="utf-8")
    existing_outputs = {
        paths["normalized"]: b"existing normalized\n",
        paths["combined_json"]: b"existing combined json\n",
        paths["combined_csv"]: b"existing combined csv\n",
        paths["combined_shortlist"]: b"existing shortlist\n",
        paths["blocked"]: b"existing blocked\n",
        paths["summary"]: b"existing summary\n",
    }
    for path, content in existing_outputs.items():
        path.write_bytes(content)
    before_state = paths["recovered_state"].read_bytes()

    for _ in range(2):
        with pytest.raises((json.JSONDecodeError, ValidationError)):
            rebuild_from_authoritative_sources(preferences)

        assert paths["recovered_state"].read_bytes() == before_state
        assert {path: path.read_bytes() for path in existing_outputs} == existing_outputs
        assert list(paths["dir"].glob("recovered_shortlist_state.json.corrupt-*")) == []


def test_recovered_closure_keeps_evidence_date_and_capture_content(tmp_path):
    preferences = preferences_at(tmp_path)
    paths = output_paths(preferences)
    capture = {"page_url": "https://www.linkedin.com/jobs/view/1", "visible_text": "saved content"}
    write_json(paths["manual_imports"], [{"record": capture}])

    assert mark_linkedin_manual_expired(preferences, "1", checked_at="2026-09-22") is True
    first = read_json(paths["manual_imports"])
    mark_linkedin_manual_expired(preferences, "1", checked_at="2026-09-22")

    assert read_json(paths["manual_imports"]) == first
    assert first[0]["record"] == {**capture, "linkedin_status": "expired", "linkedin_status_checked_at": "2026-09-22"}


def test_recovered_decisions_match_aliases_after_duplicate_merging(tmp_path):
    preferences = preferences_at(tmp_path)
    known = NormalizedVacancy(
        source="linkedin",
        source_id="known",
        title="Known",
        normalized_title="known",
        source_url="https://www.linkedin.com/jobs/view/1",
        fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    merged = known.model_copy(
        update={
            "source": "headhunter",
            "source_id": "2",
            "source_url": "https://hh.ru/vacancy/2",
            "source_urls": [known.source_url],
        }
    )
    write_json(
        output_paths(preferences)["recovered_state"],
        state(
            exclusions=[
                RecoveredExclusion(
                    identity=known,
                    reason="closed",
                    evidence=["audit#1"],
                    observed_date="2026-09-22",
                )
            ]
        ).model_dump(mode="json"),
    )

    apply_recovered_state([merged], preferences)

    assert merged.blocker is True
    assert "closed" in merged.blocker_reasons
