"""The chips, which are most of the Final Table model and had no way to be played.

WHAT WAS MISSING. `best_chip` was reachable from `backtest_final_table.py` and
from nothing else. The page set out the chip rules in full — one a week from the
lock to matchday 29, three places, bonus chips of five at 18, 24 and 29, lost if
unused — and then never named a chip to play. By the model's own backtest the
entry left alone scores about 104 and the entry with its chips about 382, so
roughly three quarters of the model lived in a path only the backtest ran.

WHAT THESE DEFEND. That the policy is in one place and both callers run it; that
the entry is replayed from what was submitted rather than recomputed; and that a
week worth no chip says so out loud, because a chip is lost if unused and
"spend nothing" has to be a visible decision rather than a blank space.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.final_table import (
    BONUS_REACH,
    BONUS_ROUNDS,
    ENTRY_LOCK_MATCHDAY,
    FIRST_CHIP_ROUND,
    LAST_CHIP_ROUND,
    WEEKLY_REACH,
    apply_chips,
    chip_plan,
    reaches_at,
)
from liga_record_mcp.source import load_final_entry
from liga_record_mcp.source.base import SquadSourceError

ROOT = Path(__file__).resolve().parents[1]


# --- when a chip exists at all ------------------------------------------------


def test_no_chips_before_the_entry_locks():
    """Nothing to correct while the entry is still being written."""
    for matchday in range(1, FIRST_CHIP_ROUND):
        assert reaches_at(matchday) == []


def test_one_chip_a_week_from_the_lock_onwards():
    for matchday in range(FIRST_CHIP_ROUND, LAST_CHIP_ROUND + 1):
        assert WEEKLY_REACH in reaches_at(matchday)


def test_the_window_is_matchdays_6_to_29():
    """PINNED TO ABSOLUTE NUMBERS ON PURPOSE.

    The tests here used to read `FIRST_SCORING_MATCHDAY + 1`, so when that
    constant was corrected from 5 to 6 the chip window slid from 6-29 to 7-29
    and every one of them still passed. A test written against the constant it
    is supposed to be checking cannot catch the constant moving. Manuel caught
    it instead, by reading the site.
    """
    assert FIRST_CHIP_ROUND == 6
    assert LAST_CHIP_ROUND == 29
    assert reaches_at(5) == []
    assert reaches_at(6) == [WEEKLY_REACH]
    assert sum(len(reaches_at(m)) for m in range(1, 36)) == 24 + len(BONUS_ROUNDS)


def test_the_bonus_stacks_with_that_weeks_ordinary_chip():
    """Two chips, not a bigger one — and the weekly is listed first."""
    for matchday in BONUS_ROUNDS:
        assert reaches_at(matchday) == [WEEKLY_REACH, BONUS_REACH]


def test_nothing_after_the_last_chip_round():
    assert reaches_at(LAST_CHIP_ROUND) != []
    assert reaches_at(LAST_CHIP_ROUND + 1) == []


# --- replaying the entry ------------------------------------------------------


def test_an_entry_with_no_chips_is_itself():
    entry = ["a", "b", "c"]
    assert apply_chips(entry, []) == entry


def test_a_chip_moves_one_club_and_shuffles_the_rest():
    assert apply_chips(["a", "b", "c", "d"], [{"clube": "d", "para": 1}]) == [
        "d",
        "a",
        "b",
        "c",
    ]


def test_chips_replay_in_order():
    """Two chips are two moves, and the file's sequence is load-bearing.

    Both of these send a club to first place, so whichever is played LAST ends
    up there. Read the list in the wrong order and the page prices next week's
    chip against a table with the wrong club on top.
    """
    chips = [{"clube": "b", "para": 1}, {"clube": "a", "para": 1}]
    assert apply_chips(["a", "b", "c", "d"], chips) == ["a", "b", "c", "d"]
    assert apply_chips(["a", "b", "c", "d"], list(reversed(chips))) == [
        "b",
        "a",
        "c",
        "d",
    ]


def test_the_entry_is_not_mutated():
    entry = ["a", "b", "c"]
    apply_chips(entry, [{"clube": "c", "para": 1}])
    assert entry == ["a", "b", "c"]


def test_a_chip_naming_a_club_that_is_not_there_is_skipped_not_crashed():
    """A relegated club left in the file must not take the page down."""
    assert apply_chips(["a", "b"], [{"clube": "gone", "para": 1}]) == ["a", "b"]


# --- the policy ---------------------------------------------------------------


def spread(**odds):
    return {club: list(values) for club, values in odds.items()}


def test_a_move_worth_nothing_is_not_made():
    """The order is already the best one, so the chip is kept in the pocket."""
    certain = spread(a=[1.0, 0.0], b=[0.0, 1.0])
    after, plays = chip_plan(["a", "b"], certain, FIRST_CHIP_ROUND)
    assert after == ["a", "b"]
    assert len(plays) == 1
    assert plays[0]["club"] is None


def test_a_move_worth_making_is_made_and_priced():
    """Reversed against a certain distribution: 50 points sit on one swap."""
    certain = spread(a=[1.0, 0.0], b=[0.0, 1.0])
    after, plays = chip_plan(["b", "a"], certain, FIRST_CHIP_ROUND)
    assert after == ["a", "b"]
    assert plays[0]["club"] is not None
    assert plays[0]["gain"] > 0
    assert plays[0]["from"] != plays[0]["to"]


def test_a_bonus_week_prices_the_second_chip_against_the_first_ones_result():
    """Not two independent moves — the bonus sees the board the weekly left."""
    certain = spread(**{f"c{i}": [1.0 if j == i else 0.0 for j in range(6)] for i in range(6)})
    scrambled = ["c5", "c4", "c3", "c2", "c1", "c0"]
    after, plays = chip_plan(scrambled, certain, BONUS_ROUNDS[0])
    assert len(plays) == 2
    assert plays[0]["bonus"] is False and plays[1]["bonus"] is True
    if plays[0]["club"] and plays[1]["club"]:
        assert plays[1]["from"] is not None


def test_no_chip_week_returns_no_plays_rather_than_a_missing_answer():
    certain = spread(a=[1.0, 0.0], b=[0.0, 1.0])
    after, plays = chip_plan(["b", "a"], certain, FIRST_CHIP_ROUND - 1)
    assert plays == []
    assert after == ["b", "a"], "an order was changed in a week with no chip"


# --- the file -----------------------------------------------------------------


def test_the_real_file_loads():
    filed = load_final_entry(ROOT / "data" / "tabela-final.yaml")
    assert filed["locked_round"] == FIRST_CHIP_ROUND - 1


def test_a_missing_file_means_not_yet_entered_not_an_error(tmp_path):
    assert load_final_entry(tmp_path / "nope.yaml") == {
        "entry": None,
        "locked_round": None,
        "chips": [],
    }


def test_a_repeated_club_is_refused(tmp_path):
    path = tmp_path / "e.yaml"
    path.write_text("entrada:\n  - a\n  - a\nchips: []\n", encoding="utf-8")
    with pytest.raises(SquadSourceError, match="repeats"):
        load_final_entry(path)


def test_a_chip_naming_a_club_outside_the_entry_is_refused(tmp_path):
    """Caught here rather than silently doing nothing where it is applied."""
    path = tmp_path / "e.yaml"
    path.write_text(
        "entrada:\n  - a\n  - b\nchips:\n  - jornada: 6\n    clube: z\n    para: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(SquadSourceError, match="not in"):
        load_final_entry(path)


def test_a_chip_sending_a_club_off_the_table_is_refused(tmp_path):
    path = tmp_path / "e.yaml"
    path.write_text(
        "entrada:\n  - a\n  - b\nchips:\n  - jornada: 6\n    clube: a\n    para: 9\n",
        encoding="utf-8",
    )
    with pytest.raises(SquadSourceError, match="place 9"):
        load_final_entry(path)


# --- one policy, two callers --------------------------------------------------


def test_the_backtest_runs_the_production_policy():
    """A backtest with its own copy of the loop measures a policy nobody runs."""
    source = (ROOT / "scripts" / "backtest_final_table.py").read_text(encoding="utf-8")
    assert "chip_plan(" in source
    assert "best_chip(" not in source, (
        "the backtest plays chips its own way again — the number it reports "
        "stops belonging to what production does"
    )


def test_the_page_names_a_chip():
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "chip_plan(" in source, "nothing on the page decides a chip"
    assert "def chip_advice(" in source


def test_the_page_replays_the_entry_instead_of_recomputing_it():
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "apply_chips(" in source
    assert "load_final_entry(" in source


# --- what the page prints -----------------------------------------------------
#
# None of this can be exercised for real until the entry is submitted in
# September, so it is exercised here instead. A section that renders for the
# first time on the day it matters has never been seen to work.


@pytest.fixture(scope="module")
def page():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_before_the_lock_it_says_the_entry_is_not_in(page):
    said = page.chip_advice({"filed": False, "locked_round": 5, "chips": []})
    assert "ainda não está entregue" in said
    assert "tabela-final.yaml" in said


def test_a_week_with_a_chip_names_the_club_and_both_places(page):
    said = page.chip_advice(
        {
            "filed": True,
            "chips": [
                {
                    "reach": WEEKLY_REACH,
                    "bonus": False,
                    "club": "Gil Vicente",
                    "from": 12,
                    "to": 9,
                    "places": 3,
                    "gain": 8.4,
                }
            ],
        }
    )
    assert "Gil Vicente" in said
    assert "12" in said and "9" in said
    assert "sobe" in said


def test_a_club_moved_down_is_not_described_as_going_up(page):
    said = page.chip_advice(
        {
            "filed": True,
            "chips": [
                {
                    "reach": WEEKLY_REACH,
                    "bonus": False,
                    "club": "Sporting",
                    "from": 2,
                    "to": 5,
                    "places": 3,
                    "gain": 4.0,
                }
            ],
        }
    )
    assert "desce" in said and "sobe" not in said


def test_a_week_worth_nothing_says_so_rather_than_going_quiet(page):
    """A chip is lost if unused, so spending nothing has to be a visible call."""
    said = page.chip_advice(
        {
            "filed": True,
            "chips": [{"reach": WEEKLY_REACH, "bonus": False, "club": None,
                       "from": None, "to": None, "places": 0, "gain": 0.0}],
        }
    )
    assert "não jogues" in said.lower()
    assert said.strip(), "the section rendered empty on a no-chip week"


def test_a_bonus_week_is_labelled_as_one(page):
    said = page.chip_advice(
        {
            "filed": True,
            "chips": [
                {"reach": WEEKLY_REACH, "bonus": False, "club": None, "from": None,
                 "to": None, "places": 0, "gain": 0.0},
                {"reach": BONUS_REACH, "bonus": True, "club": "Rio Ave", "from": 10,
                 "to": 5, "places": 5, "gain": 11.2},
            ],
        }
    )
    assert "bónus" in said and "semanal" in said
    assert "Rio Ave" in said


def test_an_entry_that_does_not_match_the_league_is_flagged_not_used(page):
    """A promoted club, or a zerozero spelling — say so instead of silently
    pricing chips against an order that is missing a club."""
    said = page.chip_advice(
        {"filed": True, "mismatch": ["Estrela", "E. Amadora"], "chips": []}
    )
    assert "não bate" in said
    assert "Estrela" in said


def test_after_the_last_chip_round_it_explains_the_silence(page):
    said = page.chip_advice({"filed": True, "chips": []})
    assert "Não há chip" in said


def test_the_entry_lock_and_the_first_chip_are_adjacent():
    """Pinned together so neither can be moved alone.

    The chip window already slid a whole matchday once, when a change to
    `FIRST_SCORING_MATCHDAY` moved a `reaches_at` that was written against it.
    These two are genuinely adjacent — a chip corrects an entry, so the first
    chip is the round after the entry seals — and saying so here means a future
    edit to one fails loudly instead of quietly desynchronising the backtest
    from the game it is supposed to be measuring.
    """
    assert FIRST_CHIP_ROUND == ENTRY_LOCK_MATCHDAY + 1
    assert reaches_at(ENTRY_LOCK_MATCHDAY) == []
    assert reaches_at(ENTRY_LOCK_MATCHDAY + 1) == [WEEKLY_REACH]


def test_the_season_has_twenty_four_chip_weeks():
    """The number the backtest has to play, and used to play as twenty-three."""
    weeks = [m for m in range(1, 36) if reaches_at(m)]
    assert len(weeks) == 24
    assert weeks[0] == 6 and weeks[-1] == 29
