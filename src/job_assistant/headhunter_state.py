"""Evidence of successful HH detail retrieval, independent of list order or publication date."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import RawRecord
from .utils import utc_now

RESPONSE_METADATA = "__hh_response"


def is_complete_hh_detail(record: Any, vacancy_id: str) -> bool:
    return (
        isinstance(record, dict)
        and record.get("__source") in (None, "headhunter")
        and (type(record.get("id")) is int or isinstance(record.get("id"), str) and bool(record["id"].strip()))
        and str(record.get("id")) == vacancy_id
        and isinstance(record.get("name"), str)
        and bool(record["name"].strip())
        and isinstance(record.get("description"), str)
        and bool(record["description"].strip())
    )


def observed_hh_detail(record: RawRecord, origin: str) -> RawRecord:
    """Called only after a successful, validated API detail response."""
    return {
        **record,
        RESPONSE_METADATA: {
            "version": 1,
            "origin": origin,
            "complete": True,
            "received_at": utc_now().isoformat(),
        },
    }


def successful_observation(item: RawRecord) -> datetime | None:
    record = item.get("record", item)
    if not isinstance(record, dict):
        return None
    metadata = record.get(RESPONSE_METADATA)
    if not isinstance(metadata, dict) or not is_complete_hh_detail(record, str(record.get("id"))):
        return None
    if metadata.get("version") != 1 or metadata.get("origin") not in ("search", "detail", "email"):
        return None
    if metadata.get("complete") is not True or not isinstance(metadata.get("received_at"), str):
        return None
    try:
        timestamp = datetime.fromisoformat(metadata["received_at"])
    except ValueError:
        return None
    return timestamp if timestamp.utcoffset() is not None else None
