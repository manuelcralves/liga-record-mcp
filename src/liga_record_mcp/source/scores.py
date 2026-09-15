"""This season, read from the weekly score emails — one file per round.

WHY THIS EXISTS. On 15 September 2026 Liga Record put every player's
`points_total` back to zero for the official phase: 542 players of 542 carried
exactly their matchday-6 score, and Pavlidis went from 40 to 9. Everything that
read a season out of that total — `points_total / matches` and
`points_total + (available - appearances)` — quietly began to see nine points
in six matches, and nothing errored.

The weekly email is the one source that says which round its numbers belong to,
and it scores every player for that round alone. So the season is built from
`data/pontuacoes/<round>.json`, round by round, and the site's total is kept
only as a cross-check: `consistency_problems` names every way the two disagree,
and the ledger refuses to record while there are any.

HOW A ROUND IS READ.

  * A club in `adiados` or `anulados_15_3` had no match worth reading: all its
    players read 0 whether or not they took the field, so the round counts for
    none of them.
  * -1 is §10.3(i)'s mark for a player who was not used: available, not played.
  * Every other score, 0 included, is a player who played. Zero is a real
    score — Lekovic made 0 for E. Amadora on matchday 6.
  * A player absent from a round's email was not on the league's list that
    round, and the round does not count for him.

What it cannot see is the man who played and netted exactly -1: he reads as
unused. The site's total had the same blind spot, and it is rare.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..models import Fixture
from ..stats import UNUSED_PENALTY
from .base import SquadSourceError


def score_key(name: str, club: str) -> str:
    """How the email files key a player. Names repeat across clubs: matchday 6
    had two Samu, two Robinho, two Pedro Santos and two João Carvalho."""
    return f"{name}|{club}"


def load_official_rounds(
    directory: str | Path, *, first_round: int
) -> dict[int, dict[str, Any]]:
    """Every round filed in `directory` from `first_round` on, keyed by round.

    Files are named by round number. Anything else in the folder, and every
    round before `first_round`, is left alone: the trial rounds were filed for
    the squad only and describe a points table the site has since erased. A
    file that cannot be read, or that claims to be a different round from its
    name, fails loudly — a season quietly short of a round is the failure this
    module exists to prevent.
    """
    folder = Path(directory)
    rounds: dict[int, dict[str, Any]] = {}
    if not folder.is_dir():
        return rounds
    for file in sorted(folder.glob("*.json")):
        if not file.stem.isdigit() or int(file.stem) < first_round:
            continue
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SquadSourceError(f"{file} is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("jogadores"), dict):
            raise SquadSourceError(f"{file} has no 'jogadores' table")
        if int(doc.get("ronda", -1)) != int(file.stem):
            raise SquadSourceError(
                f"{file} says it is round {doc.get('ronda')}, not {file.stem}"
            )
        rounds[int(file.stem)] = doc
    return rounds


def _entry(doc: Mapping[str, Any], name: str, club: str) -> tuple[str, int] | None:
    """One player's (club, score) in one round's email, or None.

    By name and club first. A player who has since moved between Portuguese
    clubs is filed under his old club in the rounds before the move, so a name
    that appears exactly once in the round is taken as him. A name shared by
    two players is never guessed.
    """
    table = doc["jogadores"]
    key = score_key(name, club)
    if key in table:
        return club, table[key]
    prefix = f"{name}|"
    found = [k for k in table if k.startswith(prefix)]
    if len(found) == 1:
        return found[0][len(prefix):], table[found[0]]
    return None


def _without_a_match(doc: Mapping[str, Any]) -> set[str]:
    return set(doc.get("adiados") or ()) | set(doc.get("anulados_15_3") or ())


def season_records(
    market: Mapping[str, Any], rounds: Mapping[int, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Appearances and points-when-playing per player, in the shape
    `advice.valuation` reads: `played`, `points` and `available`."""
    out: dict[str, dict[str, Any]] = {}
    for player in market.values():
        played = available = points = 0
        for doc in rounds.values():
            found = _entry(doc, player.name, player.club)
            if found is None:
                continue
            club, score = found
            if club in _without_a_match(doc):
                continue
            available += 1
            if score == UNUSED_PENALTY:
                continue
            played += 1
            points += score
        if available:
            out[player.id] = {"played": played, "points": points, "available": available}
    return out


def consistency_problems(
    market: Mapping[str, Any],
    rounds: Mapping[int, Mapping[str, Any]],
    fixtures: Iterable[Fixture],
    *,
    first_round: int,
) -> list[str]:
    """Why a projection must not be recorded from this data — empty if it may.

    Two checks, in order:

      * every official round that already has a match played must have its
        email on file. Without it the season is short a round the site has
        already added to its totals;
      * every player's `points_total` on the site must equal the sum of his
        scores in those emails. It did on 15/09/2026, for 542 of 542. A whole market exactly one round
        behind the emails is accepted: it is the known lag of the site, below. If that
        stops, either a round is missing or the site has changed what its total
        means again — and it has done that once without warning.

    Totals are compared only once the rounds are complete: with a round
    missing, every player who played in it would be listed, and the one line
    that matters would be lost among five hundred.
    """
    problems: list[str] = []
    scored = sorted(
        {f.round_number for f in fixtures if f.played and f.round_number >= first_round}
    )
    missing = [r for r in scored if r not in rounds]
    if missing:
        problems.append(
            "falta o email da(s) jornada(s) "
            + ", ".join(str(r) for r in missing)
            + " em data/pontuacoes/ — já têm jogos disputados"
        )
        return problems

    # Every score the site adds, the email adds too, postponed and voided
    # clubs included. `season_records` skips those rounds because they say
    # nothing about who played. This check asks a different question, whether
    # the emails add up to the total the site shows, so it counts them. Both
    # carry 0 for such a club today and agree. If the site ever pays a
    # postponed match late, as §15.3 allows for one postponed after the lock,
    # this is the check that should notice, which is why it does not skip them.
    latest = max(rounds) if rounds else None
    through_latest: dict[str, int] = {}
    through_previous: dict[str, int] = {}
    for player in market.values():
        total = before = 0
        for number, doc in rounds.items():
            found = _entry(doc, player.name, player.club)
            if found is None:
                continue
            total += found[1]
            if number != latest:
                before += found[1]
        through_latest[player.id] = total
        through_previous[player.id] = before

    # THE SITE CAN BE ONE ROUND BEHIND ITS OWN EMAILS. It publishes a round
    # days before it folds the points into `points_total` (the reason
    # `round_is_published` exists in record_projection.py), and on 25/08/2026
    # it was still serving the previous round after round 3 had been played
    # and emailed. A whole market exactly one round behind is that lag, not a
    # fault: the season is read from the emails, so nothing the projection
    # uses is stale, and refusing would fail the scheduled job at every run
    # until the site caught up, possibly past the lock. Two rounds behind, or
    # players disagreeing about how far behind the site is, is refused.
    def all_agree(expected: dict[str, int]) -> bool:
        return all(p.points_total == expected[p.id] for p in market.values())

    if all_agree(through_latest) or all_agree(through_previous):
        return problems

    wrong = [
        f"{p.name} ({p.club}): site {p.points_total}, emails {through_latest[p.id]}"
        for p in market.values()
        if p.points_total != through_latest[p.id]
    ]
    shown = "; ".join(wrong[:8]) + (" …" if len(wrong) > 8 else "")
    problems.append(
        f"{len(wrong)} jogador(es) com o total do site diferente da soma dos "
        f"emails: {shown}"
    )
    return problems
