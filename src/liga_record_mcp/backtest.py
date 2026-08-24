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
    STARTER_MINUTES,
    PRIOR_STRENGTH,
    ROTATION_PRIOR,
    ROTATION_WINDOW,
    UNUSED_PENALTY,
    adjust_for_fixture,
    fixture_multipliers,
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
    by_minutes: bool = False,
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

    MINUTES HAVE NOW BEEN TRIED ON BOTH HALVES AND BOTH ARE WORSE, which is
    worth more than either result alone. `minutes_weighted` scales what he
    RETURNS; `by_minutes` counts a part-appearance as a fraction on the half
    that decides whether he PLAYS. The ridge's block ablation says the
    information is real — dropping recent minutes costs it more than dropping
    any other block — so the fault is in the shape, twice:

                             2025/26   +archive   2024/25
        by_minutes           -0.0414    -0.0415   -0.0509

    The reason is the same reason both times. `playing` multiplies `returns`,
    and a substitute's low returns ALREADY carry the fact that he is a
    substitute. Scaling either half by minutes charges him for it twice.

    Whatever the ridge is extracting, it is not a shrinkage. It stays open.

    THE POSITION BLOCK WAS THE OTHER LEAD AND IT CLOSES. The estimator carries
    a real, structural bias by position — it over-rates goalkeepers by roughly
    four tenths of a point a round, on every configuration:

                             GK       DEF      MID      FWD
        2025/26            -0.430   +0.017   +0.053   +0.078
        2025/26 + archive  -0.176   +0.038   +0.070   +0.123
        2024/25            -0.370   +0.030   +0.024   +0.041

    Correcting it walk-forward, from the bias seen in earlier rounds only, is
    worth +0.0013, +0.0001 and +0.0003 — nothing. Correcting all four is worse
    than correcting the keepers alone.

    The reason is worth keeping: a constant offset per position cannot reorder
    anyone WITHIN that position, and one goalkeeper is picked from eighteen who
    are ranked against each other. The ridge gains on pooled correlation, where
    every position sits in one column together. A team sheet does not.

    `minutes_weighted` is off by default because it measures WORSE. Predicting each round from
    what came before, over two seasons and about ten thousand player-rounds, it
    loses to the plain split in both (1.601 against 1.530, and 1.592 against
    1.518). The idea is sound and the estimate is too noisy to pay for itself.
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

    # Over the matchdays each player ACTUALLY HAS, not a fixed range. Within one
    # season the two are the same, because every player is padded to the full
    # calendar — a round he was left out of is a real -1 and has to be there.
    # Across two, they are not: a player who was in another league last season
    # has no rounds then, and counting those absences as "did not play" would
    # make every newcomer look like a substitute who never gets on.
    appearances = [
        1.0 if played > 0 else 0.0
        for by_matchday in minutes.values()
        for m, played in by_matchday.items()
        if m < upto
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
        # HOW MUCH OF A MATCH, not merely whether. A man who came on at eighty
        # counts as a full appearance here, and next week he is not the same
        # bet as the one who played ninety — the block ablation puts the
        # ridge's largest single loss on exactly this information, and the
        # split has never used it on THIS half.
        #
        # The earlier attempt scaled what he RETURNS by minutes and measured
        # worse. Minutes are not about how much he does when he plays; they
        # are about how much of the next match he is likely to get, which is
        # the other half entirely.
        def share(matchday: int) -> float:
            played = his_minutes.get(matchday, 0)
            if not played:
                return 0.0
            return min(1.0, played / STARTER_MINUTES) if by_minutes else 1.0

        recent_rate = (
            sum(share(m) for m in recent) / len(recent)
            if recent
            else league_availability
        )
        season_rate = (
            sum(share(m) for m in on_the_field) / len(earlier)
            if earlier
            else league_availability
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


#: Yellows that earn a one-match ban. NOT looked up — MEASURED, and the data
#: is unambiguous. Crossing a multiple of five takes a player from the 86% who
#: play the following round down to 22% and 10% on the two seasons. Three, four
#: and six do nothing at all: 82/83, 82/90 and 80/79 against that same 86%. One
#: threshold produces a cliff and the neighbours are flat, which is what an
#: actual rule looks like from the outside.
YELLOWS_PER_BAN = 5

#: What a suspended player's chance of playing is set to, rather than zero.
#:
#: Zero is what the regulation says and 0.09 and 0.18 are what the seasons say
#: — reds and fifth-yellows respectively, pooled 0.15. The gap is not clemency.
#: It is the round NUMBERING: a postponed match makes "round 13" a fixture
#: played in March, and the ban falls on the next match the club actually
#: plays, not on the next number. This project has been caught by postponed
#: fixtures before.
#:
#: AND THERE IS NOTHING HERE TO TUNE, which was worth checking rather than
#: asserting. Swept from 0.0 to 0.30 — from the regulation's own answer to
#: twice the observed rate — the correlation moves less than the +0.003 this
#: project calls nothing that survives:
#:
#:                          base     0.0    0.08    0.15    0.19    0.30
#:      2025/26           0.5171  0.5386  0.5387  0.5386  0.5385  0.5375
#:      2024/25           0.5201  0.5514  0.5510  0.5505  0.5500  0.5485
#:      2025/26 + archive 0.5225  0.5439  0.5442  0.5442  0.5441  0.5433
#:
#: The whole of the gain is in KNOWING he is banned. What number that knowledge
#: is written down as is beneath the noise, so this is a constant with no
#: season behind it and none needed — which is the only honest way to have one.
SUSPENDED_PLAYS = 0.15


def card_table(
    seasons: Sequence[tuple[Mapping[str, Any], int]],
) -> dict[tuple[int, str], tuple[int, bool]]:
    """Bookings per player per round, from the reconstruction.

    Every player-match row already carries `yellow_cards` and `red_card`, and
    the model has never read either. They are used to SCORE a round — §10.3(g)
    pays -3 and -1 — and never to predict the next one, which is where they say
    the most: a man sent off on Saturday is not available on Sunday week, and
    the appearance half of the projection had no way to know it.
    """
    table: dict[tuple[int, str], tuple[int, bool]] = {}
    for players, offset in seasons:
        for player, entry in players.items():
            for match in entry.get("matches") or []:
                table[(int(match["round"]) + offset, player)] = (
                    int(match.get("yellow_cards") or 0),
                    bool(match.get("red_card")),
                )
    return table


def suspensions(
    cards: Mapping[tuple[int, str], tuple[int, bool]],
    upto: int,
    *,
    per_ban: int = YELLOWS_PER_BAN,
) -> set[str]:
    """Who cannot play in round `upto`, from rounds strictly before it.

    Two causes, and both are settled by the round before: a red card, or the
    booking that crosses a multiple of `per_ban`. Nothing here reads round
    `upto` itself, which is the whole point — a suspension is known in advance,
    which is exactly what makes it worth having and what separates it from the
    lineup news that arrives after §6.13's deadline.

    ONE ROUND, and that is measured too. Of the players sent off, 12% and 0%
    played the round after; by the round after that, 82% and 91% were back —
    at or above the 86% baseline. The ban does not linger and neither does this.

    A ban does not cross the turn of the season. The archive sits on matchdays
    at or below zero, so a red card in its last round would otherwise carry
    into the opening day of the season being replayed, which is a different
    competition as far as the punishment is concerned.
    """
    previous = upto - 1
    if previous <= 0 < upto:
        return set()

    # AND THE COUNT RESTARTS WITH THE SEASON. Bookings do not carry over, and
    # the archive lies on matchdays at or below zero, so a total swept through
    # the boundary put a man on four yellows from last year one card from a ban
    # in August — which is not a rule in any competition.
    #
    # It was not caught by reading. It was caught by measuring: the signal came
    # out at +0.0172 and +0.0211 on the two seasons alone and at MINUS 0.0015
    # with the archive attached, which is the shape of a bug and not of a weak
    # effect. Running the archive as a third configuration is what made the
    # sign flip visible at all.
    in_season = (
        (lambda matchday: matchday >= 1)
        if previous >= 1
        else (lambda matchday: matchday <= 0)
    )

    running: dict[str, int] = {}
    banned: set[str] = set()
    for (matchday, player), (yellows, red) in sorted(cards.items()):
        if matchday >= upto or not in_season(matchday):
            continue
        before = running.get(player, 0)
        running[player] = before + yellows
        if matchday != previous:
            continue
        if red or before // per_ban < running[player] // per_ban:
            banned.add(player)
    return banned


def fixture_table(
    seasons: Sequence[tuple[Mapping[str, Any], int]],
) -> dict[tuple[int, str], tuple[str, bool, int, int]]:
    """Who played whom, where, and how it finished — from the reconstruction.

    The calendar is already on file. Every player-match row carries `club`,
    `opponent`, `at_home`, `scored` and `conceded`, and both loaders read the
    row and throw all five away. Recovering them costs nothing and needs no
    second source, which matters more than it sounds: the obvious alternative,
    `history.club_records()`, SUMS the seasons it is given. It reports 68
    matches for a club, so a decision taken at matchday 6 would be reading all
    thirty-four rounds of the season being replayed. That is not a subtle leak,
    it is the whole answer — and it silently drops two of the eighteen clubs,
    whose names are absent from its lookup. The rows here are in the same
    namespace as `cells`, so nothing has to be translated at all.

    `seasons` is (players, offset) pairs, so the caller keeps the archive
    convention to itself exactly as it does for points and minutes.

    A round is settled by MAJORITY across the players who reported it. A cup
    tie can share a league round number — 2024/25 has four of them — and
    last-writer-wins would put Sporting away at a third-tier side in round 4.
    Eleven team-mates outvote the two who played the cup.

    Then the fixture is MIRRORED: if the file says A were home to B, B were
    away to A, with the goals the other way about. Clubs whose players are
    barely in the market appear on a handful of rounds otherwise, and their
    opponents can see them even when they cannot see themselves.
    """
    votes: dict[tuple[int, str], dict[tuple[str, bool, int, int], int]] = {}
    for players, offset in seasons:
        for entry in players.values():
            for match in entry.get("matches") or []:
                club, opponent = match.get("club"), match.get("opponent")
                if not club or not opponent:
                    continue
                key = (int(match["round"]) + offset, club)
                fixture = (
                    opponent,
                    bool(match.get("at_home")),
                    int(match.get("scored") or 0),
                    int(match.get("conceded") or 0),
                )
                votes.setdefault(key, {})[fixture] = (
                    votes.setdefault(key, {}).get(fixture, 0) + 1
                )

    # Ties in the vote fall to the fixture that sorts first, for the same
    # reason every other tie-break here does: an arbitrary rule written down
    # beats an arbitrary rule left to whichever player was read first.
    table = {
        key: max(sorted(counted), key=lambda f: counted[f])
        for key, counted in votes.items()
    }

    for (matchday, club), (opponent, at_home, scored, conceded) in sorted(
        table.items()
    ):
        table.setdefault(
            (matchday, opponent), (club, not at_home, conceded, scored)
        )
    return table


def club_form_upto(
    table: Mapping[tuple[int, str], tuple[str, bool, int, int]],
    upto: int,
) -> tuple[dict[str, tuple[float, float]], float, float]:
    """Goals against and for, per match, from rounds STRICTLY before `upto`.

    The invariant that matters, and the only one worth stating twice: a
    fixture's `opponent` and `at_home` may be read for round `upto` itself,
    because the calendar is published weeks ahead and knowing who you play on
    Saturday is not foresight. Its `scored` and `conceded` may not, and this
    function never looks at them — the caller reads the venue from `table`
    directly and gets the form from here.

    Returns per-club rates and the two league means. The means are the mean of
    the per-club rates rather than of all matches, matching how the live path
    computes them, and they stand in for a club with no record at all — a
    promoted side has none, and inventing one would be worse than admitting it.
    """
    against: dict[str, list[int]] = {}
    scored_by: dict[str, list[int]] = {}
    for (matchday, club), (_, _, scored, conceded) in table.items():
        if matchday >= upto:
            continue
        against.setdefault(club, []).append(conceded)
        scored_by.setdefault(club, []).append(scored)

    rates = {
        club: (mean(against[club]), mean(scored_by[club]))
        for club in sorted(against)
        if against[club]
    }
    if not rates:
        return {}, 1.0, 1.0
    league_against = mean([ga for ga, _ in rates.values()])
    league_for = mean([gf for _, gf in rates.values()])
    # A league that has conceded nothing has not kicked off. Guarding here
    # rather than at every division site keeps the arithmetic below readable.
    return rates, max(league_against, 1e-9), max(league_for, 1e-9)


def position_bias(
    points: Mapping[str, Mapping[int, float]],
    cells: Mapping[str, tuple[str, str]],
    seen: Mapping[int, Mapping[str, float]],
) -> dict[Position, float]:
    """How far each position has been out, over the rounds already played.

    The shrinkage pools a player with his club and position, so a keeper is
    compared against keepers at his club — but nothing ever corrects keepers as
    a whole against forwards as a whole. A ridge given position as a plain
    one-hot found +0.0077, +0.0030 and +0.0033 of correlation in that gap,
    which is the second largest thing it found and the cheapest to close.

    This is the closed form of exactly that: the mean residual per position,
    and nothing else. It has NO CONSTANT TO SET, which is most of the argument
    for preferring it — five hand-tuned numbers on two seasons is thin, and a
    quantity read off the past is not tuned at all.

    `seen` is what was ALREADY PREDICTED, round by round, before the round being
    asked about: {matchday: {player: what we said he would score}}. Passing it
    in rather than recomputing it is not an optimisation, it is the invariant.
    An estimate for round 7 has to be the estimate that was made at round 7,
    from data before it; rebuilding it later with what is known now would score
    the model against rounds it had already been fitted on, and the bias would
    come back flattering and useless.
    """
    residuals: dict[Position, list[float]] = {position: [] for position in Position}
    for matchday, view in seen.items():
        for player, estimate in view.items():
            actual = points.get(player, {}).get(matchday)
            cell = cells.get(player)
            if actual is None or cell is None:
                continue
            residuals[Position(cell[1])].append(actual - estimate)
    return {
        position: (mean(values) if values else 0.0)
        for position, values in residuals.items()
    }


def adjusted_projection(
    points: Mapping[str, Mapping[int, float]],
    minutes: Mapping[str, Mapping[int, int]],
    cells: Mapping[str, tuple[str, str]],
    *,
    upto: int,
    fixtures: Mapping[tuple[int, str], tuple[str, bool, int, int]] | None = None,
    cards: Mapping[tuple[int, str], tuple[int, bool]] | None = None,
    suspended: float = SUSPENDED_PLAYS,
    shares: Mapping[Position, float] | None = None,
    floor: float | None = None,
    strength: float | None = None,
    bias: Mapping[Position, float] | None = None,
    **kwargs: Any,
) -> dict[str, float]:
    """`two_part_projection`, with what is already known about the round in it.

    Two things are knowable before §6.13's deadline and neither was being used,
    and they land on OPPOSITE HALVES of the split — which is the best argument
    the split has ever had for existing.

    Who a club plays moves what a man RETURNS when he takes the field. Whether
    he is suspended moves whether he TAKES it at all. Folded into one number
    they would fight: an easy fixture would argue for a man who is banned.
    Kept apart, each says its piece to the half it knows about.

    §10.3(i) pays the same -1 whoever the opponent is, so the fixture never
    touches the blend — only `returns`, and only above the appearance floor. A
    suspension never touches `returns` either: what a man is worth on the days
    he plays has nothing to do with his not playing this one.

    Both arguments are optional, and with neither this is `two_part_projection`
    with an extra dictionary comprehension.
    """
    playing, returns = two_part_projection(
        points, minutes, cells, upto=upto, parts=True, **kwargs
    )

    if cards is not None:
        for player in suspensions(cards, upto):
            if player in playing:
                playing[player] = suspended

    if fixtures is not None:
        rates, league_against, league_for = club_form_upto(fixtures, upto)
        for player, rate in returns.items():
            cell = cells.get(player)
            fixture = fixtures.get((upto, cell[0])) if cell else None
            if fixture is None or cell is None:
                continue
            opponent, at_home, _, _ = fixture
            club_against, club_for = rates.get(cell[0], (league_against, league_for))
            opponent_against, opponent_for = rates.get(
                opponent, (league_against, league_for)
            )
            defensive, attacking = fixture_multipliers(
                max(club_against, 1e-9),
                max(club_for, 1e-9),
                opponent_against,
                opponent_for,
                league_against,
                league_for,
                at_home=at_home,
            )
            position = Position(cell[1])
            returns[player] = adjust_for_fixture(
                rate,
                position,
                defensive,
                attacking,
                share=None if shares is None else shares[position],
                **({} if floor is None else {"floor": floor}),
                **({} if strength is None else {"strength": strength}),
            )

    blended = {
        player: playing[player] * rate + (1 - playing[player]) * ABSENT
        for player, rate in returns.items()
    }
    if bias is not None:
        for player, value in blended.items():
            cell = cells.get(player)
            if cell is not None:
                blended[player] = value + bias.get(Position(cell[1]), 0.0)
    return blended


def fixture_adjusted_projection(
    points: Mapping[str, Mapping[int, float]],
    minutes: Mapping[str, Mapping[int, int]],
    cells: Mapping[str, tuple[str, str]],
    table: Mapping[tuple[int, str], tuple[str, bool, int, int]],
    *,
    upto: int,
    **kwargs: Any,
) -> dict[str, float]:
    """The opponent alone, for callers that have no card table to give."""
    return adjusted_projection(
        points, minutes, cells, upto=upto, fixtures=table, **kwargs
    )

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
    # In id order, NOT in the order the squad was handed over. Exact ties are
    # not the rare curiosity they look like: two substitutes of the same
    # position, replaced by the same incoming player, leave the SAME eleven, so
    # the gain agrees to the last bit and the winner is decided by whoever came
    # first in the list. It happened on matchdays 6, 7 and 20 of 2025/26 — sell
    # Dedic or sell Lagerbielke, both worth 57.38924458 — and the two are not
    # interchangeable afterwards, because the one sold can never be bought back.
    # Once the squad optimiser was made order-free, this was the whole of what
    # was left: identical twenty-threes, seasons 38 points apart.
    for position, out_id in ((market[i].position, i) for i in sorted(squad_ids)):
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
