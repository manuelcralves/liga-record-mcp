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

import math
from collections.abc import Collection, Iterable, Sequence
from statistics import mean

from .models import ClubRecord, Fixture, MarketPlayer, Player, Position


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


# --------------------------------------------------------------------------
# Appearances
#
# Liga Record never says who played — but §10.3 pays an unused player -1 a
# round, so the scoring gives it away. Recorded week by week, that builds the
# one signal no free external source could give us: how often a player actually
# takes the field.
#
# One honest limit: a player who took the field and scored exactly -1 is
# indistinguishable from one who sat out. That is uncommon — playing carries an
# editorial rating of roughly 2-3 — and over many rounds the occasional
# misreading washes out, but a single round should not be treated as certain.
# --------------------------------------------------------------------------

PLAYED = "played"
UNUSED = "unused"
NO_MATCH = "no_match"


def last_scored_round(fixtures: Iterable[Fixture]) -> int | None:
    """The round a player's `points_round` refers to."""
    scored = [f.round_number for f in fixtures if f.played]
    return max(scored) if scored else None


def clubs_playing_in(fixtures: Iterable[Fixture], round_number: int) -> set[str]:
    """Clubs with a completed match in that round.

    A postponed fixture is not an absence: those players score 0, not -1, and
    counting it against them would punish the club rather than the player.
    """
    playing: set[str] = set()
    for fixture in fixtures:
        if fixture.round_number == round_number and fixture.played:
            playing.add(fixture.home)
            playing.add(fixture.away)
    return playing


def classify_appearance(points_round: int, club_played: bool) -> str:
    """Whether a player took the field, from their round score alone."""
    if not club_played:
        return NO_MATCH
    return UNUSED if points_round == -1 else PLAYED


def appearance_rate(history: dict[str, str]) -> float | None:
    """Share of a player's club's matches in which he actually played.

    Rounds where the club had no match are excluded from the denominator, so a
    postponement never looks like being dropped.
    """
    counted = [v for v in history.values() if v != NO_MATCH]
    if not counted:
        return None
    return sum(1 for v in counted if v == PLAYED) / len(counted)


# --------------------------------------------------------------------------
# Projection
#
# Two matches of form is a thin basis for a season, so a projection blends what
# has been seen with a prior built from things that are known more reliably:
# the club's record over completed seasons, and the price Record itself set
# before a ball was kicked.
#
# Every constant here is a judgement call, named and bounded so it can be
# argued with rather than hidden.
# --------------------------------------------------------------------------

#: How many matches the prior is worth. The variance decomposition on two
#: rounds suggests roughly 1, but that measures week-to-week consistency —
#: largely "does he play" — rather than how well a hot start predicts a season.
#: 4 is deliberately more conservative than the data alone would justify.
PRIOR_STRENGTH = 4.0

#: Price correlates with points at about r = 0.30, so it is a real signal but a
#: weak one. A player at twice his position's average price is credited with
#: 25% more, not 100%.
PRICE_SENSITIVITY = 0.25

CLUB_FACTOR_BOUNDS = (0.6, 1.6)
PRICE_FACTOR_BOUNDS = (0.7, 1.8)

#: Keepers and defenders live off clean sheets; midfielders and forwards off
#: goals scored. The club record is read from whichever end matters.
DEFENSIVE = (Position.GK, Position.DEF)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(low, min(high, value))


def position_baselines(
    players: Sequence[Player], matches_by_club: dict[str, int]
) -> dict[Position, float]:
    """Mean points per match by position, over players who have actually played.

    Players who never took the field are excluded: including their -1 would
    drag every baseline down and describe the bench rather than the league.
    """
    gathered: dict[Position, list[float]] = {}
    for player in players:
        matches = matches_by_club.get(player.club, 0)
        if matches <= 0 or never_played(player, matches):
            continue
        gathered.setdefault(player.position, []).append(player.points_total / matches)
    return {pos: mean(rates) for pos, rates in gathered.items() if rates}


