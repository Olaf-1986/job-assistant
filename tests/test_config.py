from __future__ import annotations

from pathlib import Path

import pytest

from job_assistant.config import ConfigLoadError, load_preferences
from job_assistant.env import load_project_env


def test_config_validation_accepts_default_preferences():
    preferences = load_preferences()
    assert preferences.run.pages_per_query == 1
    assert "Product Owner" in preferences.queries.excluded_target_titles
    assert preferences.sources.linkedin.mode == "manual_current_page_only"
    assert not hasattr(preferences.sources, "habr_browser")
    assert not hasattr(preferences.sources, "wellfound_browser")
    assert preferences.languages.international_english_bonus == 0


def test_config_validation_rejects_invalid_yaml(tmp_path: Path):
    path = tmp_path / "preferences.yaml"
    path.write_text("run: []", encoding="utf-8")
    with pytest.raises(ConfigLoadError):
        load_preferences(path)


def test_project_env_loads_without_overriding_shell_environment(monkeypatch, tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("JOB_ASSISTANT_TEST_FROM_ENV=from-dotenv\nJOB_ASSISTANT_TEST_PRECEDENCE=from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("JOB_ASSISTANT_TEST_FROM_ENV", raising=False)
    monkeypatch.setenv("JOB_ASSISTANT_TEST_PRECEDENCE", "from-shell")

    assert load_project_env(tmp_path) is True

    assert __import__("os").getenv("JOB_ASSISTANT_TEST_FROM_ENV") == "from-dotenv"
    assert __import__("os").getenv("JOB_ASSISTANT_TEST_PRECEDENCE") == "from-shell"


def test_cli_loads_project_env_before_config_imports():
    import inspect
    import job_assistant.cli as cli

    source = inspect.getsource(cli)
    assert source.index("load_project_env()") < source.index("from .config import")
