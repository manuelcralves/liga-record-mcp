"""A local record of who actually played, accumulated round by round.

Liga Record does not publish appearances, and no free external source covers
the current season. But §10.3 pays an unused player -1 a round, so the scoring
itself reveals it — and snapshotting that each week builds the history nobody
will sell us.

This is the one part of the project that writes. It writes a local JSON file
and nothing else; it never sends anything to liga.record.pt.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import SquadSourceError
from .scores import season_records

#: Bumped if the file layout ever changes, so an old file fails loudly.
FORMAT = 1


def empty_store() -> dict[str, Any]:
    return {"format": FORMAT, "rounds": {}}


def load_appearances(path: str | Path) -> dict[str, Any]:
    """Read the store, or return an empty one if it does not exist yet.

    A missing file is the normal state before the first recording, so it is not
    an error. A corrupt one is.
    """
    file = Path(path)
    if not file.is_file():
        return empty_store()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SquadSourceError(f"{file} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or "rounds" not in raw:
        raise SquadSourceError(f"{file} is not an appearance store")
    if raw.get("format") != FORMAT:
        raise SquadSourceError(
            f"{file} is format {raw.get('format')}, this build expects {FORMAT}"
        )
    return raw


def save_appearances(path: str | Path, store: dict[str, Any]) -> Path:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    return file


def record_round(
    store: dict[str, Any],
    round_number: int,
    statuses: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add or replace one round's worth of appearances.

    Keyed by round number, so recording the same round twice overwrites rather
    than double-counting — the tool is safe to run repeatedly.
    """
    stamped = (now or datetime.now(timezone.utc)).isoformat()
    updated = {**store, "rounds": {**store.get("rounds", {})}}
    updated["rounds"][str(round_number)] = {
        "recorded_at": stamped,
        "players": dict(statuses),
    }
    return updated


def history_for(store: dict[str, Any], player_id: str) -> dict[str, str]:
    """One player's status in every recorded round, keyed by round."""
    out: dict[str, str] = {}
    for rnd, entry in (store.get("rounds") or {}).items():
        status = (entry.get("players") or {}).get(player_id)
        if status is not None:
            out[rnd] = status
    return out


def recorded_rounds(store: dict[str, Any]) -> list[int]:
    return sorted(int(r) for r in (store.get("rounds") or {}))


def current_records(
    market: Mapping[str, Any], rounds: Mapping[int, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """This season's appearances and points-when-playing, from the emails alone.

    The shape `advice.valuation` wants, for the rounds already scored. It is
    the emails' half of `source.season.season_so_far`, which is the reading the
    ledger, the pages, the squad proposal and the server take: that adds the
    rounds 1-5 rebuilt from zerozero, where the file exists.

    UNTIL 15 SEPTEMBER 2026 this was read off the site: the latest round from
    `points_round`, everything before it as `points_total - points_round`, and
    the round-2 statuses `record_round` had written down. Then the site put
    every total back to zero for the official phase, and this began to see
    Pavlidis on nine points in six matches. The season now comes from the
    weekly score emails, round by round, through `scores.season_records`. The
    trial rounds, 1 to 5, went with the totals: they were filed for the squad
    only, and nothing Record paid for them survives — what the model has of
    them is the rebuild.
    """
    return season_records(market, rounds)
