"""Recoverable publication at existing paths, NOT an atomic multi-file transaction.

Prepared bytes survive every replacement. A durable journal is removed only after
all destinations have been replaced. Recovery rolls the whole set forward.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from .utils import read_json_strict, write_bytes, write_json

JOURNAL = ".publication.json"


class PublicationError(RuntimeError):
    pass


def _sync_directory(directory: Path) -> None:
    # Windows does not expose a portable directory fsync. File contents are synced
    # on both platforms; power-loss guarantees still depend on the filesystem.
    if os.name != "nt":
        try:
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as exc:
            if exc.errno in {errno.EINVAL, errno.ENOTSUP}:
                return
            raise
        try:
            try:
                os.fsync(descriptor)
            except OSError as exc:
                if exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
                    raise
        finally:
            os.close(descriptor)


@contextmanager
def _publication_lock(directory: Path):
    # OS locks are released on process death; a leftover lock file is harmless.
    try:
        handle = (directory / ".publication.lock").open("a+b")
    except OSError:
        raise PublicationError("Cannot open output publication lock; check local storage access.") from None
    with handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PublicationError("Output publication is busy; retry after the other writer finishes.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _relative_target(directory: Path, path: Path) -> str:
    relative = path.resolve().relative_to(directory.resolve())
    if not relative.parts or any(part.startswith(".publication") for part in relative.parts):
        raise ValueError("Invalid publication destination")
    return relative.as_posix()


def _recover_locked(directory: Path) -> bool:
    journal = directory / JOURNAL
    if not journal.exists():
        return False
    try:
        manifest = read_json_strict(journal)
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("Invalid journal")
        stage_name = manifest["stage"]
        if not isinstance(stage_name, str) or not stage_name.startswith(".publication-stage-"):
            raise ValueError("Invalid staging directory")
        if Path(stage_name).name != stage_name:
            raise ValueError("Invalid staging directory")
        stage = directory / stage_name
        if stage.is_symlink():
            raise ValueError("Invalid staging directory")
        entries = manifest["files"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("Invalid journal entries")
        prepared: dict[Path, bytes] = {}
        for index, entry in enumerate(entries):
            target = directory / entry["path"]
            if _relative_target(directory, target) != entry["path"] or target in prepared:
                raise ValueError("Invalid destination")
            staged_file = stage / str(index)
            if staged_file.is_symlink():
                raise ValueError("Invalid staged file")
            payload = staged_file.read_bytes()
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError("Invalid staged content")
            prepared[target] = payload
    except (OSError, ValueError, KeyError, TypeError):
        raise PublicationError(
            "Invalid output publication journal or staged files; retained unchanged. "
            "Restore the complete staging set before retrying; do not delete the journal."
        ) from None

    try:
        for target, payload in prepared.items():
            write_bytes(target, payload)
            _sync_directory(target.parent)
        journal.unlink()
        _sync_directory(directory)
    except OSError:
        raise PublicationError(
            "Incomplete output publication; files may belong to different runs. "
            "Prepared data and journal retained; rerun recover-output before using the reports."
        ) from None
    # If cleanup is interrupted, orphaned staging files are inert; no journal
    # means that all destination replacements have completed.
    shutil.rmtree(stage, ignore_errors=True)
    return True


def recover_publication(directory: Path) -> bool:
    """Finish a previously prepared publication before reading authoritative data."""
    if not (directory / JOURNAL).exists():
        return False
    with _publication_lock(directory):
        return _recover_locked(directory)


def publish_files(directory: Path, files: Mapping[Path, bytes]) -> None:
    """Stage the entire set before touching destinations; keep evidence on failure."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise PublicationError("Cannot create output publication directory; check local storage access.") from None
    with _publication_lock(directory):
        _recover_locked(directory)
        stage: Path | None = None
        try:
            destinations = [_relative_target(directory, path) for path in files]
            if not destinations or len(set(destinations)) != len(destinations):
                raise ValueError("Invalid publication destinations")
            stage = Path(tempfile.mkdtemp(prefix=".publication-stage-", dir=directory))
            entries = []
            for index, (relative, payload) in enumerate(zip(destinations, files.values(), strict=True)):
                write_bytes(stage / str(index), payload)
                entries.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest()})
            _sync_directory(stage)
            write_json(directory / JOURNAL, {"version": 1, "stage": stage.name, "files": entries})
            _sync_directory(directory)
        except (OSError, ValueError):
            # A failure after journal replacement must retain the prepared set.
            if stage is not None and not (directory / JOURNAL).exists():
                shutil.rmtree(stage, ignore_errors=True)
            raise PublicationError("Output preparation failed; retry after fixing local storage.") from None
        _recover_locked(directory)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")
