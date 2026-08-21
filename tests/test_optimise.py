"""Picking the best legal team, exactly rather than nearly.

These build the ruler the rest of the project had no way to draw. A manager
scoring five points a round cannot tell whether that is good until he knows
what a perfect season, a well-chosen one and a careless one were worth — and
none of those numbers exist until something works them out from a season of
real scores.

Two things could go wrong quietly. The team sheet could be one Liga Record
would reject, in which case every benchmark answers a different game; and the
squad could be a good greedy guess presented as an optimum. Both are checked
against something independent: the real §6.13 validator, and brute force.
"""

from __future__ import annotations

import itertools
import random

from liga_record_mcp.models import (
    BENCH_SIZE,
    XI_SIZE,
    Player,
    Position,
    Selection,
    Squad,
)
from liga_record_mcp.optimise import (
    best_eleven,
    best_squad_under_budget,
    improve_squad,
    legal_shapes,
    squad_value,
)
from liga_record_mcp.rules import legal_formations, validate_selection
from liga_record_mcp.stats import UNUSED_PENALTY

QUOTA = {Position.GK: 3, Position.DEF: 8, Position.MID: 8, Position.FWD: 4}


def row(identifier, position, value=1_000_000):
    return {"id": identifier, "position": position, "value": value}


def full_squad():
    return [
        row(f"{position.value}{n}", position)
        for position, count in QUOTA.items()
        for n in range(count)
    ]


def as_player(entry):
    return Player(
        id=entry["id"],
        name=entry["id"],
        position=entry["position"],
        club="Benfica",
        value=entry["value"],
        initial_value=entry["value"],
    )


# --------------------------------------------------------------------------
# The shapes
# --------------------------------------------------------------------------


def test_the_shapes_agree_with_the_rules_that_derive_them_separately():
    """Two derivations of one regulation drifting apart is exactly the sort of
    thing that never announces itself."""
    assert {f"{d}-{m}-{f}" for d, m, f in legal_shapes()} == set(legal_formations())


def test_every_shape_is_eleven_players():
    assert all(1 + d + m + f == XI_SIZE for d, m, f in legal_shapes())


# --------------------------------------------------------------------------
# The eleven
# --------------------------------------------------------------------------


def test_the_sheet_is_one_the_real_validator_accepts():
    """The benchmark is worthless if the team could not have been fielded."""
    squad = full_squad()
    points = {entry["id"]: float(n) for n, entry in enumerate(squad)}
    sheet = best_eleven(squad, points)

    check = validate_selection(
        Squad(
            team_id=1,
            team_name="test",
            players=tuple(as_player(entry) for entry in squad),
        ),
        Selection(
            starters=tuple(sheet["starters"]),
            bench=tuple(sheet["bench"]),
            captain=sheet["captain"],
            coach_id="c1",
        ),
    )
    assert check.is_valid, check.violations
    assert check.formation == sheet["formation"]


def test_the_shape_is_chosen_and_not_assumed():
    """Three good forwards and poor defenders is a 3-4-3; the reverse is a
    5-4-1. A fixed shape gets one of them wrong all season.

    The values are graded rather than flat because a flat "everyone else scores
    one" leaves several shapes tied on exactly the same total — an XI is always
    one keeper and ten outfielders however they are arranged."""
    squad = full_squad()

    def graded(best, middling):
        return {
            entry["id"]: {best: 20.0, middling: 5.0}.get(entry["position"], 1.0)
            for entry in squad
        }

    assert best_eleven(squad, graded(Position.FWD, Position.MID))["formation"] == "3-4-3"
    assert best_eleven(squad, graded(Position.DEF, Position.MID))["formation"] == "5-4-1"


def test_the_captain_is_the_best_starter_and_is_paid_twice():
    """§10.3(l). Because the armband doubles rather than replaces, the eleven
    that scores most also contains the best captain — which is why this can be
    settled by sorting instead of by searching."""
    squad = full_squad()
    points = {entry["id"]: 1.0 for entry in squad}
    points["FWD0"] = 30.0
    sheet = best_eleven(squad, points)
    assert sheet["captain"] == "FWD0"
    assert sheet["points"] == 10 * 1.0 + 30.0 + 30.0


