"""Replaying a season without letting the future leak into it.

The hard part of testing a strategy is not the strategy. It is making sure that
at every decision the code knows only what a manager knew at the time — and
that is easy to get wrong in ways that look like brilliance. A projection built
from the whole season, used to pick a team in October, will beat any real
manager and prove nothing.

So one rule runs through this module: a decision taken before matchday M may
read matchdays strictly before M, and nothing else. Every function that could
break it takes `upto` and honours it.

Three things get replayed:

    replay                 a fixed squad, round by round
    replay_with_transfers  a squad that changes, one transfer a round under §6.8
    best_transfer          the swap the projection would actually recommend

The first two also come in a hindsight flavour, by handing them the real scores
as the forecast. That is not cheating as long as it is labelled: the gap
between the two is the value of knowing the future, which is worth measuring
precisely because it bounds what any amount of skill can win.

WHAT OF THE ROUND IS MODELLED. Twenty-three are owned, fifteen are named, and
eleven score (§6.13). The other eight contribute nothing whatever they did; a
named substitute contributes nothing unless §11 brings him on, like for like
and at most three of them; a starter who did not play costs -1 under §10.3(i)
if nobody can replace him; and the captain doubles, passing the armband to
whoever comes on for him (§11.5, §10.3(l)).

WHAT IS NOT, and each of these makes the totals here a floor rather than a
forecast:

  * §14.5's absences. A coach's points depend only on his club's result, so a
    round can be scored without knowing his name — but a suspended or sacked
    coach scores nothing at all, and that needs a name. Coaches are otherwise
    modelled, and are the second-largest free decision in the game.
  * Prices never move. §12.3 and §12.4 reprice a squad every week, which funds
    or blocks the next transfer. Last season's prices are gone, so replaying
    that would mean inventing them.
  * §6.9's February window — six swaps inside one month — and §6.10 and
    §6.11's compensations.
  * §6.17's three holiday rounds, which are worth roughly half a winning score
    each and are a strategy in their own right.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from statistics import mean
from typing import Any

from .models import Player, Position, Selection, Squad
from .optimise import best_eleven
from .rules import simulate_autosubs
from .stats import (
    PRIOR_STRENGTH,
    ROTATION_PRIOR,
    ROTATION_WINDOW,
    UNUSED_PENALTY,
)

#: A round a player has no entry for. §10.3(i): he did not take the field, and
#: whoever owned him was charged for it.
ABSENT = float(UNUSED_PENALTY)


def appeared(history: Mapping[str, Mapping[int, float]], player: str, matchday: int) -> bool:
    """Whether a player took the field — what §11 substitutes on."""
    return history.get(player, {}).get(matchday, ABSENT) != ABSENT


def shrunk_projection(
    history: Mapping[str, Mapping[int, float]],
    cells: Mapping[str, tuple[str, str]],
    *,
    upto: int,
    prior_strength: float = PRIOR_STRENGTH,
) -> dict[str, float]:
    """Each player's expected rate, from matchdays strictly before `upto`.

    The same shrinkage the live projection uses: a player's own record blended
    with what everyone at his club in his position has returned, the prior
    worth `prior_strength` rounds. A player with nothing behind him gets the
    pool, which is the honest answer and also the position a manager is in on
    the opening day.

    `cells` maps a player to the (club, position) he is pooled with. It is
    passed in rather than derived because which club a player belongs to is a
    fact about the season being replayed, not about this arithmetic.
    """
    pooled: dict[tuple[str, str], list[float]] = {}
    for player, by_matchday in history.items():
        cell = cells.get(player)
        if cell is None:
            continue
        pooled.setdefault(cell, []).extend(
            points for matchday, points in by_matchday.items() if matchday < upto
        )

    everything = [p for values in pooled.values() for p in values]
    overall = mean(everything) if everything else 0.0

    estimates: dict[str, float] = {}
    for player, by_matchday in history.items():
        cell = cells.get(player)
        values = pooled.get(cell) if cell else None
        prior = mean(values) if values else overall
        own = [p for matchday, p in by_matchday.items() if matchday < upto]
        estimates[player] = (
            (sum(own) + prior * prior_strength) / (len(own) + prior_strength)
            if own
            else prior
        )
    return estimates


def two_part_projection(
    points: Mapping[str, Mapping[int, float]],
    minutes: Mapping[str, Mapping[int, int]],
    cells: Mapping[str, tuple[str, str]],
    *,
    upto: int,
    prior_strength: float = PRIOR_STRENGTH,
    window: int = ROTATION_WINDOW,
    rotation_prior: float = ROTATION_PRIOR,
    minutes_weighted: bool = False,
    parts: bool = False,
) -> dict[str, float] | tuple[dict[str, float], dict[str, float]]:
    """Expected points, split into whether he plays and what he does when he does.

    `shrunk_projection` averages a player's rounds, which folds the two
    together: a nailed-on starter returning four and a rotation player who
    returns eight half the time both come out at four, and they are not the
    same bet at all. Worse, the fold moves slowly. A man who has just lost his
    place carries months of the points he scored when he still had it, and the
    projection keeps recommending him for weeks.

    So:

        expected = P(plays) x (points when he plays) + P(does not) x -1

    P(plays) comes from the last `window` rounds, pulled toward his own
    season-to-date rate — a short window because being dropped is news and last
    October is not. The rest of the model is unchanged: what he returns when he
    plays is shrunk toward his club-and-position pool exactly as before.

    With `parts` the two halves come back separately rather than multiplied
    together, which is what the squad optimiser needs: it prices depth by
    drawing who turns up, and cannot do that from a single blended number.

    With `minutes_weighted` the return is scaled by how much of a match he has
    lately been getting, relative to what he was getting when he earned those
    returns. That is the difference between a starter and a man who comes on at
    eighty minutes, which the appearance flag alone cannot see.

    It is off by default because it measures WORSE. Predicting each round from
    what came before, over two seasons and about ten thousand player-rounds, it
    loses to the plain split in both (1.632 against 1.591, and 1.636 against
    1.591). The idea is sound and the estimate is too noisy to pay for itself.
    """
    played_pool: dict[tuple[str, str], list[float]] = {}
    for player, by_matchday in points.items():
        cell = cells.get(player)
        if cell is None:
            continue
        played_pool.setdefault(cell, []).extend(
            value
            for matchday, value in by_matchday.items()
            if matchday < upto and minutes.get(player, {}).get(matchday, 0) > 0
        )
    everything = [v for values in played_pool.values() for v in values]
    overall = mean(everything) if everything else 0.0

    appearances = [
        1.0 if by_matchday.get(m, 0) > 0 else 0.0
        for by_matchday in minutes.values()
        for m in range(1, upto)
    ]
    league_availability = mean(appearances) if appearances else 0.5

    estimates: dict[str, float] = {}
    halves: dict[str, tuple[float, float]] = {}
    for player, by_matchday in points.items():
        cell = cells.get(player)
        his_minutes = minutes.get(player, {})
        earlier = [m for m in by_matchday if m < upto]

        # What he returns when he takes the field.
        on_the_field = [m for m in earlier if his_minutes.get(m, 0) > 0]
        pool = played_pool.get(cell) if cell else None
        prior = mean(pool) if pool else overall
        returns = (
            (sum(by_matchday[m] for m in on_the_field) + prior * prior_strength)
            / (len(on_the_field) + prior_strength)
            if on_the_field
            else prior
        )

        # Whether he takes it at all, weighted toward what has happened lately.
        recent = [m for m in earlier if m >= upto - window]
        recent_rate = (
            sum(1 for m in recent if his_minutes.get(m, 0) > 0) / len(recent)
            if recent
            else league_availability
        )
        season_rate = (
            len(on_the_field) / len(earlier) if earlier else league_availability
        )
        weight = len(recent)
        playing = (recent_rate * weight + season_rate * rotation_prior) / (
            weight + rotation_prior
        ) if (weight + rotation_prior) else league_availability

        if minutes_weighted and on_the_field:
            usual = mean([his_minutes[m] for m in on_the_field])
            lately = [his_minutes[m] for m in recent if his_minutes.get(m, 0) > 0]
            if usual > 0 and lately:
                # Bounded: a man who played ten minutes once should not project
                # at a tenth of himself, nor a cameo at three times.
                returns *= min(1.5, max(0.5, mean(lately) / usual))

        estimates[player] = playing * returns + (1.0 - playing) * ABSENT
        halves[player] = (playing, returns)
    if parts:
        return (
            {i: p for i, (p, _) in halves.items()},
            {i: r for i, (_, r) in halves.items()},
        )
    return estimates


def _rows(squad_ids: Sequence[str], market: Mapping[str, Player]) -> list[dict[str, Any]]:
    return [
        {"id": market[i].id, "position": market[i].position, "value": market[i].value}
        for i in squad_ids
    ]


def play_round(
    squad_ids: Sequence[str],
    market: Mapping[str, Player],
    history: Mapping[str, Mapping[int, float]],
    matchday: int,
    *,
    forecast: Mapping[str, float] | None = None,
    knows_availability: bool = False,
) -> dict[str, Any]:
    """Score one round for one squad.

    `forecast` is what the manager believed before kickoff. Without it the
    team is picked knowing the results, which is a ceiling and not a strategy.

    `knows_availability` separates two different kinds of ignorance. A manager
    does not know how a player will play; he usually does know whether the man
    is injured, because the press says so. Conflating them makes every
    simulated season harsher than any real one.
    """
    actual = {i: history.get(i, {}).get(matchday, ABSENT) for i in squad_ids}
    if forecast is None:
        expected = actual
    else:
        expected = {i: forecast.get(i, ABSENT) for i in squad_ids}
        if knows_availability:
            # Ranked below every fit player rather than removed, so a squad
            # whose keepers are all out can still field a legal XI.
            expected = {
                i: (v if appeared(history, i, matchday) else v - 1000.0)
                for i, v in expected.items()
            }

    sheet = best_eleven(_rows(squad_ids, market), expected)
    if sheet is None:
        raise ValueError("this squad cannot field a legal XI")

    starters, captain = list(sheet["starters"]), sheet["captain"]
    substitutions = 0
    if forecast is not None:
        squad = Squad(
            team_id=0,
            team_name="replay",
            players=tuple(market[i] for i in squad_ids),
        )
        result = simulate_autosubs(
            squad,
            Selection(
                starters=tuple(starters),
                bench=tuple(sheet["bench"]),
                captain=captain,
                coach_id="replay",
            ),
            [i for i in starters if not appeared(history, i, matchday)],
        )
        for swap in result.substitutions:
            starters[starters.index(swap.out_id)] = swap.in_id
        substitutions = len(result.substitutions)
        captain = result.captain_id or captain

    return {
        "points": sum(actual[i] for i in starters) + actual.get(captain, 0.0),
        "starters": starters,
        "captain": captain,
        "formation": sheet["formation"],
        "substitutions": substitutions,
    }


def pick_coach(
    coaches: Mapping[str, Mapping[int, float]],
    matchday: int,
    *,
    forecast: Mapping[str, float] | None = None,
) -> tuple[str | None, float]:
    """The coach to field this round, and what he actually returned.

    §6.15 lets a participant change coach every round and §6.4 charges nothing
    for it, so this is the one pick in the game with no constraint on it at
    all — no budget, no quota, no like-for-like. It is chosen every round
    because there is no reason not to.

    With `forecast` the choice is made on what was known beforehand; without
    it, knowing the result, which is a ceiling and not a strategy.
    """
    playing = [club for club, rounds in coaches.items() if matchday in rounds]
    if not playing:
        return None, 0.0
    view = forecast if forecast is not None else {c: coaches[c][matchday] for c in playing}
    chosen = max(playing, key=lambda club: (view.get(club, 0.0), club))
    return chosen, coaches[chosen][matchday]


def replay(
    squad_ids: Sequence[str],
    market: Mapping[str, Player],
    history: Mapping[str, Mapping[int, float]],
    matchdays: Iterable[int],
    *,
    forecasts: Mapping[int, Mapping[str, float]] | None = None,
    knows_availability: bool = False,
    coaches: Mapping[str, Mapping[int, float]] | None = None,
    coach_forecasts: Mapping[int, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Play a fixed squad through a season and total it up.

    `coaches` maps a club to what its coach scored each matchday. Supplying it
    adds §14.4's contribution, which is otherwise silently missing from every
    total: a coach scores in every round, for nothing, and the difference
    between the best pick and the worst was 86 points across last season.
    """
    per_round, substitutions, coach_points_won = [], 0, 0.0
    for matchday in matchdays:
        played = play_round(
            squad_ids,
            market,
            history,
            matchday,
            forecast=forecasts[matchday] if forecasts else None,
            knows_availability=knows_availability,
        )
        total = played["points"]
        if coaches is not None:
            _, earned = pick_coach(
                coaches,
                matchday,
                forecast=coach_forecasts[matchday] if coach_forecasts else None,
            )
            total += earned
            coach_points_won += earned
        per_round.append(total)
        substitutions += played["substitutions"]
    summary = _summarise(per_round, substitutions, transfers=[])
    summary["coach_points"] = round(coach_points_won, 1)
    return summary