def club_factor(
    record: ClubRecord | None,
    position: Position,
    league_goals_against: float,
    league_goals_for: float,
) -> tuple[float, bool]:
    """How much a club's history lifts or lowers a player in this position.

    Returns the factor and whether it rests on real history. A promoted club
    gets 1.0 and False — neutral, and honestly labelled, rather than an
    invented average.
    """
    if record is None or not record.has_history:
        return 1.0, False

    if position in DEFENSIVE:
        conceded = record.goals_against_per_match or league_goals_against
        if conceded <= 0:
            return CLUB_FACTOR_BOUNDS[1], True
        return _clamp(league_goals_against / conceded, CLUB_FACTOR_BOUNDS), True

    scored = record.goals_for_per_match or league_goals_for
    if league_goals_for <= 0:
        return 1.0, True
    return _clamp(scored / league_goals_for, CLUB_FACTOR_BOUNDS), True


def club_price_index(
    players: Sequence[Player], league_mean_value: float
) -> dict[str, float]:
    """How expensive each club's squad is, relative to the league.

    Needed to stop the projection counting one thing twice. A player's price
    and his club's strength correlate at about r = 0.62 — expensive players
    play for good clubs — so multiplying a raw price factor by a club factor
    credits the same fact twice over.
    """
    if league_mean_value <= 0:
        return {}
    by_club: dict[str, list[int]] = {}
    for player in players:
        by_club.setdefault(player.club, []).append(player.value)
    return {club: mean(vals) / league_mean_value for club, vals in by_club.items() if vals}


def price_factor(
    value: int, position_mean_value: float, club_index: float = 1.0
) -> float:
    """Record's own valuation, damped, and net of the player's club.

    Dividing by `club_index` leaves only the part of a price that is about this
    player rather than about his club — "expensive for a Sporting player" is
    information; "expensive because he plays for Sporting" is already in the
    club factor.
    """
    if position_mean_value <= 0:
        return 1.0
    ratio = (value / position_mean_value) / max(club_index, 0.1)
    return _clamp(1 + PRICE_SENSITIVITY * (ratio - 1), PRICE_FACTOR_BOUNDS)


def project(
    player: Player,
    matches: int,
    record: ClubRecord | None,
    baselines: dict[Position, float],
    position_mean_value: float,
    league_goals_against: float,
    league_goals_for: float,
    *,
    club_index: float = 1.0,
    prior_strength: float = PRIOR_STRENGTH,
    rounds_remaining: int | None = None,
) -> dict[str, object]:
    """Blend observed form with a prior, and show the working.

    The components are returned alongside the answer so the number can be
    explained — an unexplained projection is worse than none, because it
    cannot be argued with.
    """
    observed = per_match(player.points_total, matches)
    baseline = baselines.get(player.position, 0.0)
    club, has_history = club_factor(
        record, player.position, league_goals_against, league_goals_for
    )
    price = price_factor(player.value, position_mean_value, club_index)
    prior = baseline * club * price

    if observed is None:
        projected = prior
        weight = 0.0
    else:
        weight = matches / (matches + prior_strength)
        projected = weight * observed + (1 - weight) * prior

    out: dict[str, object] = {
        "id": player.id,
        "name": player.name,
        "position": player.position.value,
        "club": player.club,
        "value": player.value,
        "matches_played": matches,
        "observed_rate": None if observed is None else round(observed, 2),
        "prior_rate": round(prior, 2),
        "projected_rate": round(projected, 2),
        "weight_on_form": round(weight, 2),
        "components": {
            "position_baseline": round(baseline, 2),
            "club_factor": round(club, 2),
            "price_factor": round(price, 2),
        },
        "club_has_history": has_history,
        "never_played": never_played(player, matches),
    }
    if rounds_remaining is not None:
        out["projected_remaining"] = round(projected * rounds_remaining)
    return out


