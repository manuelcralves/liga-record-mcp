"""Replaying a season without letting the future leak into it.

A backtest that cheats does not fail. It produces a strategy that looks
brilliant, and the only symptom is a number too good to be true — which is
indistinguishable from a number that is simply good. So the first tests here
are not about the strategy at all: they change the future and assert that
decisions taken before it do not move.

The rest cover the constraints a transfer has to satisfy, and one that is not a
constraint but a correction. Judging a swap by the two players' own rates says
a better substitute is an improvement. It is not: only eleven of twenty-three
score. The backtest found this by reporting swaps worth +207 points of player
form that moved the season by +5.
"""

from __future__ import annotations

from liga_record_mcp.backtest import (
    ABSENT,
    best_transfer,
    play_round,
    replay,
    replay_with_transfers,
    settle_transfers,
    shrunk_projection,
    two_part_projection,
)
from liga_record_mcp.models import Player, Position

QUOTA = {Position.GK: 3, Position.DEF: 8, Position.MID: 8, Position.FWD: 4}
MATCHDAYS = list(range(1, 11))


def player(identifier, position, value=1_000_000):
    return Player(
        id=identifier,
        name=identifier,
        position=position,
        club="Benfica",
        value=value,
        initial_value=value,
    )


def market_of(extra=0):
    market = {}
    for position, count in QUOTA.items():
        for n in range(count + extra):
            identifier = f"{position.value}{n}"
            market[identifier] = player(identifier, position)
    return market


def squad_of(market):
    picked = []
    for position, count in QUOTA.items():
        picked.extend(
            i for i in market if market[i].position is position
        )
    chosen = []
    for position, count in QUOTA.items():
        chosen.extend(
            [i for i in market if market[i].position is position][:count]
        )
    return chosen


def flat_history(market, points=3.0):
    return {i: {m: points for m in MATCHDAYS} for i in market}


def cells_of(market):
    return {i: (p.club, p.position.value) for i, p in market.items()}


# --------------------------------------------------------------------------
# The future must not reach backwards
# --------------------------------------------------------------------------


def test_a_projection_cannot_see_past_its_own_cutoff():
    """The whole backtest rests on this. Rewrite everything from matchday 5
    onwards and a projection made before matchday 5 must not move by a
    thousandth."""
    market = market_of()
    cells = cells_of(market)
    history = {i: {m: float(m) for m in MATCHDAYS} for i in market}

    before = shrunk_projection(history, cells, upto=5)
    rewritten = {
        i: {m: (999.0 if m >= 5 else points) for m, points in by_round.items()}
        for i, by_round in history.items()
    }
    after = shrunk_projection(rewritten, cells, upto=5)
    assert before == after


def test_a_projection_does_move_when_its_own_past_changes():
    """The guard above would also pass if the projection ignored everything."""
    market = market_of()
    cells = cells_of(market)
    history = {i: {m: float(m) for m in MATCHDAYS} for i in market}
    changed = {
        i: {m: (0.0 if m < 5 else points) for m, points in by_round.items()}
        for i, by_round in history.items()
    }
    assert shrunk_projection(history, cells, upto=5) != shrunk_projection(
        changed, cells, upto=5
    )


def test_the_first_transfer_of_a_season_ignores_what_happens_after_it():
    """A decision taken before matchday 2 may read matchday 1 and nothing else,
    however the rest of the season turns out."""
    market = market_of(extra=2)
    cells = cells_of(market)
    history = {i: {m: float(hash(i) % 7) for m in MATCHDAYS} for i in market}
    squad = squad_of(market)

    def first_move(scores):
        played = replay_with_transfers(
            squad, market, scores, MATCHDAYS, cells=cells, budget=40_000_000
        )
        return played["transfers"][0] if played["transfers"] else None

    rewritten = {
        i: {m: (50.0 if m >= 2 else points) for m, points in by_round.items()}
        for i, by_round in history.items()
    }
    before, after = first_move(history), first_move(rewritten)
    if before is None and after is None:
        return
    assert before is not None and after is not None
    assert (before["out"], before["in"]) == (after["out"], after["in"])


