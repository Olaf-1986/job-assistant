"""Consume evidence recovered from saved shortlist snapshots and audit decisions.

This is exclusion/comparison state, never an input source of vacancy candidates.
It deliberately does not claim complete history or fresh availability checks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import Preferences
from .deduplicate import vacancies_match
from .models import NormalizedVacancy
from .paths import output_paths
from .utils import read_json_strict


class RecoveredHistoryEntry(BaseModel):
    identity: NormalizedVacancy
    evidence: list[str] = Field(min_length=1)
    exported_at: datetime | None = None


class RecoveredExclusion(BaseModel):
    identity: NormalizedVacancy
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    observed_date: str
    work_mode: Literal["hybrid", "onsite"] | None = None


class RecoveredState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    reconstructed_at: datetime
    history_coverage: Literal["partial"] = "partial"
    older_history: Literal["unknown"] = "unknown"
    missing_intermediate_history: Literal["unknown"] = "unknown"
    snapshot_mtime_is_delivery_time: Literal[False] = False
    artifacts: list[dict[str, Any]]
    history: list[RecoveredHistoryEntry]
    exclusions: list[RecoveredExclusion]


def apply_recovered_state(
    vacancies: list[NormalizedVacancy], preferences: Preferences, *, state: RecoveredState | None = None
) -> None:
    """Reapply recovered decisions after merging, before shared filtering/scoring."""
    if state is None:
        path = output_paths(preferences)["recovered_state"]
        if not path.exists():
            return
        # A malformed file must stop the rebuild, never silently clear exclusions/history.
        state = RecoveredState.model_validate(read_json_strict(path))
    for vacancy in vacancies:
        history_evidence = sorted(
            {
                evidence
                for entry in state.history
                if vacancies_match(vacancy, entry.identity)
                for evidence in entry.evidence
            }
        )
        vacancy.previously_exported = bool(history_evidence)
        vacancy.source_metadata["recovered_history"] = {
            "coverage": state.history_coverage,
            "evidence": history_evidence,
            "prior_delivery": "observed_in_snapshot" if history_evidence else "unknown",
        }
        for exclusion in state.exclusions:
            if not vacancies_match(vacancy, exclusion.identity):
                continue
            vacancy.blocker = True
            vacancy.blocker_reasons = sorted(set([*vacancy.blocker_reasons, exclusion.reason]))
            if exclusion.work_mode is not None:
                vacancy.work_mode = exclusion.work_mode
            vacancy.source_metadata.setdefault("recovered_exclusions", []).append(
                exclusion.model_dump(mode="json", exclude={"identity"})
            )
        if not vacancy.previously_exported:
            vacancy.warnings = list(
                dict.fromkeys(
                    [
                        *vacancy.warnings,
                        "Not found in recovered snapshots; older delivery history is unknown",
                        "Recovered-history comparison does not verify current availability",
                    ]
                )
            )
