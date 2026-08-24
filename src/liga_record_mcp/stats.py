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
from collections.abc import Collection, Iterable, Mapping, Sequence
from statistics import mean, pstdev
from typing import Any

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

#: How many matches the prior is worth.
#:
#: This was 4.0 and chosen rather than measured — there was no past season to
#: measure against, since Liga Record publishes none. There is one now,
#: reconstructed match by match, and it says 4 was too low.
#:
#: Standing at the end of every round from 3 to 26 and predicting the rest of
#: the season, over 4300 player-splits (mean absolute error, points per round):
#:
#:      trusting his own record alone      0.938
#:      K = 4, as it was                   0.855
#:      K = 10, as it is                   0.839
#:      K = 12, the best                   0.838
#:      trusting the club alone            0.851
#:
#: The finding is not the optimum, which is flat anywhere from 8 to 20 and puts
#: 10 and 12 a thousandth apart. It is that a player's own season to date is
#: WORSE evidence than what a player at his club in his position typically
#: returns — 0.938 against 0.851. Individual fantasy scoring is far noisier
#: than it looks, and a hot start is mostly not about the player.
#:
#: Repeated on the objective half alone, which is computed from events and
#: contains no estimate at all, the same holds and harder: 0.514 against 0.464,
#: best at 20. So the reconstruction's estimated mark is not producing this.
#:
#: CONFIRMED ON A SECOND SEASON. 2024/25 was reconstructed afterwards and
#: nothing was tuned on it. Over 2433 player-splits: own record 1.083, K=4
#: 0.974, K=10 0.947, K=12 0.944, the club alone 0.957. Same ordering, same
#: optimum, same finding — a player's own season is the weaker evidence.
#:
#: 12 is where a second, independent method lands. `scripts/tune.py` searches
#: this and the two rotation constants together, tuning on one season and
#: reporting on the other exactly once — and picks 12 tuning on 2024/25 and 16
#: tuning on 2025/26, either of which beats 10 on the season it was not shown.
#: 12 is the conservative half of that, and inside the flat region above.
#:
#: LEFT ALONE when the correlation search preferred about 6, because that
#: dimension is flat there — 2 through 16 span 0.5166 to 0.5172, six ten-
#: thousandths — while 12 has a separate measurement behind it on a different
#: question. Changing a constant across a flat is churn dressed as evidence.
PRIOR_STRENGTH = 12.0

#: Rounds in the window that decides whether a man is currently in the team.
#:
#: Five was the guess — a month of football, long enough not to be one
#: afternoon. Three is the measurement. `scripts/tune.py` searches the grid on
#: one season and reports on the other, and BOTH directions choose 3
#: independently, which is the strongest form the evidence takes here: a value
#: found twice, each time without seeing the season it was judged on.
#:
#: It says react faster to who is playing, and it agrees with everything else
#: measured about this signal — being dropped is news, and news goes stale.
#:
#: Two, after the search was pointed at the right target. Tuned on mean error
#: it wanted a window of ONE — and that was mean error being gamed rather than
#: the model improving: about half the market sits out a given round, so saying
#: -1 confidently about anyone absent last week is exactly right very often.
#: It cost correlation on both seasons, and correlation is what picks an eleven.
#:
#: Scored on correlation instead, both seasons put the peak at 2 with 1 and 3
#: below it on either side — an interior optimum, found twice, each time
#: without seeing the season it was judged on.
ROTATION_WINDOW = 2

#: How many rounds the season-long appearance rate is worth against that
#: window. Far lower than PRIOR_STRENGTH, and deliberately: whether a player is
#: in the side changes much faster than how well he plays when he is, so the
#: two halves of a projection should not be smoothed at the same speed.
#:
#: Splitting them was measured directly, by predicting each round from what
#: came before it and comparing with what happened. Mean absolute error per
#: player-round, over two seasons and roughly ten thousand predictions:
#:
#:                                2025/26   2024/25
#:      the league average          2.151     2.204
#:      form alone                  1.776     1.799
#:      plays x returns             1.530     1.518
#:      plays x returns, minutes    1.601     1.592
#:
#: The split is worth about a tenth of the remaining error over form alone, and
#: it lands on the same number in a season nothing was tuned on.
#:
#: THE MINUTES WEIGHTING IS WORSE, in both. An earlier reading had it winning,
#: from six squad-and-threshold comparisons in a transfer backtest; this is ten
#: thousand predictions and it says the opposite. Six noisy comparisons should
#: not have been believed over that, and the option stays off by default.
#: Four was the guess, two the first measurement, and one is where it lands
#: once the search scores what actually matters. Interior on both seasons: 0.5
#: and 1.5 are both worse. Same conclusion as the window — whether a man is in
#: the side changes fast, and the estimate should not fight it.
ROTATION_PRIOR = 1.0

#: How many rounds the league's own appearance rate is worth against a
#: player's. Small, because a player's record is the better evidence as soon as
#: there is any — but not zero, or two appearances would read as certainty.
APPEARANCE_PRIOR = 3.0

#: A start, as opposed to a man who came on at the end. §10.3(h) uses the same
#: threshold for docking a blank forward, which is as close to an official
#: definition of "played properly" as the regulation gives.
STARTER_MINUTES = 75


def recent_appearance_rate(
    history: dict[str, str], *, window: int = ROTATION_WINDOW
) -> tuple[float, int] | None:
    """How often he played in the last `window` rounds his club had a match.

    Returns the rate and how many rounds it rests on, because a rate from two
    rounds and a rate from five are not the same claim and the caller has to
    weight them differently.

    Rounds where the club did not play are skipped rather than counted as
    absences — a postponement is not a dropping, and the whole point of this
    signal is to tell those apart.
    """
    counted = [
        (int(rnd), status)
        for rnd, status in history.items()
        if status != NO_MATCH
    ]
    if not counted:
        return None
    counted.sort()
    lately = counted[-window:]
    return sum(1 for _, status in lately if status == PLAYED) / len(lately), len(lately)


