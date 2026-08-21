"""What every player is worth right now, in one place.

This was written twice — once inside the squad proposal and once about to be
written again inside the dashboard — and two copies of a valuation is the worst
possible duplication. They would not fail; they would quietly disagree, and the
page would recommend one transfer while the script recommended another, with
nothing to say which was the model's actual opinion.

WHAT IT DOES. Each player is estimated in two halves, because they behave
differently and folding them together hides the larger one:

    expected = P(plays) x (what he returns when he plays) + P(not) x -1

His returns are shrunk toward what players at HIS CURRENT CLUB in his position
return, because his old club's numbers are not his and a man who has moved to
Porto is not the player his Arouca record says he is. His chance of playing
leans on the current season, because being in the side is news and last
season's team sheet is not.

TWO THINGS THAT HAVE TO BE RIGHT and neither is obvious.

A player must not be in his own pool. Shrinking an estimate toward a group he
belongs to shrinks him toward himself, which is not shrinkage — in a group of
one it does nothing at all. That is not hypothetical: it once proposed three
goalkeepers from the same club at the floor price, on one match between them,
because each was his own prior and the prior was a good afternoon.

And the pool must be weighted by evidence. Unweighted, a player with one
appearance moves the group as much as one with seventy, so a single loud
afternoon becomes the club's expected return and every team-mate inherits it.
"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import mean, pstdev
from typing import Any

from .models import Position
from .stats import (
    APPEARANCE_PRIOR,
    PRIOR_STRENGTH,
    ROTATION_PRIOR,
    UNUSED_PENALTY,
    describe_pick,
)

#: How many appearances a club-and-position group needs before it is worth more
#: than the league's average for that position. Ten is about three players'
#: worth of a fortnight — enough to say something, far short of enough to
#: override it.
MIN_POOL_APPEARANCES = 10

#: Matchdays of a player's own record before his appearance rate is trusted
#: over the league's. Below it the sample says more about the fixture list than
#: about him.
MIN_OWN_HISTORY = 20


def valuation(
    players: Mapping[str, Any],
    archive: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    owned: Mapping[str, float] | None = None,
    prior_strength: float = PRIOR_STRENGTH,
) -> dict[str, dict[str, Any]]:
    """Every player, valued from the archives and the season so far.

    `players` maps an id to something carrying `position`, `club` and `value` —
    a Player will do. `archive` and `current` each map an id to `played`,
    `points` (scored on the days he played) and `available`, with `archive`
    optionally carrying `each`, the round-by-round scores, for the spread.

    Returns one entry per player: what he returns when he plays, his chance of
    playing, the two multiplied out, and enough of the working to argue with —
    including how many matches the estimate rests on, which is the difference
    between a measurement and a club average wearing his name.
    """

    def part(source: Mapping[str, Mapping[str, Any]], key: str, player_id: str):
        return (source.get(player_id) or {}).get(key, 0)

    seen_all = {
        i: part(archive, "played", i) + part(current, "played", i) for i in players
    }
    scored_all = {
        i: part(archive, "points", i) + part(current, "points", i) for i in players
    }

    # The pool: what a player at this club in this position returns when he
    # plays. The club is the CURRENT one.
    tally: dict[tuple[str, str], tuple[float, int]] = {}
    for player_id, player in players.items():
        if not seen_all[player_id]:
            continue
        cell = (player.club, player.position.value)
        points, seen = tally.get(cell, (0.0, 0))
        tally[cell] = (points + scored_all[player_id], seen + seen_all[player_id])

    total_points = sum(points for points, _ in tally.values())
    total_seen = sum(seen for _, seen in tally.values())
    league = total_points / total_seen if total_seen else 0.0
    by_position = {}
    for position in Position:
        by_position[position] = (
            sum(p for (_, pos), (p, _) in tally.items() if pos == position.value),
            sum(s for (_, pos), (_, s) in tally.items() if pos == position.value),
        )

    rates = [
        (part(archive, "played", i) + part(current, "played", i)) / total
        for i in players
        if (total := part(archive, "available", i) + part(current, "available", i)) > 0
    ]
    league_availability = mean(rates) if rates else 0.5

    out: dict[str, dict[str, Any]] = {}
    for player_id, player in players.items():
        cell = (player.club, player.position.value)
        points, seen = tally.get(cell, (0.0, 0))
        points -= scored_all[player_id]
        seen -= seen_all[player_id]
        if seen >= MIN_POOL_APPEARANCES:
            prior = points / seen
        else:
            # The position is the fallback, and it must leave him out too. It
            # is the same mistake one level up: a thin club pool falls back to
            # a position average that still contains him, and in a small market
            # that shrinks a man toward himself all over again. A test caught
            # it doing exactly that.
            pool_points, pool_seen = by_position[player.position]
            pool_points -= scored_all[player_id]
            pool_seen -= seen_all[player_id]
            if pool_seen:
                prior = pool_points / pool_seen
            elif total_seen - seen_all[player_id] > 0:
                # And the league is the last fallback, leaving him out as well.
                prior = (total_points - scored_all[player_id]) / (
                    total_seen - seen_all[player_id]
                )
            else:
                # Nobody else in the league has played. There is no outside
                # information, so there is nothing to shrink toward: his own
                # rate stands, and `appearances` is what says how much it is
                # worth. describe_pick calls that a bet, which it is.
                prior = (
                    scored_all[player_id] / seen_all[player_id]
                    if seen_all[player_id]
                    else 0.0
                )

        appearances = seen_all[player_id]
        returns = (scored_all[player_id] + prior * prior_strength) / (
            appearances + prior_strength
        )

        # Availability leans on the current season: being in the side is news,
        # and a team sheet from two years ago at another club is not.
        archive_seen = part(archive, "available", player_id)
        archive_rate = (
            part(archive, "played", player_id) / archive_seen
            if archive_seen
            else None
        )
        anchor = archive_rate if archive_rate is not None else league_availability
        now_seen = part(current, "available", player_id)
        playing = (
            part(current, "played", player_id)
            + anchor * APPEARANCE_PRIOR
            + league_availability * ROTATION_PRIOR
        ) / (now_seen + APPEARANCE_PRIOR + ROTATION_PRIOR)

        each = (archive.get(player_id) or {}).get("each") or []
        spread = pstdev(each) if len(each) >= 15 else None

        out[player_id] = {
            "returns": returns,
            "playing": playing,
            "expected": playing * returns + (1 - playing) * float(UNUSED_PENALTY),
            "appearances": appearances,
            "available": archive_seen + now_seen,
            "spread": spread,
            "prior": prior,
            # The label answers "is this a man who plays", so it is fed his own
            # record rather than an estimate shrunk toward the league on two
            # rounds of a new season — the projection is right to be cautious
            # and the label is answering a different question.
            **describe_pick(
                appearances=appearances,
                availability=(
                    archive_rate if archive_seen >= MIN_OWN_HISTORY else playing
                ),
                volatility=spread,
                owned_percent=(owned or {}).get(player_id),
            ),
        }
    return out
