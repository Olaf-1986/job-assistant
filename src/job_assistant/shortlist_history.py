"""Comparison evidence for delivered shortlist cards. Only final exports advance it."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, TypeAdapter, ValidationError

from .config import Preferences
from .deduplicate import (
    MIN_REPOST_WORDS,
    repost_descriptions_match,
    repost_group,
    url_identity_keys,
    vacancy_identity_keys,
)
from .models import NormalizedVacancy
from .paths import output_paths
from .publication import PublicationError, json_bytes, recover_publication
from .raw_store import StoreError
from .recovered_state import read_recovered_state
from .utils import read_json_strict, slugify_text

# A qf stage may invoke nested captures or async Telegram imports. Keep this
# scoped to the invoking task, including exception paths and nested batches.
_deferred = ContextVar("defer_shortlist_publication", default=False)


@contextmanager
def defer_shortlist_publication():
    token = _deferred.set(True)
    try:
        yield
    finally:
        _deferred.reset(token)


def shortlist_publication_deferred() -> bool:
    return _deferred.get()


IdentityKey = Annotated[str, Field(pattern=r"^(url|id|company-title-location|description):.+$")]
Nonempty = Annotated[str, Field(min_length=1)]
HISTORY_MARKER = "<!-- job-assistant shortlist-history v1 -->"


class ShortlistHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    keys: set[IdentityKey]
    # Normalized company/title -> substantive descriptions for the shared repost test.
    reposts: dict[Nonempty, set[Nonempty]]
    _original_digest: str | None = PrivateAttr(default=None)

    def mark(self, vacancies: list[NormalizedVacancy]) -> None:
        for vacancy in vacancies:
            keys = vacancy_identity_keys(vacancy)
            group = repost_group(vacancy)
            descriptions = self.reposts.get(group, ())
            seen = not self.keys.isdisjoint(keys)
            if not seen and descriptions:
                description = slugify_text(vacancy.description_text)
                seen = any(repost_descriptions_match(description, old) for old in descriptions)
            vacancy.previously_exported = seen
            if seen:
                # Retain verified aliases even if the old source later disappears.
                self.remember(vacancy)

    def remember(self, vacancy: NormalizedVacancy) -> None:
        keys = vacancy_identity_keys(vacancy)
        if not keys:
            raise StoreError("Shortlist card lacks a verifiable vacancy identity. Publication stopped.")
        self.keys.update(keys)
        group = repost_group(vacancy)
        if group:
            description = slugify_text(vacancy.description_text)
            if len(description.split()) >= MIN_REPOST_WORDS:
                self.reposts.setdefault(group, set()).add(description)

    def payload(self) -> bytes:
        return json_bytes(
            {
                "schema_version": self.schema_version,
                "keys": sorted(self.keys),
                "reposts": {group: sorted(values) for group, values in sorted(self.reposts.items())},
            }
        )

    @property
    def original_digest(self) -> str | None:
        return self._original_digest


def _snapshot_paths(paths: dict[str, Path]) -> set[Path]:
    directory = paths["dir"]
    reports = {paths["combined_shortlist"], paths["deduplicated_shortlist"]}
    final_name = re.compile(
        r"(?:(?:combined_shortlist|deduplicated_shortlist|shortlist)"
        r"(?:[._-](?:before-filter-)?\d{4}[-_]?\d{2}[-_]?\d{2}(?:T\d{6}Z)?)?|shortlist_extra_\d+)\.md"
    )
    for path in directory.rglob("*.md"):
        if final_name.fullmatch(path.name) and not any(
            part.startswith(".") or "preview" in part.casefold() for part in path.relative_to(directory).parts
        ):
            reports.add(path)
    return reports


def _seed_snapshots(history: ShortlistHistory, paths: dict[str, Path]) -> None:
    for path in sorted(_snapshot_paths(paths)):
        if path.is_symlink():
            raise StoreError("Cannot verify a linked shortlist snapshot. Restore the report before publishing.")
        if not path.exists():
            continue
        report = path.read_text(encoding="utf-8")
        if (
            path in (paths["combined_shortlist"], paths["deduplicated_shortlist"])
            and HISTORY_MARKER in report.splitlines()
        ):
            raise StoreError("Published shortlist history is missing. Restore it before publishing.")
        cards = re.split(r"^## \d+\. ", report, flags=re.MULTILINE)[1:]
        for card in cards:
            match = re.search(r"^- (?:Vacancy|Application) URL: (https?://\S+)\s*$", card, re.MULTILINE)
            if match:
                history.keys.update(url_identity_keys(match[1]))

    # The old baseline contained the unfiltered top N. Only a corroborated
    # identity may contribute aliases or a description for matching reposts.
    baseline = paths["previous_combined_shortlist"]
    if baseline.exists() or baseline.is_symlink():
        try:
            identities = TypeAdapter(list[NormalizedVacancy]).validate_python(read_json_strict(baseline))
        except (OSError, ValueError):
            raise StoreError("Invalid previous-combined baseline. Repair it before initializing history.") from None
        history.mark(identities)


def load_shortlist_history(preferences: Preferences) -> ShortlistHistory:
    paths = output_paths(preferences)
    recover_publication(paths["dir"])
    path = paths["shortlist_history"]
    destinations = [*paths.values(), paths["dir"] / preferences.outputs.source_status_file]
    if len({dest.resolve() for dest in destinations}) != len(destinations):
        raise PublicationError("Output publication paths must be distinct. Check the output configuration.")

    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        if path.is_symlink():
            raise StoreError("Unreadable shortlist history. Restore it before publishing.") from None
        payload = None
    except OSError:
        raise StoreError("Unreadable shortlist history. Restore it before publishing.") from None
    if payload is None:
        history = ShortlistHistory(schema_version=1, keys=set(), reposts={})
    else:
        try:
            history = ShortlistHistory.model_validate_json(payload)
        except ValidationError:
            raise StoreError("Invalid shortlist history. Restore it before publishing.") from None
        history._original_digest = hashlib.sha256(payload).hexdigest()
    recovered = read_recovered_state(preferences)
    if recovered:
        for entry in recovered.history:
            history.remember(entry.identity)
    if payload is None:
        _seed_snapshots(history, paths)
    return history