def availability(
    history: dict[str, str] | None,
    *,
    league_rate: float,
    window: int = ROTATION_WINDOW,
    rotation_prior: float = ROTATION_PRIOR,
) -> float:
    """The chance he takes the field, weighted toward what has happened lately.

    Three levels, each shrunk into the next: the last few rounds, his own
    season, and the league. A player nobody has a record for gets the league
    rate, which is what "we do not know" should mean and is a long way from
    zero.

    Both shrinkages matter, and the second one caught a real error. Without it,
    a player who has appeared in both of the two rounds on file comes out at a
    flat 100% — and since a certain starter projects at roughly twice a naive
    average, that overconfidence goes straight into the recommendation. Two
    rounds is not proof of anything, so his own rate is pulled toward the
    league's before the recent window is weighed against it.
    """
    if not history:
        return league_rate
    counted = [status for status in history.values() if status != NO_MATCH]
    if not counted:
        return league_rate

    played = sum(1 for status in counted if status == PLAYED)
    season = (played + league_rate * APPEARANCE_PRIOR) / (
        len(counted) + APPEARANCE_PRIOR
    )

    lately = recent_appearance_rate(history, window=window)
    if lately is None:
        return season
    rate, rounds = lately
    return (rate * rounds + season * rotation_prior) / (rounds + rotation_prior)


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
    appearances: int | None = None,
    expected_availability: float | None = None,
    league_availability: float | None = None,
) -> dict[str, object]:
    """Blend observed form with a prior, and show the working.

    The components are returned alongside the answer so the number can be
    explained — an unexplained projection is worse than none, because it
    cannot be argued with.

    Given `appearances` — how many of those matches he was actually used in —
    the estimate splits in two, which is a real improvement rather than a
    refinement:

        expected = P(plays) x (what he returns when he plays) + P(not) x -1

    A season average folds those together, and the fold hides the largest
    single fact about a fantasy footballer. A nailed-on starter returning four
    and a rotation player returning eight half the time both average four, and
    they are not remotely the same bet. Worse, the fold moves slowly: a man who
    has just lost his place keeps months of the points he scored when he had
    it, and the projection goes on recommending him for weeks.

    Measured on the reconstructed season, judging transfers on the folded
    average was worth nothing at all against never transferring. The split,
    with minutes, won all six squad-and-threshold comparisons.
    """
    baseline = baselines.get(player.position, 0.0)
    club, has_history = club_factor(
        record, player.position, league_goals_against, league_goals_for
    )
    price = price_factor(player.value, position_mean_value, club_index)
    prior = baseline * club * price

    observed = per_match(player.points_total, matches)
    playing = None
    if appearances is not None and matches > 0 and league_availability:
        playing = (
            expected_availability
            if expected_availability is not None
            else appearances / matches
        )
        # The prior is a rate per club match, so it already carries the
        # league's absences. Taking it back out is what makes it comparable
        # with a per-appearance return rather than half a size too small.
        prior = (prior - (1 - league_availability) * UNUSED_PENALTY) / league_availability
        # §10.3(i) pays -1 for every round he sat, so adding those back
        # recovers what he scored on the days he played.
        observed = (
            (player.points_total - UNUSED_PENALTY * (matches - appearances)) / appearances
            if appearances > 0
            else None
        )

    if observed is None:
        projected = prior
        weight = 0.0
    else:
        seen = appearances if appearances is not None else matches
        weight = seen / (seen + prior_strength)
        projected = weight * observed + (1 - weight) * prior

    if playing is not None:
        projected = playing * projected + (1 - playing) * UNUSED_PENALTY

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
            **(
                {}
                if playing is None
                else {
                    "chance_of_playing": round(playing, 2),
                    # `prior_rate` above is now a rate per appearance, not per
                    # club match. A player at exactly the league's availability
                    # projects the same either way — the inversion is a change
                    # of units, not a thumb on the scale.
                    "prior_basis": "per appearance",
                    "appearances": appearances,
                    "rate_when_playing": None
                    if observed is None
                    else round(observed, 2),
                }
            ),
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
#: Measured at 2-3 per match played; the low end was taken so the adjustment is
#: applied to more of the projection rather than less.
#:
#: AND THE LOW END WAS STILL TOO HIGH. Swept against the round a player actually
#: had, on within-round correlation over both seasons:
#:
#:                    2025/26   2024/25
#:          0.0        0.5386    0.5536
#:          0.5        0.5403    0.5569
#:          1.0        0.5399    0.5585
#:          1.5        0.5391    0.5574
#:          2.0        0.5349    0.5554     <- what this used to be
#:          2.5        0.5257    0.5505
#:          3.0        0.5129    0.5415
#:
#: An interior optimum, found on each season without seeing the other, and
#: worth +0.0050 and +0.0031 — above the +0.003 tune.py calls the least that
#: survives. What it says is that more of a player's round moves with the
#: opponent than the two-to-three reading suggested: the floor is what NO
#: fixture can touch, and it is smaller than that.
#:
#: The measured number is what is here rather than the argument for it. The
#: original reasoning — collect the rating by turning out — is sound and simply
#: does not settle the size.
APPEARANCE_FLOOR = 1.0

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
#:
#: LEFT ALONE ON PURPOSE, and the search that says to change them is the reason.
#: A coordinate search over all four plus the floor clears tune.py's bar on the
#: held-out season in BOTH directions (+0.0063 and +0.0070) — and picks values
#: that do not agree with each other: GK 0.15 tuning on 2025/26 against 0.00 on
#: 2024/25, FWD 0.45 against 0.75. Two answers that far apart, both scoring
#: well, is a plateau and not a discovery.
#:
#: Separated out, the floor carries most of that gain on its own and is
#: interior on both seasons. These four are what is left over, and changing a
#: constant across a flat is churn dressed as evidence.
#:
#: The plateau has a cause worth knowing before anyone tries again: `defensive`
#: and `attacking` both fall out of the same club-and-opponent strengths, so
#: they move together and the weight between them is barely identified. A
#: search that separates them needs a signal that pulls them apart, not a finer
#: grid over the same two.
#:
#: RE-CHECKED AND UNCHANGED, by scripts/tune_fixture.py, which exists so the
#: numbers above can be re-run instead of taken on trust. Sweeping each position
#: on its own from the values in use — no coordinate order to confound it:
#:
#:                 2025/26              2024/25
#:      GK    0.00 best, 1.00 worst   0.00 best, 1.00 worst   agree, span 0.0004/0.0010
#:      DEF   0.25-0.50 best          0.00-0.25 best          agree, span 0.0008/0.0029
#:      MID   0.25 best               0.50 best               already about right
#:      FWD   0.25 best, falls after  0.75 best, rises to it  OPPOSITE DIRECTIONS
#:
#: Changing only where both seasons agree on the DIRECTION — GK to 0.00, DEF to
#: 0.25 — is worth +0.0009 and +0.0029, under the bar on both. And GK 0.00 says
#: a keeper's return moves only with how likely his own side is to SCORE, which
#: is not a thing that can be true. That is the collinearity above showing its
#: face: with the two multipliers moving together, the search is free to put the
#: weight anywhere and picks on noise.
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
    share: float | None = None,
) -> float:
    """Rescale a season rate for one round's opponent.

    The floor is never scaled: a player who turns out collects it whoever the
    opponent is. Only what is left over — the clean sheets, the goals, the
    margin — moves with the fixture.
    """
    # Overridable so the split can be SEARCHED rather than asserted. The four
    # values below were set by eye, and a ridge handed the same three inputs
    # got more than twice as much out of them.
    if share is None:
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


