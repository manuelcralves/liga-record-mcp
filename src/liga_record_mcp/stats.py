"""Derived statistics. Pure functions, no I/O — same contract as rules.py.

This module exists because of a real mistake. A transfer proposal ranked the
squad on total points while Sp. Braga and Gil Vicente had a postponed fixture
and had played one match fewer than everyone else. Four of the twelve players
it recommended selling were among the best in the squad per match — Fran
Navarro at 6.0 a game looked like a 6-point player next to someone else's 12.

Totals are only comparable when the denominators match. Nothing here presents a
total without the number of matches behind it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .models import Fixture, Player


def matches_played(fixtures: Iterable[Fixture]) -> dict[str, int]:
    """How many matches each club has actually completed.

    A postponed fixture means clubs are mid-season on different counts, which
    is exactly the trap this module exists to close.
    """
    counts: dict[str, int] = {}
    for fixture in fixtures:
        if not fixture.played:
            continue
        for club in (fixture.home, fixture.away):
            counts[club] = counts.get(club, 0) + 1
    return counts


def per_match(points: int, matches: int) -> float | None:
    """Points per match, or None when the club has not played yet.

    None rather than 0.0 on purpose: "no data" and "scored nothing" are
    different claims, and rounding them together is how the original mistake
    got made.
    """
    if matches <= 0:
        return None
    return points / matches


def never_played(player: Player, matches: int) -> bool:
    """True when a player has been left out of every match so far.

    §10.3 gives an unused player -1 a round, so someone sitting at exactly
    minus the number of matches has not taken the field once. Across the
    market that is 42% of players, and the gap between them and a regular
    starter is the largest single effect in the game.
    """
    return matches > 0 and player.points_total == -matches


def rate_rows(
    players: Sequence[Player], matches_by_club: dict[str, int]
) -> list[dict[str, object]]:
    """Players with their scoring rate, best first.

    `value_rate` is points per match per million — the figure that actually
    answers "is this player worth his price", since it normalises both the
    denominator and the cost.
    """
    rows: list[dict[str, object]] = []
    for player in players:
        matches = matches_by_club.get(player.club, 0)
        rate = per_match(player.points_total, matches)
        rows.append(
            {
                "id": player.id,
                "name": player.name,
                "position": player.position.value,
                "club": player.club,
                "value": player.value,
                "points_total": player.points_total,
                "matches_played": matches,
                "points_per_match": None if rate is None else round(rate, 2),
                "value_rate": (
                    None if rate is None else round(rate / (player.value / 1e6), 2)
                ),
                "never_played": never_played(player, matches),
            }
        )
    rows.sort(key=lambda r: (r["points_per_match"] is None, -(r["points_per_match"] or 0)))
    return rows