def best_transfer(
    squad_ids: Sequence[str],
    market: Mapping[str, Player],
    projection: Mapping[str, float],
    *,
    budget: int,
    rounds_left: int,
    min_gain: float = 0.0,
    candidates: Iterable[str] | None = None,
    sold: Iterable[str] = (),
) -> tuple[str, str, float] | None:
    """The one swap §6.8 allows that the projection likes best, or None.

    Like for like and inside the budget, because §6.8 and §6.4 are not
    preferences. The gain is measured over the rounds still to be played, since
    a swap in October is worth twenty-nine times its weekly edge and the same
    swap in May is worth once.

    THE GAIN IS MEASURED ON THE ELEVEN, not on the player. Only eleven of
    twenty-three score, so replacing a substitute with a better substitute is
    worth exactly nothing — and comparing the two players' own rates cannot see
    that. A backtest caught this: swaps worth +207 points of raw player form
    moved the season by +5, because most of them upgraded men who were never
    going to start.

    `min_gain` is the whole strategic question. A transfer costs nothing and
    does not accumulate — skipping a round banks nothing — so the naive rule is
    to take any positive edge. But a projected edge is not a real one, and
    acting on noise churns a squad for nothing. Passing a threshold here is how
    that gets measured rather than assumed.
    """
    held = set(squad_ids)
    # Never buy back a man already sold. Two near-identical players make the
    # projection flicker between them, and without this the model spent four of
    # a season's transfers swapping one defender for another and back again —
    # paying the churn and gaining nothing.
    barred = held | set(sold)
    value = sum(market[i].value for i in held)
    pool = [
        market[i]
        for i in (candidates if candidates is not None else market)
        if i not in barred
    ]

    by_position: dict[Position, list[Player]] = {}
    for player in pool:
        by_position.setdefault(player.position, []).append(player)
    for players in by_position.values():
        players.sort(key=lambda p: (-projection.get(p.id, ABSENT), p.id))

    def eleven_worth(ids: Sequence[str]) -> float:
        sheet = best_eleven(_rows(ids, market), projection)
        return sheet["points"] if sheet else float("-inf")

    standing = eleven_worth(squad_ids)

    best: tuple[str, str, float] | None = None
    for position, out_id in ((market[i].position, i) for i in squad_ids):
        headroom = budget - value + market[out_id].value
        for incoming in by_position.get(position, ()):
            if incoming.value > headroom:
                continue
            # Sorted by projection, so the first affordable candidate is the
            # best available for this outgoing player: a higher-rated incoming
            # can never make the eleven worse.
            swapped = [incoming.id if i == out_id else i for i in squad_ids]
            gain = (eleven_worth(swapped) - standing) * rounds_left
            if gain >= min_gain and gain > 0 and (best is None or gain > best[2]):
                best = (out_id, incoming.id, gain)
            break
    return best


