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
leans on his last two rounds, pulled toward his whole record, because being
dropped is news and news goes stale. That is the rule the harness always used;
it replaced this function's own on 15/09/2026, after the first replay of the
function measured the old rule at 0.07 to 0.09 of correlation worse.

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

from collections.abc import Iterable, Mapping
from statistics import mean, pstdev
from typing import Any

from .models import Position
from .stats import (
    APPEARANCE_PRIOR,
    PRIOR_STRENGTH,
    ROTATION_PRIOR,
    ROTATION_WINDOW,
    UNUSED_PENALTY,
    adjust_for_fixture,
    describe_pick,
    fixture_multipliers,
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

#: The name the ledger files a round under, and the track record reads back. A
#: total that spans a change of model measures the change, not the model, so
#: this moves whenever the estimate does. It last moved on 15/09/2026, when the
#: chance of playing began leaning on the last two rounds.
ESTIMATOR = "valuation+fixture+recency"


def players_to_value(held: Iterable[Any], market: Mapping[str, Any]) -> dict[str, Any]:
    """Everyone a valuation should pool over: the market, plus anyone held who left it.

    `valuation` builds its pools — what a club's players return when they play,
    how often the league's players play at all — from exactly the players it is
    given. Given only the twenty-three, every man is shrunk toward his
    squad-mates: a group chosen for being good, and too small for most clubs to
    count. The ledger and the page's eleven did that until 15/09/2026 while the
    page's transfer pooled over the market, so one player carried two values on
    the same page.

    The market's copy of a player wins over the squad file's, because it has his
    club as it is today. A man held who has left the league keeps the squad
    file's, so he can still be valued and sold.
    """
    return {**{player.id: player for player in held}, **market}


def transfer_candidates(
    market: Iterable[str],
    view: Mapping[str, Mapping[str, Any]],
    held: Iterable[str],
    left_out: Iterable[str] = (),
) -> list[str]:
    """Who a transfer search may buy: a real record, and not known to be out.

    A man with no top-flight matches is valued at his club's pool, which is a
    fair estimate and a poor recommendation, so he needs `MIN_OWN_HISTORY`
    rounds of record first. A man the bulletin says is injured or suspended is
    left out as well: on 18/09/2026 the search proposed Santi García, who was
    on it. The squad's own players stay in whatever their state. They are
    already held, and the search needs them to price the squad.
    """
    held, left_out = set(held), set(left_out)
    return [
        i
        for i in market
        if i in held or (view[i]["appearances"] >= MIN_OWN_HISTORY and i not in left_out)
    ]


def recent_playing(
    record: Mapping[str, Any],
    *,
    played: int,
    seen: int,
    league_availability: float,
) -> float:
    """His chance of playing, weighted toward the rounds he has just had.

    The rule of `backtest.two_part_projection`, the estimator every accuracy
    figure in this project was measured on: his last `ROTATION_WINDOW` rounds,
    pulled toward his whole record with the weight of `ROTATION_PRIOR` rounds.
    Being dropped is news, and news goes stale.

    `record` is his season so far and carries `rounds`: each round he was
    available for, mapped to whether he played. `played` and `seen` are his
    whole record, archive and season together.

    One thing the harness never needed and the live season does: the record is
    itself pulled toward the league with the weight of `APPEARANCE_PRIOR`
    rounds. At matchday 7 the official season is one round long, and without
    that a signing with no archive would come out at exactly 0 or 1.

    MEASURED BEFORE IT WAS ADOPTED, by `scripts/replay_valuation.py` on
    15/09/2026, against the rule it replaced — this season's rate anchored to
    the archive, every round alike — on the same player-rounds of both
    reconstructed seasons, matchdays 7-34:

                                  2025/26   2024/25
        the season rule            0.4513    0.4295
        this rule                  0.5200    0.5226
        the harness, for scale     0.5198    0.5222

    Every gain cleared the +0.003 bar with its 90% interval above zero — in
    rounds 7-12 and 7-34, with this season's rounds 1-5 and without them — and
    the Brier score of the chance itself fell from 0.177 to 0.143 and 0.138.
    """
    if record.get("available") and "rounds" not in record:
        raise ValueError(
            "a season record without its rounds — the recent rule cannot see "
            "which of them he played"
        )
    rounds = record.get("rounds") or {}
    recent = [rounds[number] for number in sorted(rounds)[-ROTATION_WINDOW:]]
    history = (played + league_availability * APPEARANCE_PRIOR) / (
        seen + APPEARANCE_PRIOR
    )
    return (sum(1 for took_part in recent if took_part) + history * ROTATION_PRIOR) / (
        len(recent) + ROTATION_PRIOR
    )


def valuation(
    players: Mapping[str, Any],
    archive: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    owned: Mapping[str, float] | None = None,
    prior_strength: float = PRIOR_STRENGTH,
    recency: bool = True,
) -> dict[str, dict[str, Any]]:
    """Every player, valued from the archives and the season so far.

    `players` maps an id to something carrying `position`, `club` and `value` —
    a Player will do. `archive` and `current` each map an id to `played`,
    `points` (scored on the days he played) and `available`, with `archive`
    optionally carrying `each`, the round-by-round scores, for the spread.

    With `recency` his chance of playing comes from `recent_playing`, and every
    `current` record must also carry `rounds`. Without it, from the season
    rule: this season's rate anchored to the archive, every round alike.

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
        now_seen = part(current, "available", player_id)
        if recency:
            playing = recent_playing(
                current.get(player_id) or {},
                played=part(archive, "played", player_id)
                + part(current, "played", player_id),
                seen=archive_seen + now_seen,
                league_availability=league_availability,
            )
        else:
            anchor = archive_rate if archive_rate is not None else league_availability
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


def round_weeks(
    records: Mapping[str, Any], fixtures: Iterable[Any], round_number: int
) -> dict[str, dict[str, Any]]:
    """Each club's match in one round: the opponent, where, when, how hard.

    The multipliers come from the archive's club records, a club without one —
    promoted, or unknown — standing at the league's mean, which is the mean of
    the clubs that have one. A club missing from the result has no match that
    round: not a hard week, no week.

    The page and the ledger each computed this in a copy of their own until
    22/09/2026. One copy now, for them and for the server.
    """
    known = [r for r in records.values() if getattr(r, "has_history", False)]
    league_ga = (
        sum(r.goals_against_per_match for r in known) / len(known) if known else 1.0
    )
    league_gf = sum(r.goals_for_per_match for r in known) / len(known) if known else 1.0

    def rates(club: str) -> tuple[float, float]:
        record = records.get(club)
        if record is None or not record.has_history:
            return league_ga, league_gf
        return record.goals_against_per_match, record.goals_for_per_match

    weeks: dict[str, dict[str, Any]] = {}
    for fixture in fixtures:
        if fixture.round_number != round_number:
            continue
        for club, opponent, at_home in (
            (fixture.home, fixture.away, True),
            (fixture.away, fixture.home, False),
        ):
            own_ga, own_gf = rates(club)
            opp_ga, opp_gf = rates(opponent)
            defensive, attacking = fixture_multipliers(
                own_ga, own_gf, opp_ga, opp_gf, league_ga, league_gf, at_home=at_home
            )
            weeks[club] = {
                "opponent": opponent,
                "at_home": at_home,
                "kickoff": getattr(fixture, "kickoff", None),
                "defensive": defensive,
                "attacking": attacking,
            }
    return weeks


def round_projection(
    players: Iterable[Any],
    view: Mapping[str, Mapping[str, Any]],
    weeks: Mapping[str, Mapping[str, Any]],
    *,
    unavailable: Mapping[str, str] | None = None,
    gone: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """What each player is expected to score in one round, and why.

    THE ONE PROJECTION. The page's eleven, the ledger and the server's
    `project_points` all read a player's round from here; until 22/09/2026 the
    page and the ledger each built it in a copy of their own, and the copies
    disagreed at the edges.

    `view` is `valuation`'s output and `weeks` is `round_weeks` for the round.
    The rules, in the order they bind:

        left the league      0 — nothing he does now scores for this team
        no match this round  0 — §15.3 scores a match not played before the
                             next round begins at nothing, injured or not
        known to be out      -1 — what §10.3(i) pays a man who does not play
        otherwise            his chance of playing times what he returns,
                             moved by the opponent, and -1 for the rest

    The opponent moves what he returns WHEN HE PLAYS and never the blend:
    §10.3(i)'s -1 is the same -1 whoever the opponent is.

    The two edges the copies disagreed on, settled here: out AND without a
    match reads 0, not -1, because without a match nobody scores; and a man
    who has left the league reads 0 in the ledger too, not whatever his old
    club's fixture says.
    """
    out_list = unavailable or {}
    departed = set(gone)
    rows: dict[str, dict[str, Any]] = {}
    for player in players:
        entry = view[player.id]
        week = weeks.get(player.club)
        if player.id in departed or week is None:
            expected = 0.0
        elif player.id in out_list:
            expected = float(UNUSED_PENALTY)
        else:
            expected = entry["playing"] * adjust_for_fixture(
                entry["returns"], player.position, week["defensive"], week["attacking"]
            ) + (1 - entry["playing"]) * float(UNUSED_PENALTY)
        rows[player.id] = {
            "expected": expected,
            "season": entry["expected"],
            "returns": entry["returns"],
            "playing": entry["playing"],
            "appearances": entry["appearances"],
            "opponent": None if week is None else week["opponent"],
            "at_home": None if week is None else week["at_home"],
            "kickoff": None if week is None else week.get("kickoff"),
            "defensive": None if week is None else week["defensive"],
            "attacking": None if week is None else week["attacking"],
            "no_fixture": week is None,
            # Why he is on the out list, whichever rule set his number, and
            # nobody else. The ledger re-records a round when the out list
            # changes, comparing the list to this field, so it must hold the
            # list exactly: an out man without a match left off it, or a
            # departed man put on it, would look like a change on every run.
            "unavailable": out_list.get(player.id),
            "gone": player.id in departed,
        }
    return rows
