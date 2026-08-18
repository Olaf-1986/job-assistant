from __future__ import annotations

from pathlib import Path

from .config import Preferences


def output_paths(preferences: Preferences) -> dict[str, Path]:
    directory = preferences.outputs.output_dir()
    return {
        "dir": directory,
        "raw": directory / preferences.outputs.raw_file,
        "normalized": directory / preferences.outputs.normalized_file,
        "csv": directory / preferences.outputs.csv_file,
        "shortlist": directory / preferences.outputs.shortlist_file,
        "blocked": directory / preferences.outputs.blocked_file,
        "role_review": directory / preferences.outputs.role_review_file,
        "summary": directory / preferences.outputs.summary_file,
        "combined_json": directory / preferences.outputs.combined_json_file,
        "combined_csv": directory / preferences.outputs.combined_csv_file,
        "combined_shortlist": directory / preferences.outputs.combined_shortlist_file,
        "manual_imports": directory / preferences.outputs.manual_imports_file,
        "source_quality": directory / preferences.outputs.source_quality_file,
        "email_candidates": directory / preferences.outputs.email_candidates_file,
        "linkedin_email_queue": directory / preferences.outputs.linkedin_email_queue_file,
        "email_state": directory / preferences.outputs.email_state_file,
    }
