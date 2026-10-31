"""Strict, non-destructive reads at authoritative persistence boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import RawRecord
from .utils import read_json_strict


class StoreError(ValueError):
    pass


LABELS = {"headhunter": "HeadHunter raw store", "manual": "LinkedIn manual store", "telegram": "Telegram raw store"}


def read_raw_store(path: Path, kind: str) -> list[RawRecord]:
    try:
        data = read_json_strict(path)
    except FileNotFoundError:
        if path.is_symlink():
            raise StoreError(f"Unreadable {LABELS[kind]}; file retained unchanged.") from None
        return []
    except (OSError, ValueError):
        raise StoreError(f"Unreadable {LABELS[kind]}; file retained unchanged. Repair it before retrying.") from None
    return validate_raw_records(data, kind)


def validate_raw_records(data: Any, kind: str) -> list[RawRecord]:
    label = LABELS[kind]
    if not isinstance(data, list):
        raise StoreError(f"Invalid {label}: expected a list; file retained unchanged.")
    for item in data:
        if not isinstance(item, dict):
            raise StoreError(f"Invalid {label}: expected record objects; file retained unchanged.")
        record = item.get("record", item)
        if not isinstance(record, dict) or not record:
            raise StoreError(f"Invalid {label}: malformed record; file retained unchanged.")
        source = record.get("__source")
        text_fields = {
            "headhunter": ("name", "description", "alternate_url", "apply_alternate_url"),
            "telegram": ("title", "raw_text", "message_url", "apply_url"),
            "manual": (
                "page_url",
                "url",
                "canonical_url",
                "visible_text",
                "selected_text",
                "text",
                "source_label",
                "manual_source",
            ),
        }[kind]
        if any(record.get(key) is not None and not isinstance(record[key], str) for key in text_fields):
            raise StoreError(f"Invalid {label}: malformed field type; file retained unchanged.")
        if kind == "manual":
            declared = {
                record.get(key) for key in ("source_label", "manual_source") if isinstance(record.get(key), str)
            }
            allowed = source is None or source in ("linkedin", "other")
            allowed = allowed or (source in ("manual", "manual_capture") and declared in ({"linkedin"}, {"other"}))
            allowed = allowed and (not declared or declared <= {"linkedin", "other"}) and len(declared) <= 1
            if source in ("linkedin", "other") and declared and declared != {source}:
                allowed = False
        else:
            allowed = source is None or source == kind
        wrapper_source = item.get("__source") if "record" in item else None
        expected_source = source or (next(iter(declared), "linkedin") if kind == "manual" else kind)
        if not allowed or (wrapper_source is not None and wrapper_source != expected_source):
            raise StoreError(f"Conflicting source at {label}; file retained unchanged.")
        if kind == "headhunter":
            valid = _text(record.get("name"))
            valid = valid and (record.get("id") is None or _identity(record["id"]))
        elif kind == "telegram":
            valid = (
                _text(record.get("title"))
                and _integer_identity(record.get("channel_id"))
                and _integer_identity(record.get("message_id"))
                and type(record.get("vacancy_index", 0)) is int
            )
        else:
            valid = any(_text(record.get(key)) for key in ("page_url", "url", "canonical_url"))
        if not valid:
            raise StoreError(f"Invalid {label}: missing record identity or title; file retained unchanged.")
    return data


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identity(value: Any) -> bool:
    return type(value) is int or _text(value)


def _integer_identity(value: Any) -> bool:
    return type(value) is int or (isinstance(value, str) and value.lstrip("-").isdigit())