def test_a_player_with_nothing_behind_him_gets_the_pool_not_a_zero():
    """A January signing, or a man who has not played yet. Scoring him zero
    would say he is terrible; the pool is what "no information" means, and it
    is the difference between not knowing and knowing something bad."""
    market = market_of()
    cells = cells_of(market)
    history = flat_history(market, points=4.0)
    history["DEF0"] = {}

    view = shrunk_projection(history, cells, upto=3)
    assert view["DEF0"] == 4.0
    assert view["DEF1"] == 4.0


def test_before_a_ball_is_kicked_there_is_nothing_to_project_from():
    """Not a defect. At matchday one no football has been played, so every
    estimate is the same and none of them means anything — which is exactly
    the position a manager is in when he first buys a squad."""
    market = market_of()
    view = shrunk_projection(flat_history(market), cells_of(market), upto=1)
    assert len(set(view.values())) == 1


# --------------------------------------------------------------------------
# What a transfer has to satisfy
# --------------------------------------------------------------------------


def test_a_swap_stays_inside_the_budget():
    """§6.4 is a hard limit. A squad at the ceiling cannot buy anyone dearer."""
    market = market_of(extra=1)
    market["FWD9"] = player("FWD9", Position.FWD, value=6_000_000)
    squad = squad_of(market)
    budget = sum(market[i].value for i in squad)
    projection = {i: 1.0 for i in market}
    projection["FWD9"] = 99.0

    assert best_transfer(
        squad, market, projection, budget=budget, rounds_left=10
    ) is None
    afforded = best_transfer(
        squad, market, projection, budget=budget + 5_000_000, rounds_left=10
    )
    assert afforded is not None and afforded[1] == "FWD9"


def test_a_swap_is_like_for_like():
    """§6.8 — the positional contingent has to hold, so a brilliant forward
    cannot replace a defender however much better he is."""
    market = market_of(extra=1)
    squad = squad_of(market)
    projection = {i: 1.0 for i in market}
    spare_forward = next(i for i in market if i.startswith("FWD") and i not in squad)
    projection[spare_forward] = 99.0

    out_id, in_id, _ = best_transfer(
        squad, market, projection, budget=99_000_000, rounds_left=10
    )
    assert market[out_id].position is market[in_id].position is Position.FWD


def test_a_player_already_sold_is_never_bought_back():
    """Two near-identical players make a projection flicker between them, and
    without this the model spent four transfers of one season swapping a
    defender for another and back, paying the churn for nothing."""
    market = market_of(extra=1)
    squad = squad_of(market)
    spare = next(i for i in market if i.startswith("DEF") and i not in squad)
    projection = {i: 1.0 for i in market}
    projection[spare] = 50.0

    assert best_transfer(
        squad, market, projection, budget=99_000_000, rounds_left=10
    )[1] == spare
    assert best_transfer(
        squad, market, projection, budget=99_000_000, rounds_left=10, sold=[spare]
    ) is None


def test_upgrading_a_substitute_is_worth_nothing_and_is_not_proposed():
    """The correction the backtest forced. Only eleven of twenty-three score,
    so a better twelfth man changes no total — and comparing the two players'
    own rates cannot see that."""
    market = market_of(extra=1)
    squad = squad_of(market)
    # Everyone in the XI is excellent; the spare defender is better than the
    # squad's worst defender but nowhere near the starters.
    projection = {i: 100.0 for i in squad}
    for name in ("DEF6", "DEF7"):
        projection[name] = 0.5
    spare = next(i for i in market if i.startswith("DEF") and i not in squad)
    projection[spare] = 1.0

    assert best_transfer(
        squad, market, projection, budget=99_000_000, rounds_left=10
    ) is None


