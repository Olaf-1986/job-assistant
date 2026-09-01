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
        "email_candidates": directory / preferences.outputs.email_candidates_file,
        "linkedin_email_queue": directory / preferences.outputs.linkedin_email_queue_file,
        "linkedin_rate_limit_state": directory / "linkedin_rate_limit_state.json",
        "email_state": directory / preferences.outputs.email_state_file,
        "telegram_raw": directory / preferences.outputs.telegram_raw_file,
        "telegram_checkpoints": directory / preferences.outputs.telegram_checkpoint_file,
        "telegram_failures": directory / preferences.outputs.telegram_failures_file,
        "telegram_audit": directory / preferences.outputs.telegram_audit_file,
        "telegram_shortlist": directory / preferences.outputs.telegram_shortlist_file,
    }
