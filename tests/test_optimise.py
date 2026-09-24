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

import pytest

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
    expected_round_points,
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

    with_a_good_reserve = expected_round_points(squad, market, returns, playing, draws=200)
    returns_poor_reserve = {**returns, "GK1": 0.5, "GK2": 0.5}
    with_a_poor_reserve = expected_round_points(
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

    reliable = expected_round_points(squad, market, returns, {i: 0.98 for i in squad}, draws=200)
    fragile = expected_round_points(squad, market, returns, {i: 0.55 for i in squad}, draws=200)
    assert fragile < reliable

    # With the same fragile starters, better cover is worth having.
    thin = {**{i: 0.55 for i in squad}}
    covered = {**thin, **{i: 0.98 for i in squad if i.startswith(("DEF6", "DEF7"))}}
    assert expected_round_points(squad, market, returns, covered, draws=200) > expected_round_points(
        squad, market, returns, thin, draws=200
    )


def test_the_same_squad_scores_the_same_twice():
    """Sampling has to be reproducible, or two runs of the optimiser disagree
    and neither can be checked."""
    market = squad_market()
    squad = squad_from(market)
    returns = {i: 4.0 for i in squad}
    playing = {i: 0.7 for i in squad}
    assert expected_round_points(squad, market, returns, playing, seed=1) == expected_round_points(
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

    # Outfielders are worth what they cost, so freeing money by downgrading one
    # is a real loss. The surplus keeper is the only dead money on the books —
    # one of them starts, two do not both vanish in the same week, and the man
    # at four and a half million returns exactly what the man at half a million
    # does.
    #
    # That distinction has to be IN THE FIXTURE. With every outfielder flat on
    # 1.0 there were a dozen equally good ways to free the same money, worth the
    # same to a hundredth of a point, and which one got taken was decided by
    # whichever the search happened to read first. The assertion below then
    # looked like a claim about surplus keepers and was really a claim about
    # reading order — it passed for years on a search that sold the GOOD keeper
    # and bought him back twice, which is churn, not escape.
    returns = {
        i: (
            market[i].value / 1_000_000
            if market[i].position in (Position.DEF, Position.MID)
            else 1.0
        )
        for i in market
    }
    returns["GK0"] = 4.0
    # The good forwards are the dear ones, and none of them is in the squad.
    for n in range(4):
        returns[f"FWD{n}"] = 20.0 - n
    playing = {i: 0.95 for i in market}

    before = expected_round_points(squad, market, returns, playing, draws=400)
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


def test_the_february_allowance_is_spent_to_the_last_transfer():
    """§6.9 gives six for the month, and six is what should be spent.

    The cap used to be PREDICTED rather than counted: a pair move was priced at
    two transfers whatever it did, so once four had gone the pair search
    refused to look at all — even for a move whose downgrade leg sold a man
    bought earlier in the same window, which costs the allowance nothing. The
    window closed with transfers unspent and a squad that six moves would have
    improved.

    Counting the squad the move would leave behind gets it right in both
    directions, and there is no reason to predict what is right there to be
    counted. `season_report` already agrees — it reports the window as
    `len(set(held) - set(rebuilt))`, the same net difference.

    This is the diff's largest behaviour change and it sits behind
    `--window all`, which no other test and no default run reaches.
    """
    market = squad_market()
    squad = (
        ["GK0", "GK1", "GK2"]
        + [f"DEF{n}" for n in range(8)]
        + [f"MID{n}" for n in range(8)]
        + [f"FWD{n}" for n in range(8, 12)]
    )
    budget = sum(market[i].value for i in squad)
    returns = {
        i: (
            market[i].value / 1_000_000
            if market[i].position in (Position.DEF, Position.MID)
            else 1.0
        )
        for i in market
    }
    returns["GK0"] = 4.0
    for n in range(4):
        returns[f"FWD{n}"] = 20.0 - n
    playing = {i: 0.95 for i in market}

    allowance = 6
    capped = improve_squad(
        squad, market, returns, playing, budget=budget, draws=128,
        max_swaps=allowance,
    )
    spent = len(set(squad) - set(capped["players"]))
    # Not merely "within the cap" — a search that made one move would pass
    # that. The uncapped climb takes far more than six here, so every one of
    # the six is a move worth making.
    assert spent == allowance, f"spent {spent} of {allowance}"
    assert capped["cost"] <= budget
    assert capped["expected_round"] > expected_round_points(
        squad, market, returns, playing, draws=128
    )


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


def test_the_climb_lands_in_the_same_place_whatever_order_it_reads_the_market_in():
    """The measurement this whole search exists to make possible.

    Candidates arrive in whatever order a dict happens to hold them, and every
    order is equally defensible, so a search that takes the first move that
    helps is settling the squad on a coin toss. It is not a small toss. Six
    shuffles of one market returned 2025/26 seasons of 1360, 1398, 1418, 1421,
    1433 and 1496 — a spread of 136 points — while the six squads they produced
    differed by 0.26 a round on the objective actually being maximised. The
    search could barely tell them apart; the season gap was luck downstream of
    a tie-break. An effect worth 11.7 points was being measured against that
    and could not be seen at all.

    So the answer has to be a function of the SET of candidates and of nothing
    else. The list comes back in the order its slots were filled, which
    legitimately follows the order they went in; WHO is in it, which moves were
    taken and what they were worth must not move at all.
    """
    market = squad_market()
    squad = (
        ["GK0", "GK1", "GK2"]
        + [f"DEF{n}" for n in range(8)]
        + [f"MID{n}" for n in range(8)]
        + [f"FWD{n}" for n in range(8, 12)]
    )
    budget = sum(market[i].value for i in squad)
    # Deliberately thick with ties: every spare keeper on 1.0, every cheap
    # defender on 0.5, every surplus forward on 1.0. A tie is the crack the
    # ordering used to get in through, so a fixture without any would pass
    # whether or not this had been fixed.
    returns = {
        i: (
            market[i].value / 1_000_000
            if market[i].position in (Position.DEF, Position.MID)
            else 1.0
        )
        for i in market
    }
    returns["GK0"] = 4.0
    for n in range(4):
        returns[f"FWD{n}"] = 20.0 - n
    playing = {i: 0.95 for i in market}

    def climbed(order_seed):
        """The same problem, read in a different order. Both ends are shuffled:
        the market the candidates come out of, and the squad handed in — the
        real caller gets its squad from a dynamic program that returns the same
        twenty-three in a different order every run."""
        shuffled = list(market)
        random.Random(order_seed).shuffle(shuffled)
        held = list(squad)
        random.Random(f"squad:{order_seed}").shuffle(held)
        return improve_squad(
            held,
            {i: market[i] for i in shuffled},
            returns,
            playing,
            budget=budget,
            draws=64,
            passes=2,
        )

    first = climbed(0)
    # A search that never moves would pass everything below without meaning it.
    assert first["swaps"]
    for order_seed in range(1, 5):
        again = climbed(order_seed)
        assert set(again["players"]) == set(first["players"])
        assert again["swaps"] == first["swaps"]
        assert again["expected_round"] == first["expected_round"]


# --- a horizon of rounds ahead --------------------------------------------------


def graded_problem():
    """The squad the two tests above climb from: spent to the last euro, a good
    keeper among three, and the four good forwards all outside it."""
    market = squad_market()
    squad = (
        ["GK0", "GK1", "GK2"]
        + [f"DEF{n}" for n in range(8)]
        + [f"MID{n}" for n in range(8)]
        + [f"FWD{n}" for n in range(8, 12)]
    )
    returns = {
        i: (
            market[i].value / 1_000_000
            if market[i].position in (Position.DEF, Position.MID)
            else 1.0
        )
        for i in market
    }
    returns["GK0"] = 4.0
    for n in range(4):
        returns[f"FWD{n}"] = 20.0 - n
    playing = {i: 0.95 for i in market}
    return market, squad, sum(market[i].value for i in squad), returns, playing


def test_without_a_horizon_the_search_is_the_one_it_always_was():
    """The horizon is new, and everything the search did before must still be
    what it does without one — to the last player, swap and hundredth.

    The numbers below were read off the search the day the horizon was added,
    BEFORE the change, on the whole climb and on the one transfer a round the
    page asks for. A horizon of one round equal to the season values must give
    them too: it is the same arithmetic, divided by one. If `expected_round_points`
    itself ever changes on purpose, read them again from the new code; what
    this holds is that the horizon changed nothing on its own.
    """
    market, squad, budget, returns, playing = graded_problem()
    before = {
        "whole climb": (
            dict(budget=budget, draws=64, passes=2),
            {
                "players": [
                    "DEF0", "DEF1", "DEF10", "DEF11", "DEF2", "DEF3", "DEF4",
                    "DEF7", "FWD0", "FWD1", "FWD2", "FWD3", "GK0", "GK2", "GK3",
                    "MID0", "MID1", "MID10", "MID11", "MID2", "MID4", "MID6",
                    "MID7",
                ],
                "swaps": [
                    {"out": "GK1 + FWD10", "in": "GK3 + FWD0", "gain": 33.359},
                    {"out": "DEF5 + FWD11", "in": "DEF10 + FWD1", "gain": 16.359},
                    {"out": "MID3 + FWD8", "in": "MID10 + FWD2", "gain": 15.161},
                    {"out": "DEF3 + FWD9", "in": "DEF11 + FWD3", "gain": 2.158},
                    {"out": "MID5 + DEF6", "in": "MID11 + DEF3", "gain": 0.342},
                ],
                "expected_round": 97.86,
                "cost": 43_600_000,
            },
        ),
        "one transfer": (
            dict(budget=budget + 3_000_000, draws=64, max_swaps=1),
            {
                "players": [
                    "DEF0", "DEF1", "DEF2", "DEF3", "DEF4", "DEF5", "DEF6",
                    "DEF7", "FWD0", "FWD11", "FWD8", "FWD9", "GK0", "GK1", "GK2",
                    "MID0", "MID1", "MID2", "MID3", "MID4", "MID5", "MID6",
                    "MID7",
                ],
                "swaps": [{"out": "FWD10", "in": "FWD0", "gain": 33.359}],
                "expected_round": 63.84,
                "cost": 46_200_000,
            },
        ),
    }
    for name, (settings, expected) in before.items():
        for horizon in (None, [returns]):
            found = improve_squad(
                squad, market, returns, playing, horizon=horizon, **settings
            )
            assert sorted(found["players"]) == expected["players"], name
            assert found["swaps"] == expected["swaps"], name
            assert found["expected_round"] == expected["expected_round"], name
            assert found["cost"] == expected["cost"], name


def test_a_horizon_prefers_a_steady_run_to_one_easy_week():
    """What the horizon is for: a transfer runs to May, not to Saturday.

    X is worth more on the season values, so without a horizon he is the buy.
    But his value is one easy week followed by four hard ones, and Y is the
    same every week and better on the average of the five. Priced over the
    rounds ahead, Y is the buy — and this is the whole of the idea, so it has
    to be visible in one transfer, the one the page asks for each week.
    """
    market = squad_market()
    market["FWDX"] = as_player(row("FWDX", Position.FWD, 500_000))
    market["FWDY"] = as_player(row("FWDY", Position.FWD, 500_000))
    squad = (
        ["GK0", "GK1", "GK2"]
        + [f"DEF{n}" for n in range(8)]
        + [f"MID{n}" for n in range(8)]
        + [f"FWD{n}" for n in range(8, 12)]
    )
    budget = sum(market[i].value for i in squad)
    season = {i: 1.0 for i in market}
    season.update(FWDX=10.0, FWDY=9.0)
    ahead = [
        {**season, "FWDX": week, "FWDY": 9.5} for week in (16.0, 7.5, 7.5, 7.5, 7.5)
    ]
    playing = {i: 0.95 for i in market}

    def bought(horizon):
        found = improve_squad(
            squad, market, season, playing, budget=budget, draws=64,
            max_swaps=1, horizon=horizon,
        )
        return set(found["players"]) - set(squad)

    assert bought(None) == {"FWDX"}
    assert bought(ahead) == {"FWDY"}


# --- a man who has left the league --------------------------------------------


def test_a_squad_member_the_market_dropped_is_named_not_swallowed():
    """The failure that hid: a squad shrinking without saying so.

    Diogo Calila signed for Maghreb Fes on 3 September 2026 and stayed in the
    squad file. `build_dashboard` died on a KeyError; `propose_squad` was worse
    — it filtered him out in silence and reported twenty-two against a rival
    twenty-three as though both were whole.
    """
    from liga_record_mcp.optimise import left_the_league

    market = {"a": object(), "b": object()}
    assert left_the_league(["a", "b"], market) == []
    assert left_the_league(["a", "gone", "b"], market) == ["gone"]


def test_departures_come_back_in_squad_order_and_without_duplicates():
    """Order matters: it is printed as a list of names to act on."""
    from liga_record_mcp.optimise import left_the_league

    assert left_the_league(["x", "y", "z"], {}) == ["x", "y", "z"]
    assert left_the_league([], {"a": object()}) == []


# --------------------------------------------------------------------------
# The eleven with the bench priced in (§11)
# --------------------------------------------------------------------------


def keeper_squad(points, playing):
    """A squad flat everywhere but in goal.

    The three keepers carry the numbers under test, and one of them starts
    whatever the shape, so the choice is readable without the formation
    getting a vote.
    """
    squad = full_squad()
    values = {entry["id"]: 1.0 for entry in squad}
    chances = {entry["id"]: 1.0 for entry in squad}
    values.update(points)
    chances.update(playing)
    return squad, values, chances


KEEPERS = {"GK0": 4.0, "GK1": 3.9, "GK2": 3.0}


def test_without_the_chance_of_playing_nothing_changes():
    """The same numbers that move the choice below leave it alone here."""
    squad, points, _ = keeper_squad(KEEPERS, {})
    sheet = best_eleven(squad, points)
    assert [i for i in sheet["starters"] if i.startswith("GK")] == ["GK0"]
    assert sheet["insured"] == sheet["points"]


def test_an_uncertain_man_is_worth_the_cover_behind_him():
    """GK1 plays nine weeks in ten and is a tenth of a point worse on paper.
    The week he misses is not a -1: GK0 comes on and plays it. Priced that
    way GK1 is the better start, and the better man becomes the insurance."""
    squad, points, playing = keeper_squad(KEEPERS, {"GK1": 0.9})
    plain = best_eleven(squad, points)
    priced = best_eleven(squad, points, playing=playing)
    assert [i for i in plain["starters"] if i.startswith("GK")] == ["GK0"]
    assert [i for i in priced["starters"] if i.startswith("GK")] == ["GK1"]
    assert priced["bench"][0] == "GK0"
    # 3.9 + a tenth of the way from -1 up to GK0's 4.0.
    assert priced["insured"] - priced["points"] == pytest.approx(2 * 0.1 * 5.0)


def test_the_cover_is_the_best_man_left_of_that_position():
    """Only the best substitute of the position counts, not the others: GK2
    moving does nothing while GK0 is the one left over."""
    squad, points, playing = keeper_squad(KEEPERS, {"GK1": 0.9})
    priced = best_eleven(squad, points, playing=playing)
    worse, better = dict(points), dict(points)
    worse["GK2"], better["GK2"] = -1.0, 3.5
    assert best_eleven(squad, worse, playing=playing)["insured"] == pytest.approx(
        priced["insured"]
    )
    assert best_eleven(squad, better, playing=playing)["insured"] == pytest.approx(
        priced["insured"]
    )


def test_without_a_substitute_of_his_position_the_cover_is_the_minus_one():
    """One keeper in the squad: nobody to come on, so the choice and the
    number are the plain ones."""
    squad = [entry for entry in full_squad() if entry["position"] is not Position.GK]
    squad.append(row("GK0", Position.GK))
    points = {entry["id"]: 1.0 for entry in squad}
    points["GK0"] = 2.0
    playing = {entry["id"]: 1.0 for entry in squad}
    playing["GK0"] = 0.5
    sheet = best_eleven(squad, points, playing=playing)
    assert sheet["starters"].count("GK0") == 1
    assert sheet["insured"] == pytest.approx(sheet["points"])


def test_the_armband_is_priced_with_the_bench_too():
    """§11.5 gives the armband to whoever comes on, so the captain's second
    slot is insured like the first. The keeper is the best man on paper here;
    once his cover is counted he is also the one the armband goes to."""
    squad, points, playing = keeper_squad(
        {"GK0": 4.0, "GK1": 3.9, "GK2": 3.8}, {"GK0": 0.8}
    )
    points["FWD0"] = 4.2
    plain = best_eleven(squad, points)
    priced = best_eleven(squad, points, playing=playing)
    assert plain["captain"] == "FWD0"
    # 4.0 + 0.2 x (3.9 - -1) = 4.98, over the forward's 4.2.
    assert priced["captain"] == "GK0"


def test_the_insured_number_is_the_arithmetic_it_claims():
    from liga_record_mcp.optimise import insured

    # Three weeks in four, covered by a substitute worth 2.0 instead of -1.
    assert insured(3.0, 0.75, 2.0, -1.0) == pytest.approx(3.0 + 0.25 * 3.0)
    # No cover: the estimate stands.
    assert insured(3.0, 0.75, -1.0, -1.0) == pytest.approx(3.0)


def test_a_substitute_ranked_out_of_the_reckoning_is_no_cover_at_all():
    """A man the bulletin says is out is ranked a thousand below everyone so
    that he is picked last. He is not a catastrophe behind the starter: he
    will not come on, and that is the -1 the starter already carries."""
    from liga_record_mcp.optimise import insured

    assert insured(3.0, 0.5, -1000.0, -1.0) == pytest.approx(3.0)

    squad, points, playing = keeper_squad({"GK0": 4.0, "GK1": 3.9, "GK2": 3.0}, {})
    points["GK1"] = points["GK1"] - 1000.0  # out this round
    points["GK2"] = points["GK2"] - 1000.0
    playing["GK0"] = 0.8
    sheet = best_eleven(squad, points, playing=playing)
    assert [i for i in sheet["starters"] if i.startswith("GK")] == ["GK0"]
    assert sheet["insured"] == pytest.approx(sheet["points"])


def test_one_substitute_covers_the_position_once():
    """The bench holds one man of each position and §11 sends him on once.
    Two uncertain starters cannot both be promised him — read that way the
    eleven fills up with men who will not play, which cost 255 points a season
    when it was measured."""
    from liga_record_mcp.optimise import _best_at

    ranked = [row("A", Position.FWD), row("B", Position.FWD), row("C", Position.FWD)]
    points = {"A": 3.0, "B": 3.0, "C": 2.0}
    playing = {"A": 0.5, "B": 0.5, "C": 1.0}
    total, chosen, _ = _best_at(
        ranked, 2, lambda r: points[str(r["id"])], playing, -1.0
    )
    assert sorted(str(r["id"]) for r in chosen) == ["A", "B"]
    # 3 + 3 + P(either misses) x (C's 2.0 up from -1), and never (0.5 + 0.5) x 3.
    assert total == pytest.approx(6.0 + 0.75 * 3.0)


def test_a_position_with_several_starters_shares_its_one_substitute():
    """The same fix as above, through the front door and with four forwards,
    where a sum and a product differ. With one starter they agree, and every
    other test of the priced eleven has one keeper."""
    squad = full_squad()
    points = {entry["id"]: 1.0 for entry in squad}
    playing = {entry["id"]: 1.0 for entry in squad}
    # Three forwards who play two weeks in three, and a certain fourth.
    points.update({"FWD0": 3.0, "FWD1": 3.0, "FWD2": 3.0, "FWD3": 2.0})
    playing.update({"FWD0": 0.7, "FWD1": 0.7, "FWD2": 0.7, "FWD3": 1.0})
    sheet = best_eleven(squad, points, playing=playing)
    forwards = sorted(i for i in sheet["starters"] if i.startswith("FWD"))

    # Three forwards start: 9.0, and the cover is worth (2.0 - -1) the weeks
    # ANY of them misses — 1 - 0.7^3 — not once per man, which would be 0.9.
    assert forwards == ["FWD0", "FWD1", "FWD2"]
    shared = (1 - 0.7**3) * 3.0
    apiece = 3 * 0.3 * 3.0
    assert shared < apiece
    keepers_and_rest = sheet["insured"] - sheet["points"]
    # The armband is on a forward, so his slot carries his own insured number
    # on top; the position's share of the gap is the shared one.
    assert keepers_and_rest == pytest.approx(shared + 0.3 * 3.0)
