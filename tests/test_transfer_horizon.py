"""The rounds the page's weekly transfer is priced over, without the network.

Since cc4ae93 the page prices its one transfer a round over the next five
rounds, each against its own opponent. The search that does it is tested in
test_optimise.py; what it is HANDED was not, because it was built inline in
`model_sheet`, which reads the live site. Both reviews of 21/09/2026 named the
gap. So the building is lifted into small functions and tested here: which
rounds, what each man is worth in each of them, and the calendar they come
from.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.models import ClubRecord, Fixture, Position
from liga_record_mcp.stats import adjust_for_fixture, fixture_multipliers

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def week(defensive: float, attacking: float, opponent: str = "X") -> dict:
    return {
        "opponent": opponent,
        "at_home": True,
        "defensive": defensive,
        "attacking": attacking,
    }


# --- which rounds -------------------------------------------------------------


def test_the_horizon_is_five_rounds_starting_with_the_one_being_decided(dash):
    assert dash.LOOKAHEAD == 5
    assert dash.horizon_rounds(8) == [8, 9, 10, 11, 12]


def test_the_horizon_shrinks_at_the_end_of_the_season_and_never_passes_it(dash):
    last = dash.LAST_MATCHDAY
    assert dash.horizon_rounds(last - 2) == [last - 2, last - 1, last]
    assert dash.horizon_rounds(last) == [last]
    # Past the last round there is nothing ahead, but the round asked about is
    # still there, so `model_sheet` can read its week without a KeyError.
    assert dash.horizon_rounds(last + 1) == [last + 1]


# --- what each man is worth in each round -------------------------------------


def test_each_round_moves_what_a_man_returns_by_that_rounds_opponent(dash):
    players = {
        "striker": SimpleNamespace(club="Benfica", position=Position.FWD),
        "back": SimpleNamespace(club="Porto", position=Position.DEF),
    }
    returns = {"striker": 5.0, "back": 3.0}
    weeks = [
        {"Benfica": week(0.8, 1.4), "Porto": week(1.2, 0.9)},
        {"Benfica": week(1.3, 0.7), "Porto": week(0.9, 1.1)},
    ]

    ahead = dash.transfer_horizon(returns, players, weeks)

    assert len(ahead) == 2
    for moved, by_club in zip(ahead, weeks):
        for player_id, person in players.items():
            this = by_club[person.club]
            assert moved[player_id] == pytest.approx(
                adjust_for_fixture(
                    returns[player_id], person.position,
                    this["defensive"], this["attacking"],
                )
            )
    # And the two rounds really are different weeks for the same man.
    assert ahead[0]["striker"] != pytest.approx(ahead[1]["striker"])


def test_a_club_with_no_fixture_in_a_round_keeps_its_season_value(dash):
    """No week is not a hard week. Priced as the page priced it before."""
    players = {
        "idle": SimpleNamespace(club="Moreirense", position=Position.MID),
        "busy": SimpleNamespace(club="Benfica", position=Position.MID),
    }
    returns = {"idle": 4.0, "busy": 4.0}
    ahead = dash.transfer_horizon(returns, players, [{"Benfica": week(0.7, 1.5)}])
    assert ahead[0]["idle"] == 4.0
    assert ahead[0]["busy"] != 4.0


def test_every_round_prices_every_man(dash):
    """`improve_squad` averages each man over the rounds; a gap would drop him
    to zero in a round and make a steady player look like a bad week."""
    players = {i: SimpleNamespace(club=c, position=Position.GK) for i, c in
               (("a", "Benfica"), ("b", "Braga"), ("c", "Arouca"))}
    returns = {"a": 3.0, "b": 2.0, "c": 1.0}
    weeks = [{"Benfica": week(1.0, 1.0)}, {}, {"Braga": week(1.1, 0.9)}]
    ahead = dash.transfer_horizon(returns, players, weeks)
    assert [set(moved) for moved in ahead] == [set(returns)] * 3


# --- the calendar they come from ----------------------------------------------


class Calendar:
    def __init__(self, fixtures):
        self._fixtures = fixtures

    def fixtures(self):
        return list(self._fixtures)


def test_the_weeks_cover_every_round_asked_and_download_the_records_once(
    dash, monkeypatch
):
    fixtures = [
        Fixture(round_number=8, home="Benfica", away="Porto"),
        Fixture(round_number=9, home="Porto", away="Braga"),
        # A round outside the horizon, which must not leak into it.
        Fixture(round_number=13, home="Benfica", away="Braga"),
    ]
    records = {
        "Benfica": ClubRecord(club="Benfica", matches=10, goals_for=25, goals_against=6),
        "Porto": ClubRecord(club="Porto", matches=10, goals_for=20, goals_against=8),
        "Braga": ClubRecord(club="Braga", matches=10, goals_for=14, goals_against=12),
    }
    downloads = []

    class Records:
        def __init__(self, timeout=None):
            pass

        def club_records(self):
            downloads.append(1)
            return records

    monkeypatch.setattr(dash.mcp, "_market", Calendar(fixtures))
    monkeypatch.setattr(dash, "OpenFootballClient", Records)

    weeks = dash.fixture_weeks([8, 9, 10])

    assert list(weeks) == [8, 9, 10]
    assert len(downloads) == 1, "the club records are one download, not one a round"
    # A round with no fixtures is there, and empty: no week, not a hard week.
    assert weeks[10] == {}
    # Both sides of every match, the right way round.
    assert set(weeks[8]) == {"Benfica", "Porto"}
    assert weeks[8]["Benfica"]["opponent"] == "Porto" and weeks[8]["Benfica"]["at_home"]
    assert weeks[8]["Porto"]["opponent"] == "Benfica" and not weeks[8]["Porto"]["at_home"]
    assert set(weeks[9]) == {"Porto", "Braga"}
    # And the multipliers are the ones `fixture_multipliers` gives for them.
    known = list(records.values())
    league_ga = sum(r.goals_against_per_match for r in known) / len(known)
    league_gf = sum(r.goals_for_per_match for r in known) / len(known)
    benfica, porto = records["Benfica"], records["Porto"]
    defensive, attacking = fixture_multipliers(
        benfica.goals_against_per_match, benfica.goals_for_per_match,
        porto.goals_against_per_match, porto.goals_for_per_match,
        league_ga, league_gf, at_home=True,
    )
    assert weeks[8]["Benfica"]["defensive"] == pytest.approx(defensive)
    assert weeks[8]["Benfica"]["attacking"] == pytest.approx(attacking)


# --- where the horizon goes, and where it does not ----------------------------


def test_only_the_weekly_transfer_gets_the_horizon(dash):
    """The eleven moves on this week's opponent and the ideal 23 on season
    values; the horizon was measured for one move a round and nothing else."""
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    sheet = source[source.index("def model_sheet("):source.index("def model_section(")]
    weekly = sheet[sheet.index("improved = improve_squad("):sheet.index("move = None")]
    assert "max_swaps=1" in weekly and "horizon=ahead" in weekly
    ideal = sheet[sheet.index("settled = improve_squad("):sheet.index("chosen = settled")]
    assert "horizon" not in ideal
    assert "weeks = weeks_ahead[round_number]" in sheet
