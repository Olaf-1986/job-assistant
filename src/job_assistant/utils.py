from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .errors import sanitize_error

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "preferences.yaml"
LOGGER = logging.getLogger(__name__)


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
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        backup_path = path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex}")
        try:
            os.replace(path, backup_path)
            LOGGER.warning(sanitize_error("malformed_json_recovered: contents omitted"))
        except OSError:
            LOGGER.warning(sanitize_error("malformed_json_recovery_backup_failed: contents omitted"))
        return default


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return cleaned.rstrip("/").split("?utm_")[0]
    host = (parsed.hostname or "").lower()
    linkedin = re.search(r"/jobs/view/(\d+)", parsed.path)
    if linkedin and (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return f"https://www.linkedin.com/jobs/view/{linkedin.group(1)}"
    headhunter = re.search(r"/vacancy/(\d+)", parsed.path)
    is_headhunter = host in {"hh.ru", "headhunter.ge"} or host.endswith((".hh.ru", ".headhunter.ge"))
    if is_headhunter and parsed.path.rstrip("/") == "/applicant/vacancy_response":
        vacancy_ids = parse_qs(parsed.query).get("vacancyId", [])
        if vacancy_ids and vacancy_ids[0].isdigit():
            return f"https://{host}/vacancy/{vacancy_ids[0]}"
    if headhunter and is_headhunter:
        scheme = parsed.scheme or "https"
        return urlunsplit((scheme, parsed.netloc.lower(), f"/vacancy/{headhunter.group(1)}", "", ""))
    return cleaned.rstrip("/").split("?utm_")[0]


def slugify_text(value: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (value or "").lower(), flags=re.UNICODE).strip()
