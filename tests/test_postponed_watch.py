"""The watch on a postponed fixture, and the verdict it is there to reach.

§15.3 reads as scoring a club's players zero when their match is not played
before the next round begins. Nobody has verified it, and it matters: ten of
Manuel's twenty-three were from the two clubs whose round-2 fixture was
postponed, and the projection estimates them as if they will play.

The answer arrives when Sp. Braga–Gil Vicente is finally played, on a date
nobody has set. These tests cover the reasoning that will read it, because by
then the observation will have been made by a scheduled job with nobody
watching, and a verdict nobody checked is worth as little as no verdict.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.models import Fixture, Position

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "watch_postponed", ROOT / "scripts" / "watch_postponed.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def watch():
    return load()


def fixture(round_number: int, home: str, away: str, *, played: bool) -> Fixture:
    goals = {"home_goals": 1, "away_goals": 0} if played else {}
    return Fixture(round_number=round_number, home=home, away=away, **goals)


def reading(outstanding, affected, control) -> dict:
    return {
        "at": "2026-08-23T00:00:00+00:00",
        "outstanding": outstanding,
        "affected": {i: {"total": t, "round": 0, "name": i, "club": "c"} for i, t in affected.items()},
        "control": {i: {"total": t, "round": 0, "name": i, "club": "d"} for i, t in control.items()},
    }


# --- which fixtures are worth watching ---------------------------------------


def test_a_fixture_behind_the_calendar_is_outstanding(watch):
    """Round 2 unplayed while round 3 has been scored. The §15.3 condition."""
    found = watch.outstanding(
        [
            fixture(2, "Sp. Braga", "Gil Vicente", played=False),
            fixture(3, "Sporting", "Alverca", played=True),
        ]
    )
    assert [(f["round"], f["home"]) for f in found] == [(2, "Sp. Braga")]


def test_a_fixture_in_the_current_round_is_not_outstanding(watch):
    """Postponed within its own week says nothing — nobody has moved on yet."""
    found = watch.outstanding(
        [
            fixture(3, "Moreirense", "Benfica", played=False),
            fixture(3, "Sporting", "Alverca", played=True),
        ]
    )
    assert found == []


def test_nothing_is_outstanding_before_a_ball_is_kicked(watch):
    assert watch.outstanding([fixture(1, "a", "b", played=False)]) == []


# --- what changed between two readings ---------------------------------------


def test_only_players_whose_total_moved_are_reported(watch):
    before = {"1": {"total": 5}, "2": {"total": 5}}
    after = {"1": {"total": 9}, "2": {"total": 5}}
    assert watch.moved(before, after) == {"1": 4}


def test_a_player_who_appears_late_is_not_a_change(watch):
    """A new id has no earlier total, so its 'gain' would be invented."""
    assert watch.moved({}, {"1": {"total": 7}}) == {}


# --- the verdict --------------------------------------------------------------

WAITING = [{"round": 2, "home": "Sp. Braga", "away": "Gil Vicente", "played": False}]


def test_no_verdict_while_the_fixture_is_still_outstanding(watch):
    before = reading(WAITING, {"1": 5}, {"9": 5})
    after = reading(WAITING, {"1": 5}, {"9": 8})
    assert watch.verdict(before, after) is None


def test_no_gain_across_the_match_means_the_rule_zeroes_them(watch):
    """The finding that would change how the model projects."""
    before = reading(WAITING, {"1": 5, "2": 5}, {"9": 5})
    after = reading([], {"1": 5, "2": 5}, {"9": 5})
    said = watch.verdict(before, after)
    assert said is not None
    assert "§15.3 zera" in said


def test_a_gain_with_a_still_control_means_the_points_arrive(watch):
    """Nobody unaffected moved, so the affected players' gain has one source."""
    before = reading(WAITING, {"1": 5, "2": 5}, {"9": 5})
    after = reading([], {"1": 11, "2": 8}, {"9": 5})
    said = watch.verdict(before, after)
    assert said is not None
    assert "foram atribuídos" in said


def test_both_groups_moving_settles_nothing(watch):
    """Another round was scored in the same window. Refuse rather than guess."""
    before = reading(WAITING, {"1": 5}, {"9": 5})
    after = reading([], {"1": 11}, {"9": 9})
    said = watch.verdict(before, after)
    assert said is not None
    assert "por decidir" in said.lower()