# --------------------------------------------------------------------------
# The coach
#
# A coach is selected every round and scores every round (§6.15, §6.17), and
# for two rounds this project ignored him entirely — he was checked for
# legality and then left out of every projection, every record and every total.
# The spread is not small: after two rounds the eighteen ran from 14 points
# down to -2, roughly seven points a round between the best and the worst.
#
# His points cannot be reconstructed from the calendar. Fitting them against
# wins, draws, clean sheets, margins and goals gives r-squared 0.85 with errors
# up to 3.8 and no integer structure — if the score were computed from results
# that fit would be exact. The residual behaves like the editorial rating that
# makes up most of a player's score, which no public source publishes.
#
# So the coach is projected here and settled from the hand-maintained file,
# never computed. What is measurable is measured; the rest is admitted.
# --------------------------------------------------------------------------

#: A coach's return rides on the result, which is defence and attack in equal
#: measure — unlike a defender, who lives off the clean sheet alone.
COACH_DEFENSIVE_SHARE = 0.5


# --------------------------------------------------------------------------
# What a coach scores (§14)
# --------------------------------------------------------------------------
#
# A coach is a scoring slot in every single round. §14.4 says his points "será
# somada aos pontos acumulados pelos jogadores escolhidos", §6.17 makes the
# team invalid without one, and §6.4 makes him free — he costs no budget and
# may be changed every round.
#
# So he is the one pick with no constraint on it at all, and until this was
# written the project had a projection for coaches without ever encoding the
# rules that generate their points.

#: §14.3(a) — the result, from the coach's side.
COACH_WIN = 1
COACH_DRAW = 0
COACH_LOSS = -1

#: §14.3(c) — what the goals themselves are worth to him.
COACH_FAILED_TO_SCORE = -1
COACH_CLEAN_SHEET = 1
COACH_BIG_WIN = 1
COACH_BIG_LOSS = -1

#: The margin §14.3(c) calls "mais de 2 golos de diferença".
COACH_BIG_MARGIN = 2

#: §14.3(d) — a goal off the bench is credited to the man who made the change.
COACH_SUBSTITUTE_GOAL = 1

#: §14.3(e) and §14.5 — sent to the stand.
COACH_SENT_OFF = -3


#: Checked against Record's own published coach points, which the team-sheet
#: page shows and which nobody had thought to compare against. Of the eighteen
#: coaches in round 2 of 2026/27, sixteen had a match, and §14.3 accounted for
#: all sixteen: every remainder was a legal §10.1 mark, some with §14.3(d)'s
#: goals off the bench on top. The other two clubs had a postponed fixture.
#:
#: That is the same test the player rules passed, and it is worth more here,
#: because unlike a player's mark a coach's is not the largest term — if the
#: arithmetic were wrong the remainders would not be legal marks at all.


def coach_points(
    *,
    scored: int,
    conceded: int,
    trailed_by: int = 0,
    substitute_goals: int = 0,
    sent_off: bool = False,
    rating_points: int = 0,
    available: bool = True,
) -> int:
    """One coach's round under §14.3, given what his team did.

    `trailed_by` is the largest deficit the side came back from, which §14.3(b)
    pays for and pays double if the comeback ended in a win: two goals down and
    drawn is worth 2, two goals down and won is worth 4. Losing pays nothing
    for it, however close the recovery came.

    `rating_points` is §14.1's editorial mark, already converted through
    §10.1's table. It is the one term that cannot be computed, exactly as with
    players, and §14.2 says Record will not discuss it.

    `available` is §14.5: a coach who cannot take the bench and is replaced by
    an assistant scores nothing at all — not zero-and-the-result, nothing. A
    sacked coach is the same. Being sent off is different and does score, at
    -3, because he was there.
    """
    if not available:
        return 0

    total = rating_points
    if scored > conceded:
        total += COACH_WIN
    elif scored < conceded:
        total += COACH_LOSS
    else:
        total += COACH_DRAW

    # §14.3(b). Doubled for turning it into a win, nothing for still losing.
    if trailed_by > 0:
        if scored > conceded:
            total += 2 * trailed_by
        elif scored == conceded:
            total += trailed_by

    if scored == 0:
        total += COACH_FAILED_TO_SCORE
    if conceded == 0:
        total += COACH_CLEAN_SHEET
    margin = scored - conceded
    if margin > COACH_BIG_MARGIN:
        total += COACH_BIG_WIN
    elif -margin > COACH_BIG_MARGIN:
        total += COACH_BIG_LOSS

    total += COACH_SUBSTITUTE_GOAL * substitute_goals
    if sent_off:
        total += COACH_SENT_OFF
    return total


def coach_season(
    fixtures: Iterable[Mapping[str, Any]],
    *,
    rating_points: int = 0,
) -> dict[str, dict[int, int]]:
    """What each club's coach scored on each matchday, under §14.3.

    A coach's points follow from his club's result, so a season can be scored
    without knowing who was in charge on a given weekend — which is fortunate,
    because no public archive records that.

    Each fixture is a mapping with `round`, `home`, `away`, `home_goals`,
    `away_goals`, and optionally `home_half` and `away_half`. The half-time
    score stands in for §14.3(b)'s coming from behind: it misses a side that
    went behind and equalised inside the same half, so it undercounts
    comebacks rather than inventing them.

    Two terms are simply absent. §14.1's editorial mark is unpublished, as for
    players, and is credited to everyone alike through `rating_points` — which
    shifts every club by the same amount and so decides nothing, only totals.
    §14.3(d)'s goals off the bench and §14.3(e)'s sendings-off are not in a
    results archive at all. Both omissions make a coach's round smaller than
    it was, never larger.
    """
    season: dict[str, dict[int, int]] = {}
    for fixture in fixtures:
        matchday = int(fixture["round"])
        home_half = fixture.get("home_half")
        away_half = fixture.get("away_half")
        sides = (
            (fixture["home"], fixture["home_goals"], fixture["away_goals"], home_half, away_half),
            (fixture["away"], fixture["away_goals"], fixture["home_goals"], away_half, home_half),
        )
        for club, scored, conceded, ours, theirs in sides:
            trailed = max(0, theirs - ours) if ours is not None and theirs is not None else 0
            season.setdefault(str(club), {})[matchday] = coach_points(
                scored=int(scored),
                conceded=int(conceded),
                trailed_by=trailed,
                rating_points=rating_points,
            )
    return season