def replay_with_transfers(
    opening: Sequence[str],
    market: Mapping[str, Player],
    history: Mapping[str, Mapping[int, float]],
    matchdays: Sequence[int],
    *,
    cells: Mapping[str, tuple[str, str]],
    budget: int,
    min_gain: float = 0.0,
    knows_availability: bool = True,
    prior_strength: float = PRIOR_STRENGTH,
    candidates: Iterable[str] | None = None,
    forecasts: Mapping[int, Mapping[str, float]] | None = None,
    coaches: Mapping[str, Mapping[int, float]] | None = None,
    coach_forecasts: Mapping[int, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Play a season making one transfer a round, deciding as the season goes.

    This is the question the whole project exists to answer, asked honestly:
    standing before each round with only what has happened so far, make the
    swap the model recommends, and see at the end whether the season was better
    for it. Everything is recomputed each round from matchdays strictly
    earlier, so nothing the manager could not have known reaches the decision.

    §6.9's February window is not modelled — six swaps in one month is a
    different problem, and blurring it into "one a round" would quietly answer
    neither.
    """
    squad = list(opening)
    per_round, substitutions = [], 0
    transfers: list[dict[str, Any]] = []
    sold: set[str] = set()
    coach_points_won = 0.0

    for index, matchday in enumerate(matchdays):
        # Supplying `forecasts` is how the controls are built: hand it the real
        # remaining-season rates for a hindsight ceiling, or noise for a random
        # baseline. Anything that is not the honest projection must be labelled
        # by whoever passes it, because nothing here can tell the difference.
        projection = (
            forecasts[matchday]
            if forecasts is not None
            else shrunk_projection(
                history, cells, upto=matchday, prior_strength=prior_strength
            )
        )
        rounds_left = len(matchdays) - index
        swap = best_transfer(
            squad,
            market,
            projection,
            budget=budget,
            rounds_left=rounds_left,
            min_gain=min_gain,
            candidates=candidates,
            sold=sold,
        )
        if swap is not None:
            out_id, in_id, gain = swap
            squad[squad.index(out_id)] = in_id
            sold.add(out_id)
            transfers.append(
                {
                    "matchday": matchday,
                    "out": market[out_id].name,
                    "in": market[in_id].name,
                    "projected_gain": round(gain, 1),
                    # Filled in by the caller once the season is over: what the
                    # swap was actually worth over the rounds that followed.
                    "out_id": out_id,
                    "in_id": in_id,
                    "rounds_left": rounds_left,
                }
            )

        played = play_round(
            squad,
            market,
            history,
            matchday,
            forecast=projection,
            knows_availability=knows_availability,
        )
        total = played["points"]
        if coaches is not None:
            _, earned = pick_coach(
                coaches,
                matchday,
                forecast=coach_forecasts[matchday] if coach_forecasts else None,
            )
            total += earned
            coach_points_won += earned
        per_round.append(total)
        substitutions += played["substitutions"]

    summary = _summarise(per_round, substitutions, transfers=transfers, final=list(squad))
    summary["coach_points"] = round(coach_points_won, 1)
    return summary


def settle_transfers(
    transfers: Sequence[Mapping[str, Any]],
    history: Mapping[str, Mapping[int, float]],
    matchdays: Sequence[int],
) -> list[dict[str, Any]]:
    """What each swap was actually worth, once the season had finished.

    The projected gain is what the model believed; this is what happened. Both
    are kept, because a strategy that is right on average while being wrong
    loudly and often is a different thing from one that is quietly right, and
    only the pair of numbers tells them apart.
    """
    settled = []
    for transfer in transfers:
        after = [m for m in matchdays if m >= transfer["matchday"]]
        came_in = sum(history.get(transfer["in_id"], {}).get(m, ABSENT) for m in after)
        went_out = sum(history.get(transfer["out_id"], {}).get(m, ABSENT) for m in after)
        settled.append(
            {
                **{k: v for k, v in transfer.items() if not k.endswith("_id")},
                "actual_gain": round(came_in - went_out, 1),
                "right": came_in > went_out,
            }
        )
    return settled


def _summarise(
    per_round: Sequence[float],
    substitutions: int,
    *,
    transfers: Sequence[Mapping[str, Any]],
    final: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "points": round(sum(per_round), 1),
        "per_round": round(sum(per_round) / len(per_round), 2) if per_round else None,
        "best": round(max(per_round), 1) if per_round else None,
        "worst": round(min(per_round), 1) if per_round else None,
        "rounds": [round(p, 1) for p in per_round],
        "substitutions": substitutions,
        "transfers": list(transfers),
        "final_squad": list(final) if final is not None else None,
    }
