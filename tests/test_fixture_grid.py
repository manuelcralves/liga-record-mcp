"""The opponent grid, and the day `season_rate` changed meaning under it.

The ledger used to store a RATE in `season_rate` — which is what
`adjust_for_fixture`'s first parameter is called — and now stores the blend,
`playing * returns + (1 - playing) * -1`. The grid kept scaling it, so the
fixture was charged against the -1 a man collects for NOT playing, which no
opponent can move.

These tests are about the shape of that arithmetic rather than about any
number: a cell has to move with the opponent, and it has to move in the right
direction.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.models import Position
from liga_record_mcp.stats import UNUSED_PENALTY, adjust_for_fixture

ROOT = Path(__file__).resolve().parents[1]


def blend(playing: float, returns: float, defensive: float, attacking: float) -> float:
    """What the grid should compute, written out independently of the script."""
    return playing * adjust_for_fixture(
        returns, Position.MID, defensive, attacking
    ) + (1 - playing) * float(UNUSED_PENALTY)


EASY, NEUTRAL, HARD = 1.45, 1.0, 0.6


def test_a_fringe_player_still_moves_with_the_opponent():
    """The bug: every cell printed the same number, whoever the opponent was.

    A blend of -0.30 sits below APPEARANCE_FLOOR, so scaling it ran
    max(0.0, -0.30 - 1.0) and clamped to zero — leaving the floor, 1.0, in
    every cell of every week.
    """
    cells = [blend(0.10, 6.0, m, m) for m in (EASY, NEUTRAL, HARD)]
    assert len(set(round(c, 3) for c in cells)) == 3, (
        "a fringe player's cells are identical across easy, neutral and hard"
    )
    assert cells[0] > cells[1] > cells[2]


def test_a_fringe_player_is_not_painted_green():
    """edge = projected - the unadjusted blend. It used to read +1.30."""
    unadjusted = 0.10 * 6.0 + 0.90 * float(UNUSED_PENALTY)
    edge = blend(0.10, 6.0, NEUTRAL, NEUTRAL) - unadjusted
    assert abs(edge) < 0.25, f"a neutral fixture reads as an edge of {edge:+.2f}"


def test_a_man_who_never_plays_is_not_helped_by_an_easy_week():
    """§10.3(i) pays the same -1 whoever the opponent is."""
    for multiplier in (EASY, NEUTRAL, HARD):
        assert blend(0.0, 9.0, multiplier, multiplier) == pytest.approx(
            float(UNUSED_PENALTY)
        )


def test_a_certain_starter_moves_by_the_full_multiplier():
    at_easy = blend(1.0, 6.0, EASY, EASY)
    assert at_easy == pytest.approx(adjust_for_fixture(6.0, Position.MID, EASY, EASY))


def test_the_grid_reads_the_halves_when_the_round_has_them():
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    grid = source[source.index("def fixture_grid("):source.index("def hero(")]
    assert 'row.get("returns")' in grid and 'row.get("playing")' in grid, (
        "fixture_grid is scaling season_rate again — that field is the blend "
        "from round 4 onward, and the fixture must never touch the blend"
    )


def test_the_grid_still_handles_a_round_recorded_by_the_old_estimator():
    """Rounds 1-3 hold a rate and have no halves; they must not KeyError."""
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    grid = source[source.index("def fixture_grid("):source.index("def hero(")]
    assert "if returns is None or playing is None:" in grid
    assert 'row["season_rate"], position, defensive, attacking' in grid


# --- the grid and the team sheet read the same week ---------------------------


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_match_struck_out_by_15_3_is_drawn_as_no_week(dash, monkeypatch):
    """The contradiction this closed on 24/09/2026.

    The grid used to compute its own league means and multipliers, and its copy
    predated §15.3: `advice.round_weeks` leaves a struck-out match out, the
    copy did not, so the same page could paint a normal week for a match that
    pays nobody beside a projection of zero for the same player.

    Here round 9 has a week and round 10 does not — `fixture_weeks` is stubbed
    to answer exactly as `round_weeks` does when §15.3 has struck the match
    out, which is by leaving the club out of the round.
    """
    played = [
        (9, "Arouca", True),
        (10, "Sporting", False),
    ]
    monkeypatch.setattr(dash.mcp._market, "fixtures", lambda: [], raising=False)
    monkeypatch.setattr(dash, "upcoming_opponents", lambda *a, **k: played)
    monkeypatch.setattr(
        dash,
        "fixture_weeks",
        lambda rounds: {
            9: {"Benfica": {"opponent": "Arouca", "at_home": True,
                            "defensive": 1.2, "attacking": 1.2}},
            10: {},  # struck out: no week, and no opponent to be hard
        },
    )
    stored = {
        "players": {
            "1": {
                "name": "X", "club": "Benfica", "position": "MID",
                "season_rate": 3.0, "returns": 4.0, "playing": 0.9,
            }
        }
    }

    cells = {c["round"]: c for c in dash.fixture_grid(stored, 9)[0]["cells"]}
    assert set(cells) == {9, 10}, "the struck-out round is shown, not skipped"
    assert not cells[9].get("voided")
    assert cells[9]["projected"] == round(blend(0.9, 4.0, 1.2, 1.2), 1)

    void = cells[10]
    assert void["voided"] is True
    assert void["projected"] == 0.0, "nobody scores in a match that is not played"
    assert void["edge"] == 0.0, "and no colour: it is not a hard week, it is no week"


def test_the_grid_takes_its_multipliers_from_fixture_weeks(dash, monkeypatch):
    """Not from a copy of its own. If it ever computes them again, the league
    means can drift from the ones the team sheet used and the two disagree."""
    asked = []
    monkeypatch.setattr(dash.mcp._market, "fixtures", lambda: [], raising=False)
    monkeypatch.setattr(dash, "upcoming_opponents", lambda *a, **k: [(9, "Arouca", True)])

    def weeks(rounds):
        asked.append(list(rounds))
        return {9: {"Benfica": {"opponent": "Arouca", "at_home": True,
                                "defensive": 2.0, "attacking": 2.0}}}

    monkeypatch.setattr(dash, "fixture_weeks", weeks)
    stored = {
        "players": {
            "1": {"name": "X", "club": "Benfica", "position": "MID",
                  "season_rate": 3.0, "returns": 4.0, "playing": 1.0}
        }
    }

    grid = dash.fixture_grid(stored, 9)
    assert asked == [[9]], "one call, for the rounds actually drawn"
    assert grid[0]["cells"][0]["projected"] == round(
        blend(1.0, 4.0, 2.0, 2.0), 1
    ), "the cell has to be the multiplier it was handed, not one of its own"


def test_a_club_with_two_matches_in_one_round_gets_the_right_one_struck(dash, monkeypatch):
    """The case the first fix walked straight past.

    A postponed match lands in the round of its new date, so a club can hold
    two in one round; §15.3 strikes one of them and `round_weeks` keys its
    answer by CLUB, so it hands back the survivor. Looked up by club alone, the
    struck cell borrowed the survivor's multipliers and printed a number for
    the one game that pays nobody — the very statement this job set out to stop
    the page making.
    """
    monkeypatch.setattr(dash.mcp._market, "fixtures", lambda: [], raising=False)
    monkeypatch.setattr(
        dash,
        "upcoming_opponents",
        lambda *a, **k: [(8, "Arouca", True), (8, "Casa Pia", True)],
    )
    monkeypatch.setattr(
        dash,
        "fixture_weeks",
        # What `round_weeks` really answers here: the match that survived, and
        # no mention at all of the one that did not.
        lambda rounds: {
            8: {"Benfica": {"opponent": "Arouca", "at_home": True,
                            "defensive": 1.1, "attacking": 1.1}}
        },
    )
    stored = {
        "players": {
            "1": {"name": "X", "club": "Benfica", "position": "MID",
                  "season_rate": 3.0, "returns": 4.0, "playing": 1.0}
        }
    }

    cells = {c["opponent"]: c for c in dash.fixture_grid(stored, 8)[0]["cells"]}
    assert not cells["Arouca"].get("voided")
    assert cells["Arouca"]["projected"] == round(blend(1.0, 4.0, 1.1, 1.1), 1)
    assert cells["Casa Pia"]["voided"] is True, (
        "the struck match took the survivor's multipliers"
    )
    assert cells["Casa Pia"]["projected"] == 0.0