def project_coach(
    points_total: int,
    matches: int,
    record: ClubRecord | None,
    league_baseline: float,
    league_goals_against: float,
    league_goals_for: float,
    *,
    prior_strength: float = PRIOR_STRENGTH,
) -> dict[str, object]:
    """Blend a coach's observed rate with what his club's history implies.

    Same shrinkage as `project`: a hot start over two rounds is worth about a
    third, the club record the rest. `league_baseline` is the mean coach rate
    across the eighteen, which is the only sensible anchor — there is no
    positional structure to fall back on.
    """
    observed = per_match(points_total, matches)
    defensive, has_history = club_factor(
        record, Position.GK, league_goals_against, league_goals_for
    )
    attacking, _ = club_factor(
        record, Position.FWD, league_goals_against, league_goals_for
    )
    strength = (
        COACH_DEFENSIVE_SHARE * defensive + (1 - COACH_DEFENSIVE_SHARE) * attacking
    )
    prior = league_baseline * strength

    if observed is None:
        projected, weight = prior, 0.0
    else:
        weight = matches / (matches + prior_strength)
        projected = weight * observed + (1 - weight) * prior

    return {
        "matches": matches,
        "observed_rate": None if observed is None else round(observed, 2),
        "prior_rate": round(prior, 2),
        "projected_rate": round(projected, 2),
        "weight_on_form": round(weight, 2),
        "club_strength": round(strength, 2),
        "club_has_history": has_history,
    }


# --------------------------------------------------------------------------
# Exposure
#
# A squad is not a list of independent bets. Ten of the twenty-three players
# held in round two belonged to Sp. Braga and Gil Vicente, and when that single
# fixture was postponed all ten scored nothing at once — three of the four
# forwards among them. Nothing in this project was looking at that.
#
# Two shapes of exposure matter, and they are opposites:
#
#   concentration   many players at one club. One postponement, one collapse,
#                   one European week takes the lot.
#   both sides      players facing each other. Clean sheets are mutually
#                   exclusive, so this caps the upside as surely as it cushions
#                   the downside. Sometimes wanted, never by accident.
# --------------------------------------------------------------------------


def upcoming_opponents(
    fixtures: Iterable[Fixture], club: str, from_round: int, count: int = 5
) -> list[tuple[int, str, bool]]:
    """The club's next fixtures as (round, opponent, at_home), soonest first.

    Rounds are read in order rather than by date: a club with a postponed
    fixture has its rounds out of chronological sequence, and ordering by
    kickoff would silently reshuffle who plays whom.
    """
    found: list[tuple[int, str, bool]] = []
    for fixture in sorted(fixtures, key=lambda f: f.round_number):
        if fixture.round_number < from_round:
            continue
        if fixture.home == club:
            found.append((fixture.round_number, fixture.away, True))
        elif fixture.away == club:
            found.append((fixture.round_number, fixture.home, False))
        if len(found) >= count:
            break
    return found


def fixture_exposure(
    players: Sequence[Player], fixtures: Iterable[Fixture], round_number: int
) -> list[dict[str, object]]:
    """How much of the squad rides on each of the round's matches.

    Sorted by the number of players involved, so the match that can swing the
    round most is first. `both_sides` marks a fixture where the squad holds
    players facing each other — a hedge against itself, whether or not that was
    the intention.
    """
    by_club: dict[str, list[Player]] = {}
    for player in players:
        by_club.setdefault(player.club, []).append(player)

    rows: list[dict[str, object]] = []
    for fixture in fixtures:
        if fixture.round_number != round_number:
            continue
        home = by_club.get(fixture.home, [])
        away = by_club.get(fixture.away, [])
        if not home and not away:
            continue
        rows.append(
            {
                "home": fixture.home,
                "away": fixture.away,
                "kickoff": fixture.kickoff,
                "played": fixture.played,
                "home_players": [p.id for p in home],
                "away_players": [p.id for p in away],
                "count": len(home) + len(away),
                "both_sides": bool(home and away),
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["home"]))
    return rows


