from __future__ import annotations

from job_assistant.config import load_preferences
from job_assistant.paths import output_paths


def test_output_paths_preserve_configured_keys_and_filenames(tmp_path):
    preferences = load_preferences()
    outputs = preferences.outputs.model_copy(update={"directory": str(tmp_path)})
    preferences = preferences.model_copy(update={"outputs": outputs})

    paths = output_paths(preferences)

    expected_filenames = {
        "raw": outputs.raw_file,
        "normalized": outputs.normalized_file,
        "csv": outputs.csv_file,
        "shortlist": outputs.shortlist_file,
        "blocked": outputs.blocked_file,
        "role_review": outputs.role_review_file,
        "summary": outputs.summary_file,
        "combined_json": outputs.combined_json_file,
        "combined_csv": outputs.combined_csv_file,
        "combined_shortlist": outputs.combined_shortlist_file,
        "manual_imports": outputs.manual_imports_file,
        "source_quality": outputs.source_quality_file,
        "email_candidates": outputs.email_candidates_file,
        "linkedin_email_queue": outputs.linkedin_email_queue_file,
        "email_state": outputs.email_state_file,
    }
    assert set(paths) == {"dir", *expected_filenames}
    assert paths["dir"] == tmp_path
    assert {key: path.name for key, path in paths.items() if key != "dir"} == expected_filenames
