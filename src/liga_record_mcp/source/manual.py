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

from ..models import (
    COACH_COUNT,
    Coach,
    Player,
    Selection,
    Squad,
    SquadSnapshot,
    Violation,
)
from ..rules import validate_selection, validate_squad
from .base import SquadSourceError


def load_coaches(path: str | Path) -> list[Coach]:
    """The 18 selectable coaches, from a hand-maintained YAML file.

    Hand-maintained for the same reason the squad is: the list only renders for
    a signed-in user, and the endpoint that touches it (`team_manager.ashx`)
    sets a coach rather than listing them. It changes when a club changes
    manager, which is rare.
    """
    file = Path(path)
    if not file.is_file():
        raise SquadSourceError(f"no coach file at {file}")
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SquadSourceError(f"{file} is not valid YAML: {exc}") from exc

    entries = (raw or {}).get("coaches") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise SquadSourceError(f"{file} is missing a `coaches` list")

    try:
        coaches = [Coach(**entry) for entry in entries]
    except (TypeError, ValidationError) as exc:
        raise SquadSourceError(f"{file} has a bad coach entry: {exc}") from exc

    repeated = sorted({c.id for c in coaches if [x.id for x in coaches].count(c.id) > 1})
    if repeated:
        raise SquadSourceError(
            f"{file} has duplicate coach ids: {', '.join(repeated)}"
        )
    if len(coaches) != COACH_COUNT:
        raise SquadSourceError(
            f"{file} lists {len(coaches)} coaches; §6.15 expects {COACH_COUNT}, "
            "one per club — a short list usually means a partial copy"
        )
    return coaches


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


def load_final_entry(path: str | Path) -> dict:
    """The Final Table entry as submitted, and every chip played since.

    Hand-maintained, like the squad and the coaches, and for a stronger reason:
    zerozero is not read by this project at all. The entry exists only where
    Manuel typed it, so the file IS the record of it.

    Returns `entry` (None until the lock), `locked_round`, and `chips` in the
    order they were played. A missing file is not an error — it means the entry
    has not been written yet, which is the state for most of a season's start.

    The chips are validated here rather than where they are applied: a chip
    naming a club that is not in the entry, or a place outside it, is a typo
    that would silently do nothing at the far end.
    """
    file = Path(path)
    if not file.is_file():
        return {"entry": None, "locked_round": None, "chips": []}
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SquadSourceError(f"{file} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SquadSourceError(f"{file} is not a mapping")

    entry = raw.get("entrada")
    if entry is not None:
        if not isinstance(entry, list) or not all(isinstance(c, str) for c in entry):
            raise SquadSourceError(f"{file}: `entrada` must be a list of club names")
        if len(set(entry)) != len(entry):
            raise SquadSourceError(f"{file}: `entrada` repeats a club")

    chips = raw.get("chips") or []
    if not isinstance(chips, list):
        raise SquadSourceError(f"{file}: `chips` must be a list")
    for chip in chips:
        if not isinstance(chip, dict):
            raise SquadSourceError(f"{file}: every chip must be a mapping")
        club, to = chip.get("clube"), chip.get("para")
        if club is None or to is None:
            raise SquadSourceError(f"{file}: a chip is missing `clube` or `para`")
        if entry is not None and club not in entry:
            raise SquadSourceError(f"{file}: chip names {club!r}, not in `entrada`")
        if entry is not None and not 1 <= int(to) <= len(entry):
            raise SquadSourceError(f"{file}: chip sends {club!r} to place {to}")

    return {
        "entry": entry,
        "locked_round": raw.get("jornada_entrada"),
        "chips": chips,
    }


def load_unavailable(path: str | Path, round_number: int) -> dict[str, str]:
    """Who cannot play a given round, and why — hand-maintained.

    THE ONE THING MANUEL KNOWS THAT THE MODEL DOES NOT. Cards are counted, so
    suspensions are seen without help; injuries are not published by the site —
    a player's payload carries fifteen fields and none is availability — and
    this project does not read the press.

    Measured worth, playing a full season out from matchday 6 with his squad:
    picking the XI blind scores 1246, knowing who is out scores 1306, and adding
    transfers on top scores 1326. Knowing who is injured is three times the
    whole transfer channel.

    THE ROUND IS CHECKED, not assumed. A file left over from last week would
    bench a fit player, and the mistake is invisible — he simply is not picked.
    A file naming a different round returns nothing rather than the wrong
    thing, because a stale answer here is worse than no answer.
    """
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SquadSourceError(f"{file} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SquadSourceError(f"{file} is not a mapping")

    named = raw.get("jornada")
    if named is not None and int(named) != int(round_number):
        return {}

    out: dict[str, str] = {}
    for entry in raw.get("fora") or ():
        if not isinstance(entry, dict):
            raise SquadSourceError(f"{file}: every entry under `fora` is a mapping")
        player_id = entry.get("id")
        if player_id is None:
            raise SquadSourceError(f"{file}: an entry under `fora` has no `id`")
        out[str(player_id)] = str(entry.get("razao") or "sem razão dada")
    return out
