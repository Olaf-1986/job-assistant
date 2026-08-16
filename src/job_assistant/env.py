from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from .utils import ROOT


def load_project_env(root: Path = ROOT) -> bool:
    """Load project-root .env without replacing exported shell values."""
    env_path = root / ".env"
    if not env_path.exists():
        return False
    return load_dotenv(env_path, override=False)