def test_the_verdict_names_the_match_it_read(watch):
    before = reading(WAITING, {"1": 5}, {"9": 5})
    after = reading([], {"1": 5}, {"9": 5})
    said = watch.verdict(before, after)
    assert "Sp. Braga" in said and "Gil Vicente" in said and "jornada 2" in said


# --- it is a step of the routine ----------------------------------------------


def test_the_routine_runs_it(watch):
    spec = importlib.util.spec_from_file_location(
        "routine", ROOT / "scripts" / "routine.py"
    )
    routine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routine)
    step = next((s for s in routine.STEPS if s.slug == "adiado"), None)
    assert step is not None, "nothing observes the postponed fixture on a timer"
    assert "scripts/watch_postponed.py" in step.command
    assert not step.needs_league, "this reads the public market and nothing else"


# --- the watch has to survive the fixture being played ------------------------
#
# The bug this whole file exists to prevent, and it was here. `observe` derived
# the watched clubs from the fixtures still OUTSTANDING, and a fixture stops
# being outstanding the moment it is played — so at the one reading that
# matters the watched set emptied, `verdict` compared nothing against nothing,
# and announced "§15.3 zeroes them" whatever had actually happened.
#
# The control emptied from the other side at the same instant: it was
# `everyone who played - the watched`, so those fifty-seven players joined the
# pool the first forty ids are sliced from.


class FakeMarket:
    """Two watched clubs, one control club, and a switch for the fixture."""

    def __init__(self, played: bool, totals: dict[str, int]) -> None:
        self.played = played
        self.totals = totals

    def fixtures(self):
        def game(home, away, round_number, done):
            goals = {"home_goals": 1, "away_goals": 0} if done else {}
            return Fixture(round_number=round_number, home=home, away=away, **goals)

        return [
            game("Braga", "GilV", 2, self.played),
            game("X", "Y", 2, True),
            game("X", "Y", 3, True),
        ]

    def search(self, position):
        if position is not Position.GK:
            return []
        return [
            _Player("1", "Braga", self.totals["1"]),
            _Player("2", "GilV", self.totals["2"]),
            _Player("9", "X", self.totals["9"]),
        ]


class _Player:
    def __init__(self, pid, club, total):
        self.id, self.name, self.club = pid, "p" + pid, club
        self.points_total, self.points_round = total, 0


def test_the_watched_clubs_survive_the_fixture_being_played(watch):
    before = watch.observe(FakeMarket(False, {"1": 10, "2": 10, "9": 10}))
    assert before["watching"] == ["Braga", "GilV"]

    after = watch.observe(FakeMarket(True, {"1": 16, "2": 14, "9": 10}), before)
    assert after["watching"] == ["Braga", "GilV"], (
        "the watch emptied the moment the fixture was played — the verdict "
        "will compare nothing against nothing"
    )
    assert set(after["affected"]) == set(before["affected"])


def test_the_control_keeps_its_members_across_the_transition(watch):
    before = watch.observe(FakeMarket(False, {"1": 10, "2": 10, "9": 10}))
    after = watch.observe(FakeMarket(True, {"1": 16, "2": 14, "9": 10}), before)
    assert set(after["control"]) == set(before["control"]), (
        "the control changed membership at the one reading that matters"
    )


def test_points_arriving_is_reported_as_points_arriving(watch):
    """The case the broken version could never reach."""
    before = watch.observe(FakeMarket(False, {"1": 10, "2": 10, "9": 10}))
    after = watch.observe(FakeMarket(True, {"1": 16, "2": 14, "9": 10}), before)
    said = watch.verdict(before, after)
    assert said is not None
    assert "foram atribuídos" in said
    assert "§15.3 zera" not in said


def test_points_not_arriving_is_still_reported_correctly(watch):
    before = watch.observe(FakeMarket(False, {"1": 10, "2": 10, "9": 10}))
    after = watch.observe(FakeMarket(True, {"1": 10, "2": 10, "9": 10}), before)
    said = watch.verdict(before, after)
    assert said is not None
    assert "§15.3 zera" in said