# --------------------------------------------------------------------------
# Fixture adjustment
#
# `project` returns a season rate: it knows how strong a club is, not who the
# club plays this week. Over a whole season that averages out, but a team sheet
# is set one round at a time, and a defender away at Porto is not the same bet
# as the same defender at home to the weakest attack in the league.
#
# The split below matters more than the arithmetic. A player collects a
# roughly fixed amount for simply turning out — the editorial rating, measured
# at about 2 points a match — and the opponent barely moves it. Everything
# else rides on the result. Only that second part is scaled, so a hard fixture
# dents a projection instead of erasing it.
# --------------------------------------------------------------------------

#: Points a player collects for appearing at all, largely the editorial rating.
#: Measured at 2-3 per match played; the low end is used so the adjustment is
#: applied to more of the projection rather than less.
APPEARANCE_FLOOR = 2.0

#: Goals scored at home and away, relative to a neutral venue.
HOME_GOAL_FACTOR = 1.10
AWAY_GOAL_FACTOR = 0.91

#: How far a single fixture may move the result-dependent part. Two completed
#: seasons of club form is not enough to justify wider swings than this.
FIXTURE_BOUNDS = (0.55, 1.75)

#: How much of a position's result-dependent points ride on the defence rather
#: than the attack. Knowing only the club explains r-squared 0.76 of a
#: defender's rate but only 0.31 of a midfielder's, so the split is heavily
#: weighted at the back and barely applied up front.
DEFENSIVE_SHARE = {
    Position.GK: 1.0,
    Position.DEF: 0.85,
    Position.MID: 0.5,
    Position.FWD: 0.1,
}


def fixture_multipliers(
    club_goals_against: float,
    club_goals_for: float,
    opponent_goals_against: float,
    opponent_goals_for: float,
    league_goals_against: float,
    league_goals_for: float,
    *,
    at_home: bool,
    bounds: tuple[float, float] = FIXTURE_BOUNDS,
) -> tuple[float, float]:
    """Return (defensive, attacking) multipliers for one fixture.

    Both are expressed against the club's own average fixture, so a value of 1
    means "no easier or harder than this club's normal week". The defensive
    side compares clean-sheet odds under a Poisson reading of expected goals
    conceded; the attacking side compares expected goals scored directly.

    Callers pass league averages for clubs with no history — a promoted side
    has no record to read, and guessing one would be worse than admitting it.
    """
    own_adjustment = HOME_GOAL_FACTOR if at_home else AWAY_GOAL_FACTOR
    opponent_adjustment = AWAY_GOAL_FACTOR if at_home else HOME_GOAL_FACTOR

    expected_against = (
        club_goals_against
        * (opponent_goals_for / league_goals_for)
        * opponent_adjustment
    )
    expected_for = (
        club_goals_for
        * (opponent_goals_against / league_goals_against)
        * own_adjustment
    )

    defensive = math.exp(-expected_against) / math.exp(-club_goals_against)
    attacking = expected_for / club_goals_for
    return _clamp(defensive, bounds), _clamp(attacking, bounds)


def adjust_for_fixture(
    projected_rate: float,
    position: Position,
    defensive: float,
    attacking: float,
    *,
    floor: float = APPEARANCE_FLOOR,
) -> float:
    """Rescale a season rate for one round's opponent.

    The floor is never scaled: a player who turns out collects it whoever the
    opponent is. Only what is left over — the clean sheets, the goals, the
    margin — moves with the fixture.
    """
    share = DEFENSIVE_SHARE[position]
    multiplier = share * defensive + (1 - share) * attacking
    return floor + max(0.0, projected_rate - floor) * multiplier