def test_a_swap_that_does_reach_the_eleven_is_proposed():
    """The guard above must not be so strict that nothing is ever worth doing."""
    market = market_of(extra=1)
    squad = squad_of(market)
    projection = {i: 1.0 for i in squad}
    spare = next(i for i in market if i.startswith("DEF") and i not in squad)
    projection[spare] = 40.0

    proposed = best_transfer(
        squad, market, projection, budget=99_000_000, rounds_left=10
    )
    assert proposed is not None and proposed[1] == spare


def test_a_threshold_can_refuse_a_real_but_tiny_edge():
    """A projected edge is not a real one. Passing a threshold is how "should I
    transfer at all this week" gets measured rather than assumed."""
    market = market_of(extra=1)
    squad = squad_of(market)
    projection = {i: 1.0 for i in squad}
    spare = next(i for i in market if i.startswith("DEF") and i not in squad)
    projection[spare] = 1.2

    assert best_transfer(squad, market, projection, budget=99_000_000, rounds_left=1)
    assert (
        best_transfer(
            squad, market, projection, budget=99_000_000, rounds_left=1, min_gain=5.0
        )
        is None
    )


# --------------------------------------------------------------------------
# Reporting what happened
# --------------------------------------------------------------------------


def test_a_swap_is_settled_on_the_rounds_that_followed_it():
    """What the model believed and what happened are both kept. A strategy that
    is right on average while being wrong loudly is a different thing from one
    that is quietly right, and only the pair tells them apart."""
    history = {
        "in": {m: 10.0 for m in MATCHDAYS},
        "out": {m: 1.0 for m in MATCHDAYS},
    }
    settled = settle_transfers(
        [
            {
                "matchday": 6,
                "out": "Out",
                "in": "In",
                "out_id": "out",
                "in_id": "in",
                "projected_gain": 12.0,
                "rounds_left": 5,
            }
        ],
        history,
        MATCHDAYS,
    )[0]
    assert settled["actual_gain"] == 45.0  # five rounds at nine points
    assert settled["right"] is True
    assert settled["projected_gain"] == 12.0
    assert "out_id" not in settled


def test_knowing_the_results_is_never_worse_than_guessing_them():
    """A sanity property. If a blind season ever beat the same squad played
    with hindsight, the replay would be scoring the wrong team."""
    market = market_of()
    cells = cells_of(market)
    history = {
        i: {m: float((hash(i + str(m)) % 13) - 2) for m in MATCHDAYS} for i in market
    }
    squad = squad_of(market)
    forecasts = {m: shrunk_projection(history, cells, upto=m) for m in MATCHDAYS}

    blind = replay(squad, market, history, MATCHDAYS, forecasts=forecasts)
    perfect = replay(squad, market, history, MATCHDAYS)
    assert perfect["points"] >= blind["points"]


def test_an_absent_player_costs_a_point_rather_than_nothing():
    """§10.3(i). A history with no entry for a round is an absence, and free
    absences would flatter every season replayed here."""
    market = market_of()
    squad = squad_of(market)
    empty: dict[str, dict[int, float]] = {}
    played = replay(squad, market, empty, [1])
    assert played["points"] == 12 * ABSENT


# --------------------------------------------------------------------------
# Twenty-three are owned, fifteen are named, eleven score
# --------------------------------------------------------------------------


def test_only_the_eleven_score_and_the_other_twelve_do_not():
    """§6.13 — eleven are "ativos (a pontuar)". A squad of twenty-three where
    everyone counted would inflate every round by roughly a factor of two, and
    a model that scored the whole squad would buy entirely the wrong players.
    """
    market = market_of()
    squad = squad_of(market)
    history = {i: {1: 5.0} for i in squad}
    played = replay(squad, market, history, [1])
    # Eleven at five, and the captain paid twice. Not twenty-three at five.
    assert played["points"] == 11 * 5.0 + 5.0


