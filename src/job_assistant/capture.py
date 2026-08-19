from __future__ import annotations

import secrets
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .config import Preferences, load_preferences
from .linkedin_queue import (
    LinkedInQueueError,
    linkedin_job_id,
    linkedin_pipeline_outcome,
    manual_import_has_linkedin_job,
    open_next_pending,
    update_queue_after_capture,
    update_queue_processed,
)
from .models import BatchStats
from .paths import output_paths
from .persistence import rebuild_from_authoritative_sources
from .utils import ROOT, read_json, write_json

TOKEN_PATH = ROOT / "data" / "capture_token"
MAX_TEXT_LENGTH = 200_000
SourceLabel = Literal["linkedin", "headhunter", "greenhouse", "lever", "other"]


class ManualCapturePayload(BaseModel):
    page_url: str = Field(max_length=2048)
    document_title: str = Field(max_length=500)
    visible_text: str = Field(max_length=MAX_TEXT_LENGTH)
    selected_text: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    hostname: str = Field(max_length=255)
    captured_at: str = Field(max_length=80)
    source_label: SourceLabel = "other"
    vacancy_title: str | None = Field(default=None, max_length=500)
    company: str | None = Field(default=None, max_length=500)

    @field_validator("page_url")
    @classmethod
    def only_http_urls(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("page_url must be http(s)")
        return value


def get_or_create_token() -> str:
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    TOKEN_PATH.chmod(0o600)
    return token


def create_app(allowed_origin: str | None = None) -> FastAPI:
    app = FastAPI(title="Job Assistant Capture Server")
    origins = [allowed_origin] if allowed_origin else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["POST"],
        allow_headers=["x-job-assistant-token", "content-type"],
    )

    @app.post("/api/v1/manual-capture")
    def manual_capture(
        payload: ManualCapturePayload, x_job_assistant_token: str = Header(default="")
    ) -> dict[str, object]:
        expected = get_or_create_token()
        if not secrets.compare_digest(x_job_assistant_token, expected):
            raise HTTPException(status_code=401, detail="invalid capture token")
        preferences = load_preferences()
        try:
            return process_manual_capture(preferences, payload, auto_open_next=True)
        except LinkedInQueueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def process_manual_capture(
    preferences: Preferences, payload: ManualCapturePayload, auto_open_next: bool = False
) -> dict[str, object]:
    raw = {"query": "manual_capture", "record": {"__source": "manual_capture", **payload.model_dump()}}
    paths = output_paths(preferences)
    existing_raw = read_json(paths["manual_imports"]) if paths["manual_imports"].exists() else []
    queue_result: dict[str, object] = {"matched": False, "job_id": None, "queue_id": None}
    duplicate = False
    if payload.source_label == "linkedin":
        job_id = linkedin_job_id(payload.page_url)
        duplicate = manual_import_has_linkedin_job(paths["manual_imports"], job_id)
        queue_result = update_queue_after_capture(preferences, payload.page_url, payload.captured_at)
    if not duplicate:
        existing_raw.append(raw)
        write_json(paths["manual_imports"], existing_raw)
    summary = rebuild_from_authoritative_sources(preferences, BatchStats(raw_records_received=1))
    processed_result: dict[str, object] = {"matched": False}
    next_result: dict[str, object] = {"opened": False, "queue_id": None, "url": None, "error": None}
    warning = None
    should_process = (
        payload.source_label == "linkedin"
        and queue_result.get("matched")
        and isinstance(queue_result.get("job_id"), str)
    )
    if should_process:
        outcome = linkedin_pipeline_outcome(preferences, str(queue_result["job_id"])) or {
            "pipeline_outcome": "extraction_failed"
        }
        processed_result = update_queue_processed(preferences, str(queue_result["job_id"]), outcome)
        if auto_open_next and not duplicate:
            try:
                next_result = open_next_pending(preferences)
            except LinkedInQueueError as exc:
                warning = str(exc)
    if next_result.get("error"):
        warning = f"next_open_failed: {next_result.get('error')}"
    return {
        "status": "duplicate" if duplicate else "saved",
        "source": payload.source_label,
        "queue": queue_result,
        "processed": processed_result,
        "summary": summary,
        "next_opened": bool(next_result.get("opened")),
        "next_queue_id": next_result.get("queue_id"),
        "next_url": next_result.get("url"),
        "warning": warning,
    }


def run_capture_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    get_or_create_token()
    print(f"Capture token stored at {TOKEN_PATH}. Configure it in the extension options. Token value is not logged.")
    uvicorn.run(create_app(), host=host, port=port)