def club_concentration(players: Sequence[Player]) -> list[tuple[str, int]]:
    """Players per club, heaviest first."""
    counts: dict[str, int] = {}
    for player in players:
        counts[player.club] = counts.get(player.club, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


# --------------------------------------------------------------------------
# The editorial rating
#
# For most of this project the rating was treated as unreachable: a Record
# writer's mark, published nowhere, measured at roughly 62% of the variance in
# a score. Every model here worked around it.
#
# It was never unreachable. Record publishes the scoring in §10.1 and §10.3, so
# the objective half can be computed exactly from the calendar and SUBTRACTED —
# and what remains is the rating itself, per player, per round.
#
# Checked against round 2 before this was written: all 11 goalkeepers who
# played left a residual of 2, 3 or 4, every one a legal rating. All 73
# defenders resolved too — 67 as a bare rating, four at 8 (a rating of 4 plus a
# goal), two at -1 (a rating of 2 plus a straight red). Not one residual went
# unexplained, which is as strong a confirmation of the published formula as
# this data can give.
#
# What the calendar cannot supply is who scored, who was booked, and who came
# on late. So a residual is only called a rating when nothing else plausibly
# explains it; otherwise it is reported as ambiguous with its candidates. A
# guess dressed as a measurement would be worse than no measurement.
# --------------------------------------------------------------------------

#: §10.1 — the writers mark 0-5, and 5 converts to 7 rather than 5. These are
#: the only six values a rating can contribute.
RATING_POINTS = (0, 1, 2, 3, 4, 7)

#: §10.3(a) — a goal, other than a converted penalty.
GOAL_POINTS = {Position.GK: 20, Position.DEF: 4, Position.MID: 3, Position.FWD: 2}

#: §10.3(d) and §10.3(b) — only keepers and defenders are paid for the defence.
CLEAN_SHEET_POINTS = {Position.GK: 2, Position.DEF: 1}
CONCEDED_POINTS = {Position.GK: -2, Position.DEF: -1}

#: §10.3(e) — every used player of a winning side.
WIN_BONUS = 1

#: §10.3(i) and §10.3(h).
UNUSED_PENALTY = -1
FORWARD_BLANK_PENALTY = -1

#: §10.3(j) — an own goal, as credited by Record's journalists.
OWN_GOAL_POINTS = -2

#: §10.3(k) — one player a round, always the round's highest scorer. Because
#: the rule names the top score rather than a jury, it is derivable: whoever
#: leads the round took it. Missing this made Pavlidis's 24 unexplainable.
PLAYER_OF_THE_WEEK_BONUS = 5

#: §10.3(f) — three or more in a match.
HAT_TRICK_BONUS = 5


#: §10.3(h) — the threshold a forward has to cross before a blank costs him.
FORWARD_BLANK_MINUTES = 75

#: §10.3 — a straight red, and two yellows.
RED_CARD_POINTS = -3
SECOND_YELLOW_POINTS = -1


def objective_points(
    position: Position,
    *,
    conceded: int,
    won: bool,
    goals: int = 0,
    own_goals: int = 0,
    red_card: bool = False,
    second_yellow: bool = False,
    minutes: int | None = None,
) -> int:
    """Everything §10.3 pays for except Record's own mark.

    Called with only the scoreline it gives the part the calendar determines —
    win bonus, clean sheet, goals conceded — which is all Liga Record's own
    pages support, since they publish a total and never the events behind it.

    Called with the events as well it gives the whole of the score bar the
    rating, and that is the difference between guessing at a mark and reading
    it. zerozero publishes the events per match, which is where they come from.

    Assumes the player was used; an unused player scores a flat -1 (§10.3(i))
    and never reaches this.

    One §10.3 distinction is not available: a converted penalty is a flat +2
    whoever takes it, while an ordinary goal pays by position. Nothing in the
    source separates them, so a defender's penalty is credited +4 here. It
    would take a defender scoring from the spot for it to matter.
    """
    total = WIN_BONUS if won else 0
    if conceded == 0:
        total += CLEAN_SHEET_POINTS.get(position, 0)
    else:
        total += CONCEDED_POINTS.get(position, 0) * conceded

    total += GOAL_POINTS[position] * goals
    if goals >= 3:
        total += HAT_TRICK_BONUS
    total += OWN_GOAL_POINTS * own_goals
    if red_card:
        total += RED_CARD_POINTS
    if second_yellow:
        total += SECOND_YELLOW_POINTS
    if (
        position is Position.FWD
        and goals == 0
        and minutes is not None
        and minutes >= FORWARD_BLANK_MINUTES
    ):
        total += FORWARD_BLANK_PENALTY
    return total


def decompose_round(
    points_round: int,
    position: Position,
    *,
    conceded: int,
    won: bool,
    player_of_the_week: bool = False,
) -> dict[str, object]:
    """Split one round's score into what the calendar explains and what is left.

    `residual` is exact — the score minus the calendar's contribution. What it
    MEANS is not always certain, so `rating` is filled in only when the residual
    is a legal rating on its own and no goal, card or penalty is needed to
    explain it. `candidates` lists the readings that fit when it is not.
    """
    if points_round == UNUSED_PENALTY:
        # Read as an absence, which is what it almost always is — 42% of the
        # market sits here every round. But a player who did take the field and
        # whose rating and events netted to -1 is indistinguishable from one who
        # sat, so this is never called certain.
        return {
            "used": False,
            "objective": 0,
            "residual": None,
            "rating": None,
            "certain": False,
            "candidates": [
                "not used (§10.3(i))",
                "or used, with rating and events netting to -1",
            ],
        }

    objective = objective_points(position, conceded=conceded, won=won)
    if player_of_the_week:
        objective += PLAYER_OF_THE_WEEK_BONUS
    residual = points_round - objective

    candidates: list[str] = []
    if residual in RATING_POINTS:
        candidates.append(f"rating {residual}")
    goal = GOAL_POINTS[position]
    for scored in range(1, 6):
        bonus = goal * scored + (HAT_TRICK_BONUS if scored >= 3 else 0)
        if residual - bonus in RATING_POINTS:
            candidates.append(
                f"rating {residual - bonus} plus {scored} goal"
                + ("s" if scored > 1 else "")
            )
    for penalty, label in (
        (-3, "a straight red"),
        (-1, "a second yellow"),
        (OWN_GOAL_POINTS, "an own goal"),
    ):
        if residual - penalty in RATING_POINTS:
            candidates.append(f"rating {residual - penalty} and {label}")
    if position is Position.FWD and residual + 1 in RATING_POINTS:
        candidates.append(f"rating {residual + 1}, blank after 75 minutes")

    # The simplest reading wins. Almost any residual can also be told as a
    # rating one higher with a second yellow, and requiring a unique candidate
    # made every score ambiguous — which throws away the signal the real data
    # plainly shows: 67 of 73 round-2 defenders sat on a bare rating.
    #
    # Forwards are the exception, and not a rare one: §10.3(h) docks every
    # forward who plays 75 minutes without scoring, so a legal-looking residual
    # is as likely to be one rating higher. Theirs is never called certain.
    bare = residual in RATING_POINTS
    certain = bare and position is not Position.FWD
    return {
        "used": True,
        "objective": objective,
        "residual": residual,
        "rating": residual if certain else None,
        "certain": certain,
        "candidates": candidates or ["nothing in §10.3 explains this"],
    }


def read_rating(
    points_round: int,
    position: Position,
    *,
    conceded: int,
    won: bool,
    used: bool = True,
    goals: int = 0,
    minutes: int | None = None,
    own_goals: int = 0,
    red_card: bool = False,
    second_yellow: bool = False,
    player_of_the_week: bool = False,
) -> dict[str, object]:
    """Record's editorial mark, read off a total once the events are known.

    The companion to `decompose_round`, and the reason the scraping was worth
    doing. Working from Liga Record alone the events are invisible, so a
    residual of 9 for a midfielder is a 3 with two goals, or a 6 with one, or a
    0 with three — and a forward is never certain at all, because §10.3(h)
    docks every blank one and there is no way to know whether he blanked.

    With the events supplied the objective half is complete and the residual is
    the mark, full stop. Nothing is inferred and nothing is chosen between.

    A residual outside 0-7 means an assumption failed rather than that Record
    marked out of range — a penalty credited by position, a missing own goal, a
    Player of the Week not accounted for. It is returned as it stands, with
    `certain` false, because a reading that quietly clamps itself into the
    legal range would hide exactly the cases worth looking at.
    """
    if not used:
        return {
            "used": False,
            "objective": UNUSED_PENALTY,
            "residual": points_round - UNUSED_PENALTY,
            "rating": None,
            "certain": points_round == UNUSED_PENALTY,
            "candidates": ["not used (§10.3(i))"],
        }

    objective = objective_points(
        position,
        conceded=conceded,
        won=won,
        goals=goals,
        own_goals=own_goals,
        red_card=red_card,
        second_yellow=second_yellow,
        minutes=minutes,
    )
    if player_of_the_week:
        objective += PLAYER_OF_THE_WEEK_BONUS
    residual = points_round - objective

    legal = residual in RATING_POINTS
    return {
        "used": True,
        "objective": objective,
        "residual": residual,
        "rating": residual if legal else None,
        "certain": legal,
        "candidates": [f"rating {residual}"]
        if legal
        else [f"residual {residual} is not a rating in §10.1"],
    }


# --------------------------------------------------------------------------
# Standing in for the editorial mark, so a past season can be scored
# --------------------------------------------------------------------------
#
# §10.2 says Record will not discuss an individual mark with anyone, and no
# past season's marks are published. Everything else in §10.3 survives in
# zerozero's match pages — goals, cards, minutes, scorelines — so a past
# season's score is exact but for this one term.
#
# zerozero's own 0-10 rating is the only public verdict on the same
# performance. Fitted against 489 marks read off real 2026/27 scores, it lands
# within 0.44 points of the mark on held-out rounds, against scores whose own
# spread is 2.66. It cuts the error of simply assuming the average mark by
# about two fifths, so it is doing real work, and it is not doing miracles.
#
# What makes that good enough is what it is NOT asked to do. The large, spiky
# part of a score — a hat-trick, a clean sheet, a sending off, a man left on
# the bench — is computed from the events and never estimated. Only the mark
# is, and the mark is the flattest term there is.
#
# Checked a third way, against nothing this code has ever seen: Record's own
# editorial team set every price for this season after watching last season,
# and those prices correlate r = 0.63 with what the reconstruction says a
# player was worth (n = 170; GK 0.85, DEF 0.70, FWD 0.72, MID 0.54). No price
# is used anywhere in building it. Two groups looked at the same football and
# broadly agree.

#: Fitted on 489 player-rounds of 2026/27. Refit when the season is longer.
MARK_FIT_SLOPE = 0.9883
MARK_FIT_INTERCEPT = -4.0686

#: What to use when a match went unrated — the market's own average mark,
#: which is what "no information" should mean.
MEAN_MARK_POINTS = 2.59

#: zerozero rates 0-10 but hands out very little of that range; anything
#: outside is a data error rather than a performance.
RATING_SCALE = (0.0, 10.0)


def expected_rating_points(rating: float) -> float:
    """The mark, in points, on average, for a zerozero rating.

    Unrounded, and therefore unbiased. This is the one to sum over a season:
    rounding each match first would throw away the halves that cancel.
    """
    if not RATING_SCALE[0] <= rating <= RATING_SCALE[1]:
        raise ValueError(f"{rating} is not a zerozero rating")
    estimate = MARK_FIT_INTERCEPT + MARK_FIT_SLOPE * rating
    return min(max(estimate, min(RATING_POINTS)), max(RATING_POINTS))


def likely_rating_points(rating: float) -> int:
    """The mark a single match most likely carried, as a mark Record can give.

    §10.1 allows six values and no others — note that 5 and 6 points are not
    among them — so a per-match reconstruction has to land on one of them.
    Snapping measures slightly more accurate per match than the unrounded
    estimate, and slightly worse over a season, which is why both exist.
    """
    estimate = expected_rating_points(rating)
    return min(RATING_POINTS, key=lambda points: abs(points - estimate))


def reconstruct_points(
    position: Position,
    *,
    conceded: int,
    won: bool,
    used: bool,
    rating: float | None = None,
    goals: int = 0,
    minutes: int | None = None,
    own_goals: int = 0,
    red_card: bool = False,
    second_yellow: bool = False,
    per_match: bool = True,
) -> dict[str, object]:
    """What Liga Record would have paid for one match, rebuilt from the events.

    The inverse of `read_rating`: that one takes a published score and works
    out the mark, this one takes the mark and works out the score. Everything
    but the mark is §10.3 arithmetic on facts, so `objective` is not an
    estimate and is reported separately from the part that is.

    Player of the Week is deliberately absent. §10.3(k) gives it to the round's
    highest scorer, which cannot be known for one player in isolation — it is a
    property of the whole round, and belongs to whatever assembles these.
    """
    if not used:
        return {
            "points": float(UNUSED_PENALTY),
            "objective": UNUSED_PENALTY,
            "mark": 0.0,
            "mark_source": "unused",
            "estimated": False,
        }

    objective = objective_points(
        position,
        conceded=conceded,
        won=won,
        goals=goals,
        own_goals=own_goals,
        red_card=red_card,
        second_yellow=second_yellow,
        minutes=minutes,
    )
    if rating is None:
        mark, source = MEAN_MARK_POINTS, "assumed average"
    elif per_match:
        mark, source = float(likely_rating_points(rating)), "rating"
    else:
        mark, source = expected_rating_points(rating), "rating"

    return {
        "points": objective + mark,
        "objective": objective,
        "mark": mark,
        "mark_source": source,
        "estimated": True,
    }


# --------------------------------------------------------------------------
# Telling one country's first division from another's
# --------------------------------------------------------------------------
#
# zerozero labels a competition by its tier, not by its country: the Swiss
# Super League, the Belgian Pro League and the Danish Superliga are all "D1",
# exactly like the Primeira Liga. Filtering a player's season to "D1" therefore
# keeps every league he played in, and a reconstruction built on that scores a
# Swiss August as though it were a Portuguese one.
#
# It is not a small share and it is not visibly wrong: 9% of a first sweep, and
# the only outward sign was a handful of players with thirty-seven matches in a
# thirty-four-round league.
#
# The fix is to check the matches against a calendar that is definitely the
# right competition. A club belongs if its matches keep turning up in it.

#: How much of a club's record has to appear in the calendar before the club is
#: taken to belong to it. Well clear of both populations in practice — real
#: members land near 1.0, foreign clubs near 0.0 — so the exact value does not
#: decide anything.
CALENDAR_AGREEMENT = 0.5


def _same_day(date: str) -> str:
    """One spelling for a date, since the two sources punctuate differently."""
    return (date or "").strip().replace("/", "-")


def clubs_in_calendar(
    appearances: Iterable[Mapping[str, Any]],
    calendar: Iterable[Mapping[str, Any]],
    *,
    agreement: float = CALENDAR_AGREEMENT,
) -> dict[str, dict[str, Any]]:
    """Which clubs in a pile of match records belong to a known competition.

    Decided by evidence rather than by a list of names. A club's matches are
    checked against the calendar on round, date and scoreline, none of which
    needs the two sources to spell the club the same way — and they do not:
    one says "Sporting Clube de Braga" where the other says "SC Braga".

    Membership is judged per club rather than per match on purpose. Sources
    disagree about individual results — these two disagree about three matches
    of the final round — and a per-match rule would silently drop a real
    round of real football over a scoreline typo. A club that turns up all
    season is a member on its last day too.

    Returns every club seen, so the ones being excluded can be looked at.
    """
    known = {
        (
            int(fixture["round"]),
            _same_day(str(fixture["date"])),
            int(fixture["home_goals"]),
            int(fixture["away_goals"]),
        )
        for fixture in calendar
    }

    seen: dict[str, list[bool]] = {}
    for appearance in appearances:
        club = str(appearance.get("club") or "")
        if not club:
            continue
        key = (
            int(appearance["round"]),
            _same_day(str(appearance["date"])),
            int(appearance["home_goals"]),
            int(appearance["away_goals"]),
        )
        seen.setdefault(club, []).append(key in known)

    return {
        club: {
            "matches": len(hits),
            "in_calendar": sum(hits),
            "share": round(sum(hits) / len(hits), 3),
            "member": sum(hits) / len(hits) >= agreement,
        }
        for club, hits in seen.items()
    }


def fill_absent_rounds(
    matches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Add the rounds a player was not even in the squad for.

    A match archive lists a player when he was involved — playing, or on the
    bench. It has no row at all for the weeks he was injured, suspended or left
    out, because from football's point of view nothing happened.

    From a Liga Record manager's point of view something very much happened: he
    paid for that player and §10.3(i) gave him -1. Leaving the gap out divides
    a season by the weeks that went well, which is how a defender with six
    appearances in a thirty-four-round season comes out looking like one of the
    best in the league. He was, on the six days he played.

    Only the span between his first and last appearance is filled. Before and
    after that he was somewhere else — another country, another division — and
    could not have been owned, so those rounds are nobody's loss.
    """
    present = {int(m["round"]): dict(m) for m in matches}
    if not present:
        return []

    for round_number in range(min(present), max(present) + 1):
        if round_number in present:
            continue
        present[round_number] = {
            "round": round_number,
            "used": False,
            "absent": True,
            "minutes": 0,
            "goals": 0,
            "assists": 0,
            "yellow_cards": 0,
            "red_card": False,
            "rating": None,
            "objective": UNUSED_PENALTY,
            "mark": 0.0,
            "mark_source": "not in the squad",
            "points": float(UNUSED_PENALTY),
            "player_of_the_week": False,
        }
    return [present[round_number] for round_number in sorted(present)]


#: A score at or below this is a wasted week — the player was on the pitch and
#: returned nothing, or was not on it at all.
BLANK_POINTS = 0

#: A score at or above this is the kind of week a season turns on. Set from the
#: reconstructed distribution: about one match in twenty clears it.
HAUL_POINTS = 8

#: Rounds in a form window. Five is a month of football, short enough to move
#: and long enough not to be one good afternoon.
FORM_WINDOW = 5


# --------------------------------------------------------------------------
# Saying how much a recommendation should be trusted
# --------------------------------------------------------------------------
#
# Two different things get called "risk" and running them together is the
# mistake worth avoiding.
#
# HOW SURE IS THE NUMBER. A player with sixty matches behind him is measured;
# one with two is his club's average wearing his name. That is a statement
# about the estimate, and it is the same whoever is reading it.
#
# WHAT HAVING HIM DOES TO YOUR POSITION. A player half the country owns cannot
# move you up the table, only stop you falling; one nobody owns is the only way
# to gain ground, and the only way to lose it. That is a statement about the
# field, and it has nothing to do with how well he is measured.
#
# A confident estimate of a differential is the rare, valuable case. A thin
# estimate of a player everybody owns is the worst of both.
#
# Every cut below is a percentile of the real market, measured rather than
# chosen — 498 players, and the two reconstructed seasons behind them.

#: Matches behind an estimate. The median player with any history has 30; a
#: quarter have fewer than 17. Over half the market has none at all.
EVIDENCE_MEASURED = 30
EVIDENCE_THIN = 15

#: Share of rounds a player takes the field in. Measured over 186 players with
#: at least twenty matchdays on the books: the quartiles are 0.64 and 0.88 and
#: the median is 0.76.
#:
#: The first version put the cut at 0.75, which is the median — so half the
#: league fell on the wrong side of it by a hundredth, and eleven of a
#: twenty-three-man squad came out with the same label. A threshold sitting on
#: the median does not divide anything.
#:
#: Feed this the rate a player's HISTORY shows, not a projection shrunk toward
#: the league on two rounds of a new season. The projection is right to be
#: cautious and the label is answering a different question: is this a man who
#: plays, as far as anyone can tell.
AVAILABILITY_REGULAR = 0.85
AVAILABILITY_FRINGE = 0.65

#: Spread of a player's points from round to round. The market's median is
#: 1.67, the quartiles 1.17 and 2.20.
VOLATILITY_STEADY = 1.2
VOLATILITY_SWINGY = 2.2

#: Ownership. The median player is held by 0.9% of teams and the ninetieth
#: percentile by 11.9%, so "everybody has him" starts a long way below half.
OWNED_TEMPLATE = 10.0
OWNED_DIFFERENTIAL = 1.0


def describe_pick(
    *,
    appearances: int,
    availability: float | None = None,
    volatility: float | None = None,
    owned_percent: float | None = None,
) -> dict[str, Any]:
    """How much to trust a recommendation, and what it does to your position.

    Returns both readings separately and a plain sentence that leads with
    whichever matters most, because a label on its own invites the wrong
    reading: "risky" could mean the player is unpredictable, that the estimate
    is a guess, or that nobody else owns him, and those call for three
    different decisions.
    """
    if appearances >= EVIDENCE_MEASURED:
        evidence = "measured"
    elif appearances >= EVIDENCE_THIN:
        evidence = "partial"
    else:
        evidence = "thin"

    role = None
    if availability is not None:
        if availability >= AVAILABILITY_REGULAR:
            role = "regular"
        elif availability >= AVAILABILITY_FRINGE:
            role = "rotated"
        else:
            role = "fringe"

    swing = None
    if volatility is not None:
        swing = (
            "steady"
            if volatility <= VOLATILITY_STEADY
            else "swingy"
            if volatility >= VOLATILITY_SWINGY
            else "normal"
        )

    field = None
    if owned_percent is not None:
        field = (
            "template"
            if owned_percent >= OWNED_TEMPLATE
            else "differential"
            if owned_percent <= OWNED_DIFFERENTIAL
            else "common"
        )

    # One word for a table, one sentence for a decision. The word alone is not
    # enough — "risky" could mean unpredictable, unmeasured, or unowned — but a
    # sentence in every row of a list of twenty-three is unreadable, and an
    # unreadable recommendation is not read.
    #
    # Both lead with the largest caveat. A thin estimate outranks everything
    # else: there is no point discussing the shape of a number that is not
    # really about this player.
    if evidence == "thin":
        label = "bet"
        verdict = (
            f"a bet — {appearances} matches behind him, so this is his club's "
            "average with his name on it"
        )
    elif role == "fringe":
        label = "risky"
        verdict = "risky — often not in the side, and §10.3(i) charges -1 for that"
    elif role == "rotated":
        label = "uncertain"
        verdict = "uncertain — he is in and out of the team"
    elif swing == "swingy":
        label = "volatile"
        verdict = "safe to own, wild to captain — the returns swing hard"
    elif evidence == "measured" and role == "regular":
        label = "safe"
        verdict = "safe — measured, and he plays"
    else:
        label = "fair"
        verdict = "reasonable"

    if field == "differential" and evidence != "thin":
        verdict += "; almost nobody owns him, so he moves you either way"
    elif field == "template":
        verdict += "; most of the field owns him, so he mostly keeps you level"

    return {
        "label": label,
        # The field is a separate word on purpose. Folding it into the first
        # one would say a differential is risky, and it is not: it is a
        # different question, with the same answer for a good player and a bad
        # one.
        "field_label": {"template": "template", "differential": "differential"}.get(
            field, ""
        )
        if evidence != "thin"
        else "",
        "evidence": evidence,
        "appearances": appearances,
        "role": role,
        "availability": None if availability is None else round(availability, 2),
        "swing": swing,
        "volatility": None if volatility is None else round(volatility, 2),
        "field": field,
        "owned_percent": owned_percent,
        "verdict": verdict,
    }


def season_summary(matches: Sequence[dict[str, object]]) -> dict[str, object]:
    """One player's season, from the matches reconstructed for him.

    `per_match` divides by matches in the squad, not by matches played, because
    a Liga Record manager pays for a player every round whether he starts or
    sits. A forward who scores 8 in a third of the rounds and -1 in the rest is
    not an 8-point forward, and dividing by appearances would say he was.

    `per_90` is the other question — how good he is when he plays — and is
    reported beside it rather than instead of it.
    """
    if not matches:
        return {
            "matches": 0,
            "played": 0,
            "points": 0.0,
            "per_match": None,
            "per_90": None,
            "minutes": 0,
            "goals": 0,
            "blanks": 0,
            "hauls": 0,
            "best": None,
            "worst": None,
            "consistency": None,
        }

    points = [float(m["points"]) for m in matches]
    played = [m for m in matches if m.get("used")]
    minutes = sum(int(m.get("minutes") or 0) for m in matches)
    played_points = [float(m["points"]) for m in played]

    return {
        "matches": len(matches),
        "played": len(played),
        "points": round(sum(points), 1),
        "per_match": round(sum(points) / len(points), 2),
        "per_90": round(sum(played_points) / (minutes / 90), 2) if minutes else None,
        "minutes": minutes,
        "goals": sum(int(m.get("goals") or 0) for m in matches),
        "blanks": sum(1 for p in points if p <= BLANK_POINTS),
        "hauls": sum(1 for p in points if p >= HAUL_POINTS),
        "best": max(points),
        "worst": min(points),
        # Spread of what he actually returns. Two players can average the same
        # and be entirely different bets, and the captaincy is a bet.
        "consistency": round(pstdev(points), 2) if len(points) > 1 else 0.0,
    }


def form_curve(
    matches: Sequence[dict[str, object]], *, window: int = FORM_WINDOW
) -> list[dict[str, object]]:
    """A rolling average through the season, in round order.

    The point of reconstructing a season match by match rather than as a total:
    a total says a player was worth 3.1 a round, and the curve says he was
    worth 6 until January and 1 after it. Only the second one is a reason to
    buy or sell.

    Rounds are sorted before the window is taken — a match list that arrives
    newest-first, as zerozero's does, would otherwise produce a curve running
    backwards through the season without ever looking wrong.
    """
    ordered = sorted(matches, key=lambda m: int(m["round"]))
    curve: list[dict[str, object]] = []
    for index, match in enumerate(ordered):
        recent = ordered[max(0, index - window + 1) : index + 1]
        points = [float(m["points"]) for m in recent]
        curve.append(
            {
                "round": int(match["round"]),
                "points": float(match["points"]),
                "form": round(sum(points) / len(points), 2),
                "window": len(points),
            }
        )
    return curve


def rating_history(
    rounds: Iterable[dict[str, object]]
) -> dict[str, object]:
    """Summarise a player's ratings across the rounds that could be read.

    Only rounds whose residual was unambiguous count toward the mean; the rest
    are counted and reported so the sample size is never overstated.
    """
    ratings = [r["rating"] for r in rounds if r.get("rating") is not None]
    ambiguous = sum(1 for r in rounds if r.get("used") and r.get("rating") is None)
    unused = sum(1 for r in rounds if r.get("used") is False)
    return {
        "rounds_read": len(ratings),
        "ambiguous": ambiguous,
        "unused": unused,
        "mean_rating": round(mean(ratings), 2) if ratings else None,
        "ratings": ratings,
    }


def league_table(fixtures: Iterable[Fixture]) -> list[dict[str, object]]:
    """The Primeira Liga table, computed from the calendar rather than fetched.

    Every input is already on hand: the calendar carries scores for played
    matches, so the standings are a fold over it. Nothing new is requested from
    the site.

    `played` is reported per club and never assumed equal. Two clubs are a
    match behind after one postponement, and a table that hides that is the
    same error as a points total without its denominator — which is what this
    module exists to prevent.

    Ordering is the Portuguese one: points, then goal difference, then goals
    scored, then name so the result is stable.
    """
    table: dict[str, dict[str, int]] = {}
    for fixture in fixtures:
        if not fixture.played:
            continue
        sides = (
            (fixture.home, fixture.home_goals, fixture.away_goals),
            (fixture.away, fixture.away_goals, fixture.home_goals),
        )
        for club, scored, conceded in sides:
            row = table.setdefault(
                club,
                {"played": 0, "won": 0, "drawn": 0, "lost": 0,
                 "goals_for": 0, "goals_against": 0},
            )
            row["played"] += 1
            row["goals_for"] += scored
            row["goals_against"] += conceded
            if scored > conceded:
                row["won"] += 1
            elif scored == conceded:
                row["drawn"] += 1
            else:
                row["lost"] += 1

    rows = []
    for club, row in table.items():
        difference = row["goals_for"] - row["goals_against"]
        rows.append(
            {
                "club": club,
                **row,
                "goal_difference": difference,
                "points": row["won"] * 3 + row["drawn"],
            }
        )
    rows.sort(
        key=lambda r: (-r["points"], -r["goal_difference"], -r["goals_for"], r["club"])
    )
    for place, row in enumerate(rows, 1):
        row["position"] = place
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
