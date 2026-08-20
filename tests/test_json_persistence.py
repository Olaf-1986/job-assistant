from __future__ import annotations

import pytest

from job_assistant.utils import read_json, write_json


def test_write_json_preserves_existing_file_and_cleans_temp_file_on_replace_failure(monkeypatch, tmp_path):
    path = tmp_path / "records.json"
    path.write_text('{"existing": true}', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("job_assistant.utils.os.replace", fail_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        write_json(path, {"replacement": True})

    assert read_json(path) == {"existing": True}
    assert list(tmp_path.glob(".records.json.*.tmp")) == []


def test_read_json_recovers_corrupt_file_without_logging_contents(tmp_path, caplog):
    path = tmp_path / "records.json"
    path.write_text('{"synthetic-secret":', encoding="utf-8")
    default = {"fallback": True}

    with caplog.at_level("WARNING", logger="job_assistant.utils"):
        result = read_json(path, default)

    backups = list(tmp_path.glob("records.json.corrupt-*"))
    assert result is default
    assert not path.exists()
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    assert "synthetic-secret" not in caplog.text
    assert "contents omitted" in caplog.text
