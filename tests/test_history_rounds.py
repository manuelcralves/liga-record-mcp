"""The round-by-round history, after the site reset its table and renumbered it.

On 15 September 2026 Liga Record began the official phase, wiped the standings
totals and renumbered the rounds: its round 1 is matchday 6. Asked for matchday
6 it answers with an empty row, so `record_history` filed matchday 5's numbers
under matchday 6 — a round already on file was never fetched again — and the
front page showed 52 points for a round worth 63 until someone noticed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.models import FIRST_SCORING_MATCHDAY

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def history():
    return load("record_history")


@pytest.fixture(scope="module")
def dash():
    return load("build_dashboard")


# --- asking in the site's own numbers -------------------------------------------


def test_the_sites_first_round_is_our_first_scoring_matchday(history):
    assert history.site_round(FIRST_SCORING_MATCHDAY) == 1
    assert history.site_round(FIRST_SCORING_MATCHDAY + 1) == 2


def test_a_trial_matchday_has_no_number_on_the_site(history):
    """Round zero fetches the empty row that started all this."""
    with pytest.raises(ValueError, match="trial phase"):
        history.site_round(FIRST_SCORING_MATCHDAY - 1)


def test_the_question_goes_out_renumbered(history):
    asked = []

    class Client:
        def standings(self, *, team, page_size, round_number):
            asked.append(round_number)
            return [], 0

    assert history.fetch_round(Client(), {"id": 1, "name": "Melro"}, 7) is None
    assert asked == [2]


def test_the_row_is_taken_by_id_and_never_by_position(history):
    class Row:
        def __init__(self, team_id):
            self.team_id = team_id
            self.points_round = 63
            self.points_total = 63
            self.position = 4029
            self.position_round = 4029

    class Client:
        def standings(self, *, team, page_size, round_number):
            return [Row(999), Row(156412)], 2

    got = history.fetch_round(Client(), {"id": 156412, "name": "Melro"}, 6)
    assert got == {
        "points_round": 63,
        "points_total": 63,
        "position": 4029,
        "position_round": 4029,
    }


# --- the round the site had not published yet ------------------------------------


def row(points_round, points_total, position):
    return {
        "points_round": points_round,
        "points_total": points_total,
        "position": position,
        "position_round": position,
    }


def test_a_team_repeating_every_number_is_spotted(history):
    assert history.same_as_before(row(52, 240, 6326), row(52, 240, 6326))


def test_one_number_moving_is_enough_to_be_a_round(history):
    assert not history.same_as_before(row(52, 303, 6326), row(52, 240, 6326))


def test_the_first_round_has_nothing_to_repeat(history):
    assert not history.same_as_before(row(49, 49, 1246), None)


def test_a_whole_field_repeating_is_the_site_not_the_football(history):
    """What happened on 15/09: every team answered with the round before."""
    earlier = {"1": row(52, 240, 6326), "2": row(30, 200, 9000)}
    assert not history.published(dict(earlier), earlier)


def test_one_blank_round_does_not_condemn_the_others(history):
    """A team can score nothing and hold his place; the round still happened.

    Judged team by team, his row was refused and the round went missing for him.
    """
    earlier = {"me": row(52, 240, 6326), "blank": row(0, 63, 9000)}
    fetched = {"me": row(63, 303, 4029), "blank": row(0, 63, 9000)}
    assert history.published(fetched, earlier)


def test_nothing_fetched_is_not_a_published_round(history):
    assert not history.published({}, {"1": row(52, 240, 6326)})


# --- two phases, never on one axis -----------------------------------------------


def round_row(number, points, position):
    return {"round": number, "points_round": points, "position": position}


def test_the_bars_chart_the_official_phase_and_name_the_trial_one(dash):
    rounds = [round_row(n, 40 + n, 6000 - n) for n in range(1, FIRST_SCORING_MATCHDAY)]
    rounds.append(round_row(FIRST_SCORING_MATCHDAY, 63, 4029))
    shown, trial = dash.phases(rounds)
    assert [r["round"] for r in shown] == [FIRST_SCORING_MATCHDAY]
    assert [r["round"] for r in trial] == list(range(1, FIRST_SCORING_MATCHDAY))


def test_before_the_official_phase_the_trial_rounds_are_the_chart(dash):
    rounds = [round_row(n, 40 + n, 6000 - n) for n in range(1, 4)]
    shown, trial = dash.phases(rounds)
    assert shown == rounds
    assert trial == []


def test_nothing_at_all_charts_nothing(dash):
    assert dash.phases([]) == ([], [])


# --- what the arrow under the bars says ------------------------------------------


def test_one_official_round_is_not_a_movement(dash):
    swing, story = dash.movement([round_row(FIRST_SCORING_MATCHDAY, 63, 4029)])
    assert swing is None
    assert story == f"a primeira jornada da fase oficial, a {FIRST_SCORING_MATCHDAY}"


def test_one_trial_round_is_not_called_the_official_phase(dash):
    swing, story = dash.movement([round_row(1, 49, 1246)])
    assert swing is None
    assert story == "a primeira jornada da época, a 1"


def test_places_gained_are_counted_between_the_ends(dash):
    swing, story = dash.movement(
        [round_row(6, 63, 4029), round_row(7, 50, 5000), round_row(8, 70, 3029)]
    )
    assert swing == -1000
    assert story == "lugares ganhos entre a jornada 6 e a 8"


def test_standing_still_says_so(dash):
    swing, story = dash.movement([round_row(6, 63, 4029), round_row(7, 40, 4029)])
    assert swing == 0
    assert story == "sem movimento na tabela nacional"
