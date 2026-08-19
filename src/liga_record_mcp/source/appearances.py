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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import SquadSourceError

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
