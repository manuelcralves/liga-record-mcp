"""A squad read from a hand-maintained YAML file.

This is the source that makes everything above it buildable before any scraper
exists, and it stays a usable fallback afterwards: 23 players, edited when you
make a transfer.

The file is checked against the regulation on every read. A hand-maintained
file drifts, and the cost of silent drift here is high — a wrong squad does not
announce itself, it just produces confident bad advice. So a bad file raises
instead of loading.

Freshness comes from the file's modification time unless the document declares
an `updated` date, so there is no second timestamp to keep in sync by hand.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..models import Player, Selection, Squad, SquadSnapshot, Violation
from ..rules import validate_selection, validate_squad
from .base import SquadSourceError


def _describe(violations: list[Violation]) -> str:
    return "; ".join(f"{v.rule} {v.detail}" for v in violations)


def _as_datetime(value: Any) -> datetime:
    """Coerce a YAML date or timestamp into an aware UTC datetime."""
    # datetime subclasses date, so it has to be tested first.
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise SquadSourceError(f"`updated` should be a date or timestamp, got {value!r}")


class ManualSquadSource:
    """Reads a squad from a YAML file on disk."""

    name = "manual"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> SquadSnapshot:
        raw = self._read()
        squad = self._build_squad(raw)

        composition = validate_squad(squad)
        if not composition.is_valid:
            raise SquadSourceError(
                f"{self.path} is not a legal squad: {_describe(composition.violations)}"
            )

        selection = self._build_selection(raw)
        if selection is not None:
            sheet = validate_selection(squad, selection)
            if not sheet.is_valid:
                raise SquadSourceError(
                    f"{self.path} has an illegal selection: {_describe(sheet.violations)}"
                )

        return SquadSnapshot(
            squad=squad,
            selection=selection,
            round_number=raw.get("round"),
            fetched_at=self._fetched_at(raw),
            source=self.name,
        )

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise SquadSourceError(f"no squad file at {self.path}")
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SquadSourceError(f"{self.path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise SquadSourceError(
                f"{self.path} should hold a mapping at the top level, "
                f"found {type(raw).__name__}"
            )
        return raw

    def _build_squad(self, raw: dict[str, Any]) -> Squad:
        team = raw.get("team")
        if not isinstance(team, dict):
            raise SquadSourceError(f"{self.path} is missing a `team` mapping")

        entries = raw.get("players")
        if not isinstance(entries, list):
            raise SquadSourceError(f"{self.path} is missing a `players` list")

        try:
            players = tuple(Player(**entry) for entry in entries)
        except (TypeError, ValidationError) as exc:
            raise SquadSourceError(
                f"{self.path} has a bad player entry: {exc}"
            ) from exc

        try:
            return Squad(
                team_id=team["id"],
                team_name=team["name"],
                players=players,
                bonus=team.get("bonus", 0),
                penalties=team.get("penalties", 0),
            )
        except (KeyError, ValidationError) as exc:
            raise SquadSourceError(
                f"{self.path} has a bad `team` block: {exc}"
            ) from exc

    def _build_selection(self, raw: dict[str, Any]) -> Selection | None:
        block = raw.get("selection")
        if block is None:
            return None
        if not isinstance(block, dict):
            raise SquadSourceError(
                f"{self.path} has a `selection` that is not a mapping"
            )
        try:
            return Selection(
                starters=tuple(block.get("starters") or ()),
                bench=tuple(block.get("bench") or ()),
                captain=block.get("captain"),
                coach_id=block.get("coach"),
            )
        except ValidationError as exc:
            raise SquadSourceError(
                f"{self.path} has a bad `selection` block: {exc}"
            ) from exc

    def _fetched_at(self, raw: dict[str, Any]) -> datetime:
        declared = raw.get("updated")
        if declared is not None:
            return _as_datetime(declared)
        return datetime.fromtimestamp(self.path.stat().st_mtime, tz=timezone.utc)
