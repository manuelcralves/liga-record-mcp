"""Last season's scores, as this project reconstructed them.

Liga Record publishes no history: when a season ends its scores are gone, and
§10.2 says the marks behind them were never for discussion in the first place.
`scripts/build_last_season.py` rebuilds them from §10.3 and zerozero's match
pages, and this reads what it wrote.

The file is not authoritative and must never be presented as though it were.
Everything in it except one term is arithmetic on published facts — goals,
cards, clean sheets, wins, absences — but Record's own mark is estimated, and
`estimated` on every row says so. A reader that drops that distinction is
claiming Liga Record said something it did not.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from .base import SquadSourceError

#: The reconstructions, newest first. Each is optional: the project works
#: with one season, or none, and simply knows less.
ARCHIVES = ("last-season.json", "season-2024-25.json")


class LastSeasonError(SquadSourceError):
    """The reconstruction is missing, unreadable, or from another schema."""


def _fold(text: str) -> str:
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.split())


class LastSeasonSource:
    """Reads the reconstruction once, and again whenever it changes on disk.

    Cached on modification time rather than for the life of the process: a
    sweep finishing while the server is up should not need a restart to be
    seen, and a stale season quietly served for hours is worse than a reread.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._loaded: dict[str, Any] | None = None
        self._stamp: float | None = None

    @property
    def available(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise LastSeasonError(
                f"no reconstruction at {self.path} — run scripts/build_last_season.py"
            )
        stamp = self.path.stat().st_mtime
        if self._loaded is None or stamp != self._stamp:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LastSeasonError(f"could not read {self.path}: {exc}") from exc
            if "players" not in loaded:
                raise LastSeasonError(f"{self.path} is not a reconstruction")
            self._loaded, self._stamp = loaded, stamp
        return self._loaded

    def players(self) -> dict[str, dict[str, Any]]:
        return self.load()["players"]

    def find(self, name: str) -> list[dict[str, Any]]:
        """Every player whose name contains this, folded for accents.

        Returns all of them rather than picking. Two players can share a
        surname, and choosing between them belongs to whoever knows which one
        was meant.
        """
        needle = _fold(name)
        if not needle:
            return []
        return [
            player
            for player in self.players().values()
            if needle in _fold(player.get("name", ""))
        ]


def archive_records(folder: str | Path) -> dict[str, dict[str, Any]]:
    """Appearances, points-when-playing and the round-by-round scores.

    The shape `advice.valuation` wants, gathered across every reconstruction on
    disk. A season that has not been swept is simply absent; nothing here
    insists on having two.

    `points` is what he scored on the days he PLAYED, not his total: §10.3(i)'s
    -1 for the weeks he sat belongs to the other half of the estimate, and
    counting it twice would punish a rotation player for rotating.
    """
    folder = Path(folder)
    out: dict[str, dict[str, Any]] = {}
    for name in ARCHIVES:
        path = folder / name
        if not path.exists():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))["players"]
        except (OSError, ValueError, KeyError) as exc:
            raise LastSeasonError(f"could not read {path}: {exc}") from exc
        for player_id, player in loaded.items():
            entry = out.setdefault(
                player_id, {"played": 0, "points": 0.0, "available": 0, "each": []}
            )
            for match in player["matches"]:
                entry["available"] += 1
                if match.get("used"):
                    entry["played"] += 1
                    entry["points"] += float(match["points"])
                    entry["each"].append(float(match["points"]))
    return out