def test_a_player_with_no_score_is_charged_for_not_playing():
    """§10.3(i) pays -1, and a manager owned him. Scoring him zero would make
    absences free and quietly flatter every benchmark built on this."""
    squad = full_squad()
    sheet = best_eleven(squad, {})
    assert sheet["points"] == XI_SIZE * UNUSED_PENALTY + UNUSED_PENALTY


def test_a_squad_that_cannot_field_a_legal_eleven_says_so():
    """Two players is not a team, and a best effort would put an illegal sheet
    into a benchmark without anything noticing."""
    thin = [row("GK0", Position.GK), row("DEF0", Position.DEF)]
    assert best_eleven(thin, {}) is None


def test_the_bench_carries_the_spare_keeper():
    """§11 substitutes like for like, so a bench with no keeper cannot cover
    the one position that has no cover. A strategy, not a rule."""
    squad = full_squad()
    points = {entry["id"]: float(n) for n, entry in enumerate(squad)}
    sheet = best_eleven(squad, points)
    assert len(sheet["bench"]) == BENCH_SIZE
    assert any(name.startswith("GK") for name in sheet["bench"])
    assert not set(sheet["bench"]) & set(sheet["starters"])


def test_the_eleven_is_the_best_one_and_brute_force_agrees():
    """The claim is optimality, so it is checked against enumeration rather
    than against the reasoning that produced it."""
    rng = random.Random(11)
    squad = full_squad()
    points = {entry["id"]: round(rng.uniform(-1, 15), 2) for entry in squad}
    sheet = best_eleven(squad, points)

    by_position = {p: [e["id"] for e in squad if e["position"] is p] for p in Position}
    best = float("-inf")
    for defenders, midfielders, forwards in legal_shapes():
        for combination in itertools.product(
            itertools.combinations(by_position[Position.GK], 1),
            itertools.combinations(by_position[Position.DEF], defenders),
            itertools.combinations(by_position[Position.MID], midfielders),
            itertools.combinations(by_position[Position.FWD], forwards),
        ):
            chosen = [i for group in combination for i in group]
            total = sum(points[i] for i in chosen) + max(points[i] for i in chosen)
            best = max(best, total)
    assert abs(sheet["points"] - best) < 1e-9


# --------------------------------------------------------------------------
# The twenty-three
# --------------------------------------------------------------------------


def market(count=9, seed=7):
    rng = random.Random(seed)
    return [
        row(
            f"{position.value}{n}",
            position,
            rng.choice([500_000, 1_000_000, 2_500_000]),
        )
        for position in Position
        for n in range(count)
    ]


def test_the_quota_is_exact_and_the_budget_is_not_exceeded():
    """§6.6 and §6.4 are hard limits, not preferences."""
    players = market()
    points = {entry["id"]: float(len(entry["id"])) + n for n, entry in enumerate(players)}
    bought = best_squad_under_budget(players, points, budget=20_000_000)
    chosen = set(bought["players"])
    counts = {
        position: sum(
            1 for e in players if e["id"] in chosen and e["position"] is position
        )
        for position in Position
    }
    assert counts == QUOTA
    assert bought["cost"] <= 20_000_000


