from __future__ import annotations

import json

import httpx
import pytest

from job_assistant.cli import _print_summary
from job_assistant.config import load_preferences
from job_assistant.connectors.headhunter import _format_http_error
from job_assistant.errors import sanitize_error
from job_assistant.export import export_all
from job_assistant.models import BatchStats
from job_assistant.sources import load_statuses, status_path, write_statuses


@pytest.mark.parametrize(
    ("unsafe", "expected"),
    [
        ("aUtHoRiZaTiOn: bEaReR SYNTHETIC", "aUtHoRiZaTiOn: bEaReR REDACTED"),
        ("BEARER SYNTHETIC", "BEARER REDACTED"),
        ("AcCeSs_ToKeN=SYNTHETIC", "AcCeSs_ToKeN=REDACTED"),
        ("ToKeN=SYNTHETIC", "ToKeN=REDACTED"),
        ("PaSsWoRd=SYNTHETIC", "PaSsWoRd=REDACTED"),
        ("SeCrEt=SYNTHETIC", "SeCrEt=REDACTED"),
    ],
)
def test_sanitize_error_redacts_supported_secret_patterns(unsafe, expected):
    assert sanitize_error(unsafe) == expected


def test_sanitize_error_omits_multiline_response_body_and_caps_length():
    sanitized = sanitize_error("request failed response_body=first line\nSYNTHETIC\nlast line")
    assert sanitized == "request failed response_body=[omitted]"
    assert "SYNTHETIC" not in sanitized

    long_error = sanitize_error("x" * 300)
    assert len(long_error) == 240
    assert long_error.endswith("...")


def test_sanitize_error_leaves_safe_errors_unchanged():
    assert sanitize_error("request failed: timeout") == "request failed: timeout"


def test_run_summary_persists_only_sanitized_errors(tmp_path):
    preferences = load_preferences()
    preferences = preferences.model_copy(
        update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path)})}
    )
    error = "request failed password=SYNTHETIC response_body=raw body"

    export_all([], [], preferences, BatchStats(errors=[error]), [])

    summary = json.loads((tmp_path / preferences.outputs.summary_file).read_text(encoding="utf-8"))
    assert summary["errors"] == ["request failed password=REDACTED response_body=[omitted]"]
    assert "SYNTHETIC" not in (tmp_path / preferences.outputs.summary_file).read_text(encoding="utf-8")
    assert "raw body" not in (tmp_path / preferences.outputs.summary_file).read_text(encoding="utf-8")


def test_legacy_unsafe_last_error_is_sanitized_on_load_and_rewrite(tmp_path):
    preferences = load_preferences()
    preferences = preferences.model_copy(
        update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path)})}
    )
    status_file = status_path(preferences)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(
        json.dumps(
            {
                "headhunter": {
                    "source": "headhunter",
                    "enabled": True,
                    "mode": "api_search",
                    "last_error": "legacy token=SYNTHETIC response_body=raw body",
                }
            }
        ),
        encoding="utf-8",
    )

    statuses = load_statuses(preferences)
    assert statuses["headhunter"].last_error == "legacy token=REDACTED response_body=[omitted]"
    write_statuses(preferences, statuses)
    persisted = status_file.read_text(encoding="utf-8")
    assert "SYNTHETIC" not in persisted
    assert "raw body" not in persisted


def test_console_uses_the_shared_sanitized_error_representation(capsys):
    _print_summary({"errors": ["failure secret=SYNTHETIC"]})
    output = capsys.readouterr().out
    assert "secret=REDACTED" in output
    assert "SYNTHETIC" not in output


def test_headhunter_http_errors_do_not_expose_response_body():
    response = httpx.Response(
        500,
        text="raw response body with SYNTHETIC",
        request=httpx.Request("GET", "https://hh.test/vacancies"),
    )
    error = httpx.HTTPStatusError("server error", request=response.request, response=response)

    formatted = _format_http_error(error)

    assert formatted == "HeadHunter HTTP request failed; http_status=500"
    assert "raw response body" not in formatted
    assert "SYNTHETIC" not in formatted


def test_headhunter_captcha_classification_does_not_expose_response_body():
    response = httpx.Response(
        403,
        text="captcha required; SYNTHETIC",
        request=httpx.Request("GET", "https://hh.test/vacancies"),
    )
    error = httpx.HTTPStatusError("forbidden", request=response.request, response=response)

    formatted = _format_http_error(error)

    assert formatted.startswith("captcha_required:")
    assert "SYNTHETIC" not in formatted