def test_a_substitute_scores_only_if_he_comes_on():
    """He is named and he is not "ativo" (§6.13). §11 brings him on only when a
    starter of his position did not play — so a bench full of good rounds is
    worth nothing to a team whose eleven all turned up."""
    market = market_of()
    squad = squad_of(market)
    forecast = {i: float(len(squad) - n) for n, i in enumerate(squad)}
    history = {i: {1: 1.0} for i in squad}

    named = play_round(squad, market, history, 1, forecast=forecast)
    for identifier in named["bench"] if "bench" in named else []:
        history[identifier] = {1: 99.0}
    played = play_round(squad, market, history, 1, forecast=forecast)
    assert played["substitutions"] == 0
    assert played["points"] == 12 * 1.0  # eleven plus the captain, nobody else


def test_a_second_failure_in_one_position_cannot_be_covered():
    """§11.2 substitutes like for like, and the bench carries one spare per
    position. So the first midfielder who does not play is replaced and the
    second one is not — his -1 stands, whatever else is on the bench."""
    market = market_of()
    squad = squad_of(market)
    forecast = {i: float(len(squad) - n) for n, i in enumerate(squad)}
    history = {i: {1: 4.0} for i in squad}

    named = play_round(squad, market, history, 1, forecast=forecast)
    midfielders = [i for i in named["starters"] if i.startswith("MID")][:2]
    assert len(midfielders) == 2
    for identifier in midfielders:
        history[identifier] = {1: ABSENT}

    played = play_round(squad, market, history, 1, forecast=forecast)
    assert played["substitutions"] == 1
    assert sum(1 for i in played["starters"] if history[i][1] == ABSENT) == 1


def test_at_most_three_substitutes_come_on():
    """§6.13 — four are named and only three may enter, so a round in which
    four starters fail always leaves one -1 standing.

    The four who fail are chosen after seeing which eleven the model actually
    picked, because asserting "at most three" against a round where only one
    could ever have failed is a test that cannot fail.
    """
    market = market_of()
    squad = squad_of(market)
    forecast = {i: float(len(squad) - n) for n, i in enumerate(squad)}
    everyone = {i: {1: 4.0} for i in squad}

    named = play_round(squad, market, everyone, 1, forecast=forecast)
    # One starter from each position, because the bench carries one spare of
    # each — so all four could be covered were it not for the cap of three.
    failing = []
    for prefix in ("GK", "DEF", "MID", "FWD"):
        failing.extend(
            [i for i in named["starters"] if i.startswith(prefix)][:1]
        )
    assert len(failing) == 4

    history = dict(everyone)
    for identifier in failing:
        history[identifier] = {1: ABSENT}

    played = play_round(squad, market, history, 1, forecast=forecast)
    assert played["substitutions"] == 3
    still_out = [i for i in played["starters"] if history[i][1] == ABSENT]
    assert len(still_out) == 1


# --------------------------------------------------------------------------
# Whether he plays, and what he does when he does
# --------------------------------------------------------------------------


def minutes_like(market, played=90):
    return {i: {m: played for m in MATCHDAYS} for i in market}


def test_the_split_cannot_see_past_its_own_cutoff_either():
    """The same guard as for the plain projection, and it matters more here:
    a rotation signal reading next week's team sheet would be uncanny."""
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: float(m) for m in MATCHDAYS} for i in market}
    mins = minutes_like(market)

    before = two_part_projection(points, mins, cells, upto=6)
    future_points = {
        i: {m: (999.0 if m >= 6 else v) for m, v in by.items()} for i, by in points.items()
    }
    future_minutes = {
        i: {m: (0 if m >= 6 else v) for m, v in by.items()} for i, by in mins.items()
    }
    assert two_part_projection(future_points, future_minutes, cells, upto=6) == before


