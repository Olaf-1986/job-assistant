from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "preferences.yaml"


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def lower_text(*parts: object) -> str:
    return "\n".join(str(part or "") for part in parts).lower()


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    return cleaned.rstrip("/").split("?utm_")[0]


def slugify_text(value: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (value or "").lower(), flags=re.UNICODE).strip()
