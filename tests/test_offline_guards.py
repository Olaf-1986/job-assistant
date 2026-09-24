from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import Mock

import dotenv
import pytest

from tests import conftest


@pytest.fixture
def guarded_dotenv(monkeypatch, tmp_path):
    project_env = tmp_path / "project" / ".env"
    project_env.parent.mkdir()
    monkeypatch.setattr(conftest, "PROJECT_ENV", project_env)
    loader = Mock(return_value=True)
    reader = Mock(return_value={"SYNTHETIC": "fixture"})
    monkeypatch.setattr(dotenv, "load_dotenv", loader)
    monkeypatch.setattr(dotenv, "dotenv_values", reader)
    cleanups = []
    conftest.pytest_configure(SimpleNamespace(add_cleanup=cleanups.append))
    try:
        yield SimpleNamespace(project_env=project_env, loader=loader, reader=reader)
    finally:
        for cleanup in reversed(cleanups):
            cleanup()


@pytest.mark.parametrize("method", ["load_dotenv", "dotenv_values"])
@pytest.mark.parametrize(
    "path_kind",
    ["regular", "relative", "symlink", "absolute_symlink", "target", "alias", "dangling", "implicit"],
)
def test_dotenv_guards_block_project_file_without_reading(guarded_dotenv, monkeypatch, tmp_path, method, path_kind):
    project_env = guarded_dotenv.project_env
    requested = project_env
    if path_kind in {"regular", "relative", "implicit"}:
        project_env.touch()
        if path_kind == "relative":
            monkeypatch.chdir(project_env.parent)
            requested = ".env"
        elif path_kind == "implicit":
            requested = None
    else:
        target = project_env.parent / "synthetic-target"
        if path_kind != "dangling":
            target.touch()
        project_env.symlink_to(target if path_kind == "absolute_symlink" else target.name)
        if path_kind == "target":
            requested = target
        elif path_kind == "alias":
            requested = tmp_path / "alias"
            requested.symlink_to(project_env)

    if method == "load_dotenv":
        assert dotenv.load_dotenv(requested) is False
    else:
        with pytest.raises(AssertionError, match="synthetic dotenv fixture"):
            dotenv.dotenv_values(requested)

    guarded_dotenv.loader.assert_not_called()
    guarded_dotenv.reader.assert_not_called()


@pytest.mark.parametrize("method", ["load_dotenv", "dotenv_values"])
@pytest.mark.parametrize("symlink", [False, True])
def test_dotenv_guards_allow_unrelated_fixtures(guarded_dotenv, tmp_path, method, symlink):
    # A linked project .env must not prevent reading an unrelated synthetic fixture.
    project_target = guarded_dotenv.project_env.parent / "synthetic-target"
    project_target.touch()
    guarded_dotenv.project_env.symlink_to(project_target.name)
    fixture_path = tmp_path / "fixture.env"
    fixture_path.touch()
    if symlink:
        alias = tmp_path / "fixture-alias"
        alias.symlink_to(fixture_path.name)
        fixture_path = alias

    result = getattr(dotenv, method)(dotenv_path=fixture_path, encoding="utf-8")

    underlying = guarded_dotenv.loader if method == "load_dotenv" else guarded_dotenv.reader
    assert result == underlying.return_value
    underlying.assert_called_once_with(fixture_path, encoding="utf-8")


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_offline_guard_blocks_socket_connections_without_opening_socket(method):
    with pytest.raises(AssertionError, match="mock socket connections"):
        getattr(socket.socket, method)(None, ("example.invalid", 443))


def test_offline_guard_blocks_dns_resolution():
    with pytest.raises(AssertionError, match="mock DNS resolution"):
        socket.getaddrinfo("example.invalid", 443)
