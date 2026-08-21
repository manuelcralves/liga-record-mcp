"""A record of what was decided each round, and what it was worth.

The projection ledger already stores what was expected of each PLAYER and what
he did. This stores the other half: what the manager did with that — the score
his team actually returned, the transfer he made, whether it was the one the
model suggested, and whether a holiday round was spent.

WHY BOTH HALVES. A model that projects well and is ignored is worth nothing,
and a model that is followed while projecting badly is worse than nothing. Only
the pair says which is happening. Kept round by round, it also answers the
question that decides whether any of this was worth building: over a season,
did taking the advice beat not taking it?

WHAT IT IS NOT. Not a second copy of the squad, and not a scraper. Most of what
goes in here can be read from the site — a team's round score and rank, and the
`OnHoliday` flag the ranking exposes — and the rest is what the participant
himself did and only he knows.

It writes a local JSON file and nothing else. Like every other writer in this
project, it never sends anything to liga.record.pt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import SquadSourceError

#: Bumped if the layout ever changes, so an old file fails loudly instead of
#: being half-read.
FORMAT = 1


def empty_store() -> dict[str, Any]:
    return {"format": FORMAT, "rounds": {}, "season": {}}


def load_decisions(path: str | Path) -> dict[str, Any]:
    """Read the ledger, or return an empty one if it does not exist yet.

    A missing file is the normal state before the first round, so it is not an
    error. A corrupt or foreign one is.
    """
    file = Path(path)
    if not file.is_file():
        return empty_store()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SquadSourceError(f"{file} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or "rounds" not in raw:
        raise SquadSourceError(f"{file} is not a decision ledger")
    if raw.get("format") != FORMAT:
        raise SquadSourceError(
            f"{file} is format {raw.get('format')}, this code writes {FORMAT}"
        )
    raw.setdefault("season", {})
    return raw


def save_decisions(path: str | Path, store: dict[str, Any]) -> Path:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(store, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return file


def record_decision(
    store: dict[str, Any],
    round_number: int,
    *,
    our_points: int | None = None,
    our_rank: int | None = None,
    field_size: int | None = None,
    transfer_out: str | None = None,
    transfer_in: str | None = None,
    suggested_out: str | None = None,
    suggested_in: str | None = None,
    followed: bool | None = None,
    holiday: bool = False,
    captain: str | None = None,
    coach: str | None = None,
    note: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write one round's decisions into the ledger.

    Refuses to overwrite a round already on file unless asked. A round's
    decisions are a matter of record and quietly rewriting one turns the ledger
    into a story rather than evidence — the same reason the projection ledger
    refuses to snapshot a round twice.

    `followed` is worked out from the transfer and the suggestion when both are
    known, and can still be given explicitly for the case they do not cover:
    making no transfer when none was suggested is following the advice, and
    making none when one was is not.
    """
    key = str(int(round_number))
    if key in store["rounds"] and not overwrite:
        raise SquadSourceError(
            f"round {key} is already recorded — pass overwrite to replace it"
        )

    if followed is None and (suggested_out or suggested_in):
        followed = (transfer_out, transfer_in) == (suggested_out, suggested_in)

    store["rounds"][key] = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "our_points": our_points,
        "our_rank": our_rank,
        "field_size": field_size,
        "transfer": (
            {"out": transfer_out, "in": transfer_in}
            if transfer_out or transfer_in
            else None
        ),
        "suggested": (
            {"out": suggested_out, "in": suggested_in}
            if suggested_out or suggested_in
            else None
        ),
        "followed": followed,
        "holiday": bool(holiday),
        "captain": captain,
        "coach": coach,
        "note": note,
    }
    return store["rounds"][key]


def record_season_choice(
    store: dict[str, Any], *, top_scorer: str | None = None, **extra: Any
) -> dict[str, Any]:
    """Choices made once for the whole season, not round by round.

    §10.3(m)'s bet on the leading scorer is the one that matters: it is made
    when the team is first submitted, never again, and it is worth up to twenty
    points that nobody notices until May.
    """
    if top_scorer is not None:
        store["season"]["top_scorer"] = top_scorer
    store["season"].update(extra)
    store["season"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    return store["season"]


def recorded_rounds(store: dict[str, Any]) -> list[int]:
    return sorted(int(r) for r in (store.get("rounds") or {}))


def holidays_used(store: dict[str, Any]) -> list[int]:
    """Which rounds a holiday was taken in. §6.17 allows three."""
    return [
        int(rnd)
        for rnd, entry in (store.get("rounds") or {}).items()
        if entry.get("holiday")
    ]


def track_record(store: dict[str, Any]) -> dict[str, Any]:
    """How the advice has done, as far as the ledger goes.

    Deliberately plain arithmetic over what is written down. Anything cleverer
    would need more rounds than exist, and a confident summary of four rounds
    would be worse than none.
    """
    rounds = store.get("rounds") or {}
    scored = [e["our_points"] for e in rounds.values() if e.get("our_points") is not None]
    advised = [e for e in rounds.values() if e.get("suggested")]
    taken = [e for e in advised if e.get("followed")]
    ranks = [
        (e["our_rank"], e["field_size"])
        for e in rounds.values()
        if e.get("our_rank") and e.get("field_size")
    ]
    return {
        "rounds": len(rounds),
        "scored": len(scored),
        "points": sum(scored) if scored else None,
        "per_round": round(sum(scored) / len(scored), 1) if scored else None,
        "best": max(scored) if scored else None,
        "worst": min(scored) if scored else None,
        "advice_given": len(advised),
        "advice_taken": len(taken),
        "holidays_used": holidays_used(store),
        "holidays_left": 3 - len(holidays_used(store)),
        # Lower is better: 12.0 means a round finished in the top twelve per
        # cent of the country. Guarded against a rank larger than the field,
        # which is not a good round but a bad reading of the site.
        "best_percentile": (
            round(100 * min(r / n for r, n in ranks if 0 < r <= n), 1)
            if any(0 < r <= n for r, n in ranks)
            else None
        ),
        "top_scorer_bet": (store.get("season") or {}).get("top_scorer"),
    }