def test_a_man_who_has_stopped_playing_is_written_down_quickly():
    """The reason the split exists. Averaging a season folds a lost place into
    months of the points he scored when he still had it, and keeps recommending
    him for weeks."""
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: 3.0 for m in MATCHDAYS} for i in market}
    mins = minutes_like(market)

    dropped = "MID0"
    for m in MATCHDAYS[-5:]:
        points[dropped][m] = ABSENT
        mins[dropped][m] = 0

    folded = shrunk_projection(points, cells, upto=MATCHDAYS[-1] + 1)
    split = two_part_projection(points, mins, cells, upto=MATCHDAYS[-1] + 1)
    assert split[dropped] < folded[dropped]
    # And the players still in the side are not dragged down with him.
    assert split["MID1"] > split[dropped] + 1.0


def test_a_regular_projects_at_what_he_returns():
    """A man who plays every week and scores four is a four-point player, and
    the split must not shave him for the arithmetic's sake."""
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: 4.0 for m in MATCHDAYS} for i in market}
    view = two_part_projection(points, minutes_like(market), cells, upto=MATCHDAYS[-1] + 1)
    assert all(abs(v - 4.0) < 0.01 for v in view.values())


def test_a_man_who_never_plays_projects_at_the_penalty():
    """§10.3(i) is what owning him costs, and it is what he is worth."""
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: ABSENT for m in MATCHDAYS} for i in market}
    mins = {i: {m: 0 for m in MATCHDAYS} for i in market}
    view = two_part_projection(points, mins, cells, upto=MATCHDAYS[-1] + 1)
    assert all(abs(v - ABSENT) < 0.01 for v in view.values())


def test_the_minutes_adjustment_is_bounded_at_both_ends():
    """One ten-minute cameo should not project a man at a ninth of himself, and
    a single full match should not project a substitute as a star."""
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: 4.0 for m in MATCHDAYS} for i in market}
    mins = minutes_like(market)
    for m in MATCHDAYS[-3:]:
        mins["FWD0"][m] = 5

    plain = two_part_projection(points, mins, cells, upto=MATCHDAYS[-1] + 1)
    scaled = two_part_projection(
        points, mins, cells, upto=MATCHDAYS[-1] + 1, minutes_weighted=True
    )
    assert scaled["FWD0"] < plain["FWD0"]
    assert scaled["FWD0"] >= plain["FWD0"] * 0.5


def test_two_players_on_the_same_average_are_told_apart_by_how_they_got_there():
    """A nailed-on starter returning four and a rotation player returning eight
    half the time both average four. They are not the same bet, and folding
    them together is what the split is for."""
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: 4.0 for m in MATCHDAYS} for i in market}
    mins = minutes_like(market)

    rotated = "FWD1"
    for n, m in enumerate(MATCHDAYS):
        playing = n % 2 == 0
        points[rotated][m] = 9.0 if playing else ABSENT
        mins[rotated][m] = 90 if playing else 0

    folded = shrunk_projection(points, cells, upto=MATCHDAYS[-1] + 1)
    split = two_part_projection(points, mins, cells, upto=MATCHDAYS[-1] + 1)
    assert abs(folded[rotated] - folded["FWD0"]) < 1.0
    assert split[rotated] != split["FWD0"]


def test_the_minutes_weighting_stays_off_unless_asked_for():
    """It measured worse on both reconstructed seasons — 1.632 against 1.591,
    and 1.636 against 1.591 — over about ten thousand predictions. An earlier
    reading had it winning, on six noisy comparisons from a transfer backtest,
    and it was turned on because of them.

    Pinned so that turning it back on is a decision with a measurement behind
    it rather than a default nobody looked at.
    """
    market = market_of()
    cells = cells_of(market)
    points = {i: {m: 4.0 for m in MATCHDAYS} for i in market}
    mins = minutes_like(market)
    for m in MATCHDAYS[-3:]:
        mins["FWD0"][m] = 10

    default = two_part_projection(points, mins, cells, upto=MATCHDAYS[-1] + 1)
    asked = two_part_projection(
        points, mins, cells, upto=MATCHDAYS[-1] + 1, minutes_weighted=True
    )
    assert default["FWD0"] != asked["FWD0"]
