from __future__ import annotations

import json
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator

from .config import Preferences
from .models import RawRecord
from .paths import output_paths
from .utils import read_json_strict, write_json


class RvcError(RuntimeError):
    pass


class RvcSalaryCard(BaseModel):
    display: str | None = None


class RvcCard(BaseModel):
    """Only documented teaser fields are retained; server instructions are never stored."""

    masked_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    title: str = Field(min_length=1, max_length=180)
    company: str = Field(min_length=1, max_length=120)
    attributed_url: str
    work_arrangement: Literal["FULLY_REMOTE", "REMOTE_IN_COUNTRY", "HYBRID", "ON_SITE"] | None
    work_arrangement_display: str
    relative_age: str | None
    salary: RvcSalaryCard
    skills: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def check_url(self) -> RvcCard:
        url = urlsplit(self.attributed_url)
        if url.scheme != "https" or url.netloc != "app.rvc.global" or url.path != f"/vacancy/view/{self.masked_id}":
            raise ValueError("Unexpected RVC vacancy URL")
        return self


class RvcReview(BaseModel):
    """Explicit user review, independent of RVC's inferred location and relative age."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    title: str = Field(min_length=2, max_length=300)
    company: str = Field(min_length=2, max_length=300)
    description_text: str = Field(min_length=200, max_length=200_000)
    published_at: AwareDatetime
    location: str = Field(min_length=2, max_length=500)
    work_mode: Literal["remote", "hybrid", "onsite"]
    eligible_from_georgia: Literal[True]
    accepting_applications: Literal[True]
    application_url: HttpUrl | None = None
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    salary_currency: str | None = None
    salary_period: Literal["hour", "month", "year"] | None = None
    salary_gross: bool | None = None
    hiring_country: str | None = None


class RvcEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    card: RvcCard
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    status: Literal["pending", "reviewed", "rejected", "expired"] = "pending"
    review: RvcReview | None = None
    reviewed_at: AwareDatetime | None = None
    review_minutes: float = Field(default=0, ge=0)
    existing_sources_at_review: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_review(self) -> RvcEntry:
        if self.status == "reviewed" and (self.review is None or self.reviewed_at is None):
            raise ValueError("Reviewed entry is missing its review")
        return self

    def raw_record(self) -> RawRecord | None:
        if self.review is None:
            return None
        return {
            **self.review.model_dump(mode="json"),
            "__source": "rvc",
            "id": self.card.masked_id,
            "url": self.card.attributed_url,
            "review_status": self.status,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }


class RvcRun(BaseModel):
    at: AwareDatetime
    query: str
    countries: list[str]
    since_days: int
    returned: int
    new_candidates: int
    total_available: int
    truncated: bool


class RvcStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    entries: dict[str, RvcEntry] = Field(default_factory=dict)
    runs: list[RvcRun] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_keys(self) -> RvcStore:
        if any(key != entry.card.masked_id for key, entry in self.entries.items()):
            raise ValueError("RVC identity mismatch")
        return self


def load_rvc_store(preferences: Preferences) -> RvcStore:
    path = output_paths(preferences)["rvc_raw"]
    if not path.exists():
        return RvcStore()
    try:
        data = read_json_strict(path)
        if not isinstance(data, dict) or not {"version", "entries", "runs"} <= data.keys():
            raise ValueError("Missing RVC store fields")
        return RvcStore.model_validate(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
        raise RvcError("Invalid RVC store; file retained unchanged. Repair it before continuing.") from None


def save_rvc_store(preferences: Preferences, store: RvcStore) -> None:
    write_json(output_paths(preferences)["rvc_raw"], store.model_dump(mode="json"))


def read_rvc_raw(preferences: Preferences) -> list[RawRecord]:
    return [
        {"query": "rvc_review", "record": raw}
        for entry in load_rvc_store(preferences).entries.values()
        if (raw := entry.raw_record()) is not None
    ]