def test_the_optimum_is_the_optimum_and_brute_force_agrees():
    """A greedy answer looks identical on easy inputs and is wrong on the ones
    that matter — a budget tight enough that one position's bargain is what
    pays for another position's star."""
    rng = random.Random(3)
    quota = {Position.GK: 1, Position.DEF: 2, Position.MID: 1, Position.FWD: 1}
    players = [
        row(
            f"{position.value}{n}",
            position,
            rng.choice([500_000, 1_000_000, 1_500_000]),
        )
        for position in Position
        for n in range(4)
    ]
    points = {entry["id"]: round(rng.uniform(0, 50), 1) for entry in players}
    budget = 4_000_000

    bought = best_squad_under_budget(players, points, budget=budget, quota=quota)

    by_position = {p: [e for e in players if e["position"] is p] for p in Position}
    best = float("-inf")
    for combination in itertools.product(
        *(itertools.combinations(by_position[p], n) for p, n in quota.items())
    ):
        picked = [e for group in combination for e in group]
        if sum(e["value"] for e in picked) > budget:
            continue
        best = max(best, sum(points[e["id"]] for e in picked))
    assert abs(bought["points"] - best) < 1e-9


def test_a_budget_too_small_for_a_legal_squad_returns_nothing():
    """Rather than a squad that could not have been bought."""
    players = market()
    points = {entry["id"]: 1.0 for entry in players}
    assert best_squad_under_budget(players, points, budget=1_000_000) is None


def test_a_player_with_no_recorded_season_is_not_bought():
    """He may be excellent. There is no evidence either way, and a quota place
    spent on a blank is not the same as one spent on a zero."""
    players = market()
    points = {entry["id"]: 1.0 for entry in players if entry["id"] != "GK0"}
    bought = best_squad_under_budget(players, points, budget=40_000_000)
    assert "GK0" not in bought["players"]


# --------------------------------------------------------------------------
# Pricing a squad by what the eleven returns
# --------------------------------------------------------------------------


def squad_market(gk_prices=(5_500_000, 4_500_000, 500_000)):
    """A market with keepers at chosen prices, spare cheap ones, and outfielders.

    The spares matter: an earlier version of this had exactly three keepers,
    so the optimiser could not have downgraded one if it had wanted to, and
    the test asserting that it did was unfalsifiable.
    """
    market = {}
    for n, price in enumerate(gk_prices):
        market[f"GK{n}"] = as_player(row(f"GK{n}", Position.GK, price))
    for n in range(len(gk_prices), len(gk_prices) + 5):
        market[f"GK{n}"] = as_player(row(f"GK{n}", Position.GK, 500_000))
    for position, count in ((Position.DEF, 20), (Position.MID, 20), (Position.FWD, 12)):
        for n in range(count):
            price = 500_000 if n >= 8 else 3_000_000 - n * 300_000
            market[f"{position.value}{n}"] = as_player(
                row(f"{position.value}{n}", position, price)
            )
    return market


def squad_from(market):
    picked = []
    for position, count in QUOTA.items():
        picked.extend(
            [i for i in market if market[i].position is position][:count]
        )
    return picked


def test_a_third_goalkeeper_is_worth_almost_nothing():
    """Only one keeper starts, and two do not both vanish in the same week. A
    squad valuation that cannot see this spends ten million of forty on two
    good goalkeepers — which is exactly what the season-total objective did."""
    market = squad_market()
    squad = squad_from(market)
    returns = {i: 4.0 for i in squad}
    playing = {i: 0.95 for i in squad}

    with_a_good_reserve = squad_value(squad, market, returns, playing, draws=200)
    returns_poor_reserve = {**returns, "GK1": 0.5, "GK2": 0.5}
    with_a_poor_reserve = squad_value(
        squad, market, returns_poor_reserve, playing, draws=200
    )
    # Downgrading two keepers who never play should barely move the sheet.
    assert abs(with_a_good_reserve - with_a_poor_reserve) < 0.5


def test_depth_is_worth_something_where_it_is_actually_needed():
    """A fourth centre back earns his place because the third one gets injured.
    A valuation that scored only the eleven would price him at zero and buy a
    squad that collapses the first time anyone is unavailable."""
    market = squad_market()
    squad = squad_from(market)
    returns = {i: 4.0 for i in squad}

    reliable = squad_value(squad, market, returns, {i: 0.98 for i in squad}, draws=200)
    fragile = squad_value(squad, market, returns, {i: 0.55 for i in squad}, draws=200)
    assert fragile < reliable

    # With the same fragile starters, better cover is worth having.
    thin = {**{i: 0.55 for i in squad}}
    covered = {**thin, **{i: 0.98 for i in squad if i.startswith(("DEF6", "DEF7"))}}
    assert squad_value(squad, market, returns, covered, draws=200) > squad_value(
        squad, market, returns, thin, draws=200
    )


