from __future__ import annotations

import os
import socket
from pathlib import Path

import dotenv
import pytest

PROJECT_ENV = Path(__file__).resolve().parents[1] / ".env"


def pytest_configure(config):
    """Isolate collection as well as tests from local credentials and live services."""
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    load_dotenv = dotenv.load_dotenv
    dotenv_values = dotenv.dotenv_values

    def fixture_load_dotenv(dotenv_path=None, *args, **kwargs):
        if dotenv_path is None or Path(dotenv_path).resolve() == PROJECT_ENV.resolve():
            return False
        return load_dotenv(dotenv_path, *args, **kwargs)

    def fixture_dotenv_values(dotenv_path=None, *args, **kwargs):
        if dotenv_path is None or Path(dotenv_path).resolve() == PROJECT_ENV.resolve():
            raise AssertionError("Tests must use a synthetic dotenv fixture")
        return dotenv_values(dotenv_path, *args, **kwargs)

    def offline_connect(sock, address):
        raise AssertionError("Tests must mock socket connections")

    def offline_resolve(*args, **kwargs):
        raise AssertionError("Tests must mock DNS resolution")

    patch.setattr(dotenv, "load_dotenv", fixture_load_dotenv)
    patch.setattr(dotenv, "dotenv_values", fixture_dotenv_values)
    patch.setattr(socket.socket, "connect", offline_connect)
    patch.setattr(socket.socket, "connect_ex", offline_connect)
    patch.setattr(socket, "getaddrinfo", offline_resolve)
    for name in tuple(os.environ):
        if name.startswith(("HH_", "EMAIL_IMAP_", "TELEGRAM_", "JOOBLE_")):
            patch.delenv(name)
