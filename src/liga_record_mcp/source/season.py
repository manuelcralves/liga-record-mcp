"""This season, as the valuation reads it: rounds 1-5 rebuilt, the emails from 6.

WHY THE REBUILD. On 15 September 2026 the site put every total back to zero,
and the weekly emails — the only record of what Record paid — begin at
matchday 6. Rounds 1-5 went with the reset, and two rounds say little about who
plays. The replay of 15/09 (`scripts/replay_valuation.py`) measured rounds 1-5
worth +0.013 and +0.023 of correlation in matchdays 7-12, and +0.006 over 7-34,
once the chance of playing leans on the recent rounds.

So they are rebuilt the way the archive is: zerozero's match lists, scored by
`stats.reconstruct_points`, in `data/season-2026-27.json` — never committed,
not ours to redistribute. Only Record's mark is estimated, from zerozero's
rating, and the file was checked against the emails of rounds 6-7, where both
exist, before it was let in: `scripts/measure_early_rounds.py`.

WHICH ROUNDS COUNT FOR A MAN, one rule for everyone:

  * his club played that round, read from the rebuild itself, whose rows cover
    every club on the market;
  * and he was on the market on 19 August — the round-2 record in
    `appearances.json` — or his club had already called him up: a zerozero
    row, on the pitch or on the bench, that round or before.

A round counts as played if he took the field. A signing who arrived late
starts counting at his first call-up. The two round-3 matches played after
round 4 began count with what he did: §15.3 scored them at nothing in the game,
but the football happened, and it says who plays.

WITHOUT THE FILE, NOTHING CHANGES. The job on GitHub and a fresh clone read the
emails alone, as everything did before, and `evidence` says so.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..models import FIRST_SCORING_MATCHDAY
from .appearances import current_records, load_appearances
from .base import SquadSourceError
from .scores import load_official_rounds
from .zerozero import clubs_agree

#: The rebuild of this season. Named for the season because it exists for one
#: reason — the reset of 15/09/2026 — and a reset next year would be its own.
REBUILT_FILE = "season-2026-27.json"

#: The round `appearances.json` recorded on 19 August: who was on the market then.
MARKET_AT_START_ROUND = "2"


def load_rebuilt(folder: str | Path) -> dict[str, Any] | None:
    """The rebuilt players of this season, by market id, or None without the file."""
    path = Path(folder) / REBUILT_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["players"]
    except (OSError, ValueError, KeyError) as exc:
        raise SquadSourceError(f"could not read {path}: {exc}") from exc


def market_at_start(folder: str | Path) -> set[str]:
    """Who was on the market on 19 August, from the round-2 appearance record."""
    store = load_appearances(Path(folder) / "appearances.json")
    recorded = store["rounds"].get(MARKET_AT_START_ROUND) or {}
    return set(recorded.get("players") or {})


def _club_for(real: Sequence[Mapping[str, Any]], number: int) -> str | None:
    """His club for a round he has no row in: the last one before it, else the next."""
    before = [m for m in real if int(m["round"]) <= number]
    if before:
        return before[-1]["club"]
    return real[0]["club"] if real else None


def early_records(
    rebuilt: Mapping[str, Mapping[str, Any]],
    market: Mapping[str, Any],
    *,
    at_start: Collection[str],
    last_round: int = FIRST_SCORING_MATCHDAY - 1,
) -> dict[str, dict[str, Any]]:
    """Rounds 1 to `last_round`, per market player, in the emails' shape.

    `played`, `points` (scored on the days he played), `available` and
    `rounds`, which maps each round that counts to whether he played. A row
    `fill_absent_rounds` wrote for a week he was not in the squad is not a
    call-up; it is read as the rule reads any round without one.

    ONLY FOR A MAN THE REBUILD COVERS. One zerozero could not link, or whose
    page could not be read, is not in the file at all — and read as a man
    never called up, he would get five rounds on the bench he may have spent
    on the pitch. He gets nothing from here, and the emails alone, as before.
    A man linked with no match this season is in the file, with no rows.
    """
    clubs_in: dict[int, set[str]] = {}
    for entry in rebuilt.values():
        for match in entry.get("matches") or ():
            if not match.get("absent"):
                clubs_in.setdefault(int(match["round"]), set()).add(match["club"])

    out: dict[str, dict[str, Any]] = {}
    for player_id, player in market.items():
        if player_id not in rebuilt:
            continue
        real = sorted(
            (m for m in rebuilt[player_id].get("matches") or () if not m.get("absent")),
            key=lambda m: int(m["round"]),
        )
        by_round = {int(m["round"]): m for m in real}
        rounds: dict[int, bool] = {}
        played, points = 0, 0.0
        for number in range(1, last_round + 1):
            row = by_round.get(number)
            if row is None:
                club = _club_for(real, number)
                playing = clubs_in.get(number, set())
                if club is None:
                    # Never called up this season: his club as the market has it.
                    if not any(clubs_agree(player.club, seen) for seen in sorted(playing)):
                        continue
                elif club not in playing:
                    continue
                called_up = any(int(m["round"]) <= number for m in real)
                if not called_up and player_id not in at_start:
                    continue
                rounds[number] = False
                continue
            rounds[number] = bool(row.get("used"))
            if rounds[number]:
                played += 1
                points += float(row["points"])
        if rounds:
            out[player_id] = {
                "played": played,
                "points": points,
                "available": len(rounds),
                "rounds": rounds,
            }
    return out


def _merged(
    order: Mapping[str, Any],
    emails: Mapping[str, Mapping[str, Any]],
    early: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """The two halves of one season, added up, in market order."""
    out: dict[str, dict[str, Any]] = {}
    for player_id in order:
        late, first = emails.get(player_id), early.get(player_id)
        if first is None:
            if late is not None:
                out[player_id] = dict(late)
            continue
        if late is None:
            out[player_id] = dict(first)
            continue
        out[player_id] = {
            "played": first["played"] + late["played"],
            "points": first["points"] + late["points"],
            "available": first["available"] + late["available"],
            "rounds": {**first["rounds"], **late["rounds"]},
        }
    return out


def season_so_far(
    market: Mapping[str, Any],
    folder: str | Path,
    *,
    emails: Mapping[int, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[int]]]:
    """This season for `advice.valuation`, and which rounds came from where.

    THE ONE READING. The ledger, the page, the squad proposal and the server's
    `project_points` all take the season from here; each composed it from the
    emails in a line of its own until phase 2.

    `emails` lets the ledger pass the rounds it has already read for its
    consistency check, so the check and the valuation read the same files and
    the check never sees a rebuilt round: the site's totals start at matchday 6.
    """
    folder = Path(folder)
    if emails is None:
        emails = load_official_rounds(
            folder / "pontuacoes", first_round=FIRST_SCORING_MATCHDAY
        )
    rebuilt = load_rebuilt(folder)
    early = (
        early_records(rebuilt, market, at_start=market_at_start(folder))
        if rebuilt is not None
        else {}
    )
    evidence = {
        "email_rounds": sorted(emails),
        "estimated_rounds": sorted({n for record in early.values() for n in record["rounds"]}),
    }
    return _merged(market, current_records(market, emails), early), evidence