def test_the_same_squad_scores_the_same_twice():
    """Sampling has to be reproducible, or two runs of the optimiser disagree
    and neither can be checked."""
    market = squad_market()
    squad = squad_from(market)
    returns = {i: 4.0 for i in squad}
    playing = {i: 0.7 for i in squad}
    assert squad_value(squad, market, returns, playing, seed=1) == squad_value(
        squad, market, returns, playing, seed=1
    )


def test_the_optimiser_escapes_a_squad_that_has_spent_everything():
    """The failure this exists to fix. A squad at the budget can only swap for
    someone cheaper, so every move that would free money looks like a loss on
    its own and is refused — and two expensive goalkeepers stay put for the
    season while a better forward sits unbought on the market.

    The squad is built by hand rather than taken off the top of the market,
    because a squad that already holds the best players at every position has
    nothing to spend freed money on, and the test would pass by having nothing
    to do.
    """
    market = squad_market()
    # Two dear keepers, and forwards who are cheap and poor.
    squad = (
        ["GK0", "GK1", "GK2"]
        + [f"DEF{n}" for n in range(8)]
        + [f"MID{n}" for n in range(8)]
        + [f"FWD{n}" for n in range(8, 12)]
    )
    budget = sum(market[i].value for i in squad)

    returns = {i: 1.0 for i in market}
    returns["GK0"] = 4.0
    # The good forwards are the dear ones, and none of them is in the squad.
    for n in range(4):
        returns[f"FWD{n}"] = 20.0 - n
    playing = {i: 0.95 for i in market}

    before = squad_value(squad, market, returns, playing, draws=400)
    better = improve_squad(
        squad, market, returns, playing, budget=budget, draws=400, passes=3
    )
    assert better["expected_round"] > before
    assert better["cost"] <= budget

    spent_on_keepers = sum(
        market[i].value for i in better["players"] if market[i].position is Position.GK
    )
    assert spent_on_keepers < sum(
        market[i].value for i in squad if market[i].position is Position.GK
    )
    # And the money went where it was worth something.
    assert any(f"FWD{n}" in better["players"] for n in range(4))


def test_the_optimiser_never_breaks_the_quota_or_the_budget():
    """§6.6 and §6.4 hold at every step, not just at the end."""
    market = squad_market()
    squad = squad_from(market)
    budget = sum(market[i].value for i in squad)
    returns = {i: float(len(i)) for i in market}
    returns["FWD0"] = 30.0
    playing = {i: 0.9 for i in market}

    better = improve_squad(
        squad, market, returns, playing, budget=budget, draws=16, passes=2
    )
    assert len(set(better["players"])) == sum(QUOTA.values())
    assert better["cost"] <= budget
    counts = {
        position: sum(
            1 for i in better["players"] if market[i].position is position
        )
        for position in Position
    }
    assert counts == QUOTA


def test_a_squad_nothing_improves_comes_back_untouched():
    """Churn for its own sake would cost a transfer and gain nothing."""
    market = squad_market()
    squad = squad_from(market)
    # Distinct values rather than ties: with everyone on the same number the
    # sheet is flat, several squads score identically, and "it did not churn"
    # stops being a claim about the optimiser at all.
    returns = {
        i: (100.0 - n if i in squad else 0.5 - n / 1000)
        for n, i in enumerate(market)
    }
    playing = {i: 0.95 for i in market}
    better = improve_squad(
        squad, market, returns, playing, budget=99_000_000, draws=32, passes=2
    )
    assert set(better["players"]) == set(squad)
    assert better["swaps"] == []
