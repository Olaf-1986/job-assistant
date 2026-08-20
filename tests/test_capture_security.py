from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import httpx

from job_assistant.capture import create_app, run_capture_server
from job_assistant.config import load_preferences
from job_assistant.utils import read_json


def post_to_app(app, url: str, base_url: str, **kwargs) -> httpx.Response:
    async def post() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            return await client.post(url, **kwargs)

    return asyncio.run(post())


def capture_payload() -> dict[str, str]:
    return {
        "page_url": "https://linkedin.test/jobs/1",
        "document_title": "Synthetic capture",
        "visible_text": "Requirements analysis",
        "selected_text": "",
        "hostname": "linkedin.test",
        "captured_at": "2026-01-01T00:00:00Z",
        "source_label": "linkedin",
    }


def test_token_is_loaded_once_and_original_value_survives_provider_changes(monkeypatch):
    calls: list[int] = []

    def get_token() -> str:
        calls.append(1)
        return "synthetic-original-token"

    monkeypatch.setattr("job_assistant.capture.get_or_create_token", get_token)
    monkeypatch.setattr(
        "job_assistant.capture.process_manual_capture",
        lambda *args, **kwargs: {"status": "saved"},
    )
    app = create_app()
    monkeypatch.setattr(
        "job_assistant.capture.get_or_create_token",
        lambda: (_ for _ in ()).throw(AssertionError("token provider called per request")),
    )

    for host in ("localhost", "127.0.0.1:8765"):
        response = post_to_app(
            app,
            "/api/v1/manual-capture",
            f"http://{host}",
            json=capture_payload(),
            headers={"x-job-assistant-token": "synthetic-original-token"},
        )
        assert response.status_code == 200
    assert len(calls) == 1


def test_token_file_changes_do_not_rotate_active_token(monkeypatch, tmp_path):
    token_path = tmp_path / "capture_token"
    token_path.write_text("synthetic-original-token", encoding="utf-8")
    monkeypatch.setattr("job_assistant.capture.TOKEN_PATH", token_path)
    monkeypatch.setattr(
        "job_assistant.capture.process_manual_capture",
        lambda *args, **kwargs: {"status": "saved"},
    )
    app = create_app()
    token_path.unlink()

    valid = post_to_app(
        app,
        "/api/v1/manual-capture",
        "http://localhost",
        json=capture_payload(),
        headers={"x-job-assistant-token": "synthetic-original-token"},
    )
    token_path.write_text("synthetic-replacement-token", encoding="utf-8")
    invalid = post_to_app(
        app,
        "/api/v1/manual-capture",
        "http://localhost",
        json=capture_payload(),
        headers={"x-job-assistant-token": "synthetic-replacement-token"},
    )
    assert valid.status_code == 200
    assert invalid.status_code == 401


def test_run_capture_server_loads_token_only_through_create_app(monkeypatch, capsys):
    calls: list[int] = []
    uvicorn_calls: list[tuple[tuple, dict]] = []

    def get_token() -> str:
        calls.append(1)
        return "synthetic-run-token"

    monkeypatch.setattr("job_assistant.capture.get_or_create_token", get_token)

    def fake_run(*args, **kwargs):
        uvicorn_calls.append((args, kwargs))

    fake_uvicorn = SimpleNamespace(run=fake_run)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    run_capture_server()

    assert calls == [1]
    assert len(uvicorn_calls) == 1
    assert uvicorn_calls[0][1] == {"host": "127.0.0.1", "port": 8765}
    assert "synthetic-run-token" not in capsys.readouterr().out


def test_trusted_hosts_and_ports_are_accepted(monkeypatch):
    monkeypatch.setattr("job_assistant.capture.get_or_create_token", lambda: "synthetic-token")
    monkeypatch.setattr(
        "job_assistant.capture.process_manual_capture",
        lambda *args, **kwargs: {"status": "saved"},
    )
    app = create_app()

    for host in ("localhost", "localhost:8765", "127.0.0.1", "127.0.0.1:8765"):
        response = post_to_app(
            app,
            "/api/v1/manual-capture",
            f"http://{host}",
            json=capture_payload(),
            headers={"x-job-assistant-token": "synthetic-token"},
        )
        assert response.status_code == 200


def test_untrusted_host_is_rejected_before_capture_processing(monkeypatch):
    monkeypatch.setattr("job_assistant.capture.get_or_create_token", lambda: "synthetic-token")
    processed = []
    monkeypatch.setattr(
        "job_assistant.capture.process_manual_capture",
        lambda *args, **kwargs: processed.append(True),
    )
    app = create_app()

    response = post_to_app(
        app,
        "/api/v1/manual-capture",
        "http://malicious.example",
        json=capture_payload(),
        headers={"x-job-assistant-token": "synthetic-token"},
    )

    assert response.status_code == 400
    assert processed == []
    assert "synthetic-token" not in response.text


def test_concurrent_captures_retain_every_manual_import(monkeypatch, tmp_path):
    preferences = load_preferences()
    preferences = preferences.model_copy(
        update={"outputs": preferences.outputs.model_copy(update={"directory": str(tmp_path / "output")})}
    )
    monkeypatch.setattr("job_assistant.capture.get_or_create_token", lambda: "synthetic-token")
    monkeypatch.setattr("job_assistant.capture.load_preferences", lambda: preferences)

    from job_assistant.capture import write_json as original_write_json

    def delayed_write_json(path, data):
        time.sleep(0.05)
        original_write_json(path, data)

    monkeypatch.setattr("job_assistant.capture.write_json", delayed_write_json)
    app = create_app()

    async def capture_all() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/api/v1/manual-capture",
                        json={
                            **capture_payload(),
                            "source_label": "other",
                            "vacancy_title": title,
                            "page_url": f"https://other.test/jobs/{index}",
                        },
                        headers={"x-job-assistant-token": "synthetic-token"},
                    )
                    for index, title in enumerate(("First capture", "Second capture"), start=1)
                ]
            )

    responses = asyncio.run(capture_all())
    manual_imports = read_json(preferences.outputs.output_dir() / preferences.outputs.manual_imports_file, [])

    assert [response.status_code for response in responses] == [200, 200]
    assert {item["record"]["vacancy_title"] for item in manual_imports} == {"First capture", "Second capture"}
