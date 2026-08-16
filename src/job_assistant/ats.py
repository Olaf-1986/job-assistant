from __future__ import annotations

from urllib.parse import urlparse


def discover_greenhouse_board_token(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    if "greenhouse.io" not in host:
        return None
    if host.startswith("boards.greenhouse.io") and parts:
        return parts[0]
    if host.startswith("job-boards.greenhouse.io") and len(parts) >= 2 and parts[0] == "embed":
        return parts[1]
    return None


def discover_lever_site(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host != "jobs.lever.co" or not parts:
        return None
    region = "eu" if any(part.lower() == "eu" for part in parts[1:]) else "global"
    return parts[0], region