# --------------------------------------------------------------------------
# Differentials
#
# Ownership is the strongest single predictor in the market. Measured over 498
# players after two rounds, mean points per match by ownership band:
#
#     0-1%    -0.15      15-30%    3.45
#     1-5%     1.08      30%+      5.33
#     5-15%    2.84
#
# Monotonic and steep — the crowd is right. Which is exactly why "buy what
# nobody owns" is a losing strategy: the bottom band scores below nothing.
#
# But a squad built only from the top band moves with the pack. Owning what
# everyone owns cannot close a gap on someone above you; it locks the gap in.
# The useful question is not who is under-owned, it is who is under-owned
# *relative to what they are producing* — the residual, not the level.
# --------------------------------------------------------------------------

#: Ownership is roughly linear in the log, not in the raw percentage: the step
#: from 1% to 5% carries far more information than 40% to 44%.
def _log_ownership(percent: float) -> float:
    return math.log1p(max(0.0, percent))


#: Below this many matches a rate is one good afternoon, not a pattern.
MIN_MATCHES_FOR_RESIDUAL = 1


def _fit(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Least squares through (x, y), returning (intercept, slope)."""
    if len(points) < 2:
        return 0.0, 0.0
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    if spread == 0:
        return mean_y, 0.0
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
    return mean_y - slope * mean_x, slope


def ownership_baseline(
    players: Sequence[MarketPlayer], matches_by_club: dict[str, int]
) -> dict[Position, tuple[float, float]]:
    """Fit rate against log-ownership, once per position.

    Fitting all four positions together looks reasonable and is not: forwards
    out-score defenders at every ownership level, so a single line puts every
    forward above it and every defender below. The first run of this ranked
    eight forwards in the top eight — a position table wearing the costume of a
    discovery. Each position now gets its own line, so a defender is only ever
    measured against other defenders as widely owned as he is.

    Players whose club has not played are excluded: they have no rate, and
    entering them at zero would drag every line down.
    """
    gathered: dict[Position, list[tuple[float, float]]] = {}
    for player in players:
        matches = matches_by_club.get(player.club, 0)
        rate = per_match(player.points_total, matches)
        if rate is not None:
            gathered.setdefault(player.position, []).append(
                (_log_ownership(player.owned_percent), rate)
            )
    return {position: _fit(points) for position, points in gathered.items()}


def expected_rate(owned_percent: float, baseline: tuple[float, float]) -> float:
    """What the market's pricing of attention implies this player should score."""
    intercept, slope = baseline
    return intercept + slope * _log_ownership(owned_percent)


def differential_rows(
    players: Sequence[MarketPlayer],
    matches_by_club: dict[str, int],
    *,
    baseline: dict[Position, tuple[float, float]] | None = None,
    exclude: Collection[str] = (),
    min_matches: int = MIN_MATCHES_FOR_RESIDUAL,
) -> list[dict[str, object]]:
    """Players ranked by how far they beat their ownership, best first.

    A positive `residual` means producing more than this level of ownership
    normally buys. That is the only kind of signal that can close a gap on
    someone ahead of you — matching their squad can only preserve it.

    The residual is not a recommendation on its own. Two rounds of football is
    a thin basis, and a high residual on one match played is noise wearing a
    number, so `matches_played` travels with every row.
    """
    fitted = baseline if baseline is not None else ownership_baseline(
        players, matches_by_club
    )
    rows: list[dict[str, object]] = []
    for player in players:
        if player.id in exclude:
            continue
        line = fitted.get(player.position)
        if line is None:
            continue
        matches = matches_by_club.get(player.club, 0)
        rate = per_match(player.points_total, matches)
        if rate is None or matches < min_matches or never_played(player, matches):
            continue
        implied = expected_rate(player.owned_percent, line)
        rows.append(
            {
                "id": player.id,
                "name": player.name,
                "position": player.position.value,
                "club": player.club,
                "value": player.value,
                "owned_percent": round(player.owned_percent, 2),
                "matches_played": matches,
                "observed_rate": round(rate, 2),
                "expected_rate": round(implied, 2),
                "residual": round(rate - implied, 2),
            }
        )
    rows.sort(key=lambda r: (-r["residual"], r["id"]))
    return rows


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
