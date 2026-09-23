"""The coach of the round, priced on the round's match — for the page and the ledger.

Coaches are free and chosen every round (§6.13–6.16), and what one scores rides
on his club's result, so the round to value him on is the one about to be
played: a big club at home to a weak one, not a big club in a derby.

ONE PRICE, TWO READERS. The page ranks the eighteen on it and the ledger files
the coach on the sheet with it; a page and a ledger pricing the same man two
ways would put two numbers beside his name, which is what happened on
22/09/2026 — 1.33 filed, +0.41 advised.

MEASURED before either used it (`scripts/measure_coach_pick.py`, §14.3 on
openfootball's results, strengths only from matches already played):

    choosing on the match, against one coach for the season
        +9 points in 2024/25, +3 in 2025/26, +11 in 2023/24
    predicting a coach's round, correlation (mean error)
        on the match      0.52 (1.24)   0.55 (1.24)   0.46 (1.30)
        project_coach     0.37 (1.33)   0.38 (1.40)   0.29 (1.40)
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .final_table import coach_values, strengths
from .stats import MEAN_MARK_POINTS, league_table


def round_strengths(records: Mapping[str, Any], fixtures: Iterable[Any]) -> dict:
    """The Final Table's goal model on the archive and the table so far."""
    return strengths(records, league_table(fixtures))


def rank_coaches(
    coaches: Sequence[Mapping[str, Any]],
    fixtures: Iterable[Any],
    strength: Mapping[str, tuple[float, float]],
    round_number: int,
) -> list[dict]:
    """The eighteen coaches, priced on this round's match, best first.

    `expected` is what the coach is expected to score: §14.3 over every
    scoreline of his club's match, plus the editorial mark every coach is
    credited on average. The mark is the same for all eighteen, so it moves no
    choice — but it is in what he actually scores, and the ledger compares
    against that. A club with no match this round scores nothing: 0, and no
    opponent.

    Joined on the exact club name: the coaches file, the calendar and the
    market all write clubs as the site does.
    """
    week = [(f.home, f.away) for f in fixtures if f.round_number == round_number]
    values = coach_values(week, strength)
    against: dict[str, tuple[str, bool]] = {}
    for home, away in week:
        against[home] = (away, True)
        against[away] = (home, False)
    rows = []
    for coach in coaches:
        club = coach["club"]
        opponent, at_home = against.get(club, (None, None))
        rows.append(
            {
                "id": coach.get("id"),
                "name": coach["name"],
                "club": club,
                "opponent": opponent,
                "at_home": at_home,
                "expected": (
                    values[club] + MEAN_MARK_POINTS if club in values else 0.0
                ),
            }
        )
    rows.sort(key=lambda r: (r["opponent"] is None, -r["expected"], r["club"]))
    return rows


def _words(text: str) -> set[str]:
    """A club label as bare words: no accents, no case, no dots."""
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return {word for word in stripped.replace(".", " ").split() if word}


def same_club(one: str, other: str) -> bool:
    """Whether two labels name one club, allowing one to be shorter.

    The site writes the same club two ways in two places: the weekly email
    credits "Bruno Pinheiro|Académico" where the calendar, the market and the
    coaches file all say "Académico Viseu". Matching on the exact string
    dropped that club — silently, which is the worst way to drop one.

    Short of a subset there is no match, so "Nacional" and "Internacional"
    stay apart, and none of the nineteen labels the project holds — the coaches
    file, the calendar and the emails — is a subset of another.

    The one edge the subset rule leaves open is a reserve side: "Sporting" is a
    subset of "Sporting B". No such label exists anywhere here, because the
    game is the Primeira Liga and nothing else, but a source that brought them
    in would need more than words to tell the two apart.
    """
    first, second = _words(one), _words(other)
    return bool(first) and bool(second) and (first <= second or second <= first)


def coach_points_by_club(official: Mapping[str, Any], club: str) -> int | None:
    """What a club's coach scored, read off a round's email file by the club.

    The email's `treinadores` are keyed "name|club". The model picks a coach
    for his club, so the club is the key that does not break when a club
    changes coach between the file and the email — and `same_club` is what
    keeps it from breaking when the two spell the club differently.
    """
    for key in sorted(official.get("treinadores") or {}):
        if same_club(key.split("|", 1)[-1], club):
            return official["treinadores"][key]
    return None
