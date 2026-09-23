"""The replay that decides which rule and which evidence the model advises with.

It has already decided one real piece of work — whether to read zerozero again
for every player in the market — and now decides how the chance of playing is
computed. So the parts that could quietly lie are pinned: never a round from the
one being predicted, every rule and arm scoring the same player-rounds, and the
two readings of an absence.
"""

from __future__ import annotations

import importlib.util
import statistics
from pathlib import Path

import pytest

from liga_record_mcp.models import Position

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def replay():
    spec = importlib.util.spec_from_file_location(
        "replay_valuation", ROOT / "scripts" / "replay_valuation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(number, *, used, points, club="Arouca"):
    return {"round": number, "used": used, "points": points, "club": club}


def rows_for(*rows):
    return {r["round"]: r for r in rows}


# --- what counts as a round ---------------------------------------------------


def test_every_round_counts_a_missing_round_as_an_absence(replay):
    season = {
        "x": rows_for(
            row(1, used=True, points=3),
            row(2, used=False, points=-1),
            row(4, used=True, points=5),
        )
    }
    got = replay.season_so_far(season, first=1, upto=6, every_round=True)
    assert got["x"] == {
        "played": 2,
        "points": 8.0,
        "available": 5,
        "rounds": {1: True, 2: False, 3: False, 4: True, 5: False},
    }


def test_rows_only_counts_the_rounds_the_reconstruction_has(replay):
    season = {
        "x": rows_for(
            row(1, used=True, points=3),
            row(2, used=False, points=-1),
            row(4, used=True, points=5),
        )
    }
    got = replay.season_so_far(season, first=1, upto=6, every_round=False)
    assert got["x"] == {
        "played": 2,
        "points": 8.0,
        "available": 3,
        "rounds": {1: True, 2: False, 4: True},
    }


def test_a_minus_one_on_the_bench_is_not_points(replay):
    """Points are what he scored when he played; the -1 is the other half."""
    season = {"x": rows_for(row(1, used=False, points=-1))}
    got = replay.season_so_far(season, first=1, upto=2, every_round=True)
    assert got["x"] == {"played": 0, "points": 0.0, "available": 1, "rounds": {1: False}}


def test_never_a_round_from_the_one_being_predicted(replay):
    season = {"x": rows_for(row(1, used=True, points=3), row(4, used=True, points=50))}
    got = replay.season_so_far(season, first=1, upto=4, every_round=True)
    assert got["x"] == {
        "played": 1,
        "points": 3.0,
        "available": 3,
        "rounds": {1: True, 2: False, 3: False},
    }


def test_the_arm_without_starts_at_the_first_official_round(replay):
    assert dict(replay.ARMS) == {"with": 1, "without": 6}
    season = {"x": rows_for(row(2, used=True, points=9), row(6, used=True, points=2))}
    got = replay.season_so_far(season, first=6, upto=8, every_round=True)
    assert got["x"] == {
        "played": 1,
        "points": 2.0,
        "available": 2,
        "rounds": {6: True, 7: False},
    }


def test_a_player_with_nothing_in_the_window_is_left_out(replay):
    season = {"x": rows_for(row(20, used=True, points=4))}
    assert replay.season_so_far(season, first=6, upto=8, every_round=False) == {}


def test_two_rows_for_one_round_fail_loudly(replay):
    players = {
        "x": {"matches": [row(3, used=True, points=1), row(3, used=True, points=2)]}
    }
    with pytest.raises(ValueError, match="round 3"):
        replay.rows_by_round(players)


# --- whose player he is --------------------------------------------------------


def test_the_club_is_the_one_he_had_before_the_round(replay):
    rows = rows_for(
        row(3, used=True, points=2, club="Arouca"),
        row(20, used=True, points=2, club="FC Porto"),
    )
    assert replay.club_before(rows, 10) == "Arouca"
    assert replay.club_before(rows, 21) == "FC Porto"


def test_before_his_first_row_he_belongs_to_the_club_he_first_played_for(replay):
    rows = rows_for(row(18, used=True, points=2, club="Sporting"))
    assert replay.club_before(rows, 7) == "Sporting"


def test_a_filled_absence_names_no_club_and_the_last_real_row_decides(replay):
    """The reconstruction's filled -1 rows carry no club at all.

    Found by the code review: every synthetic row here had one, so nothing
    would have caught `club_before` reading a club off an absence.
    """
    rows = rows_for(row(3, used=True, points=2, club="Arouca"))
    rows[5] = {"round": 5, "used": False, "points": -1.0, "absent": True}
    assert replay.club_before(rows, 8) == "Arouca"


# --- the rules and the arms -----------------------------------------------------


def test_every_rule_and_arm_scores_the_same_player_rounds(replay):
    season = {
        "a": rows_for(*(row(n, used=True, points=4) for n in range(1, 10))),
        "b": rows_for(
            *(row(n, used=n % 2 == 0, points=2 if n % 2 == 0 else -1) for n in range(1, 10))
        ),
    }
    positions = {"a": Position.MID, "b": Position.MID}
    scored = replay.replay(season, positions, {}, every_round=True, rounds=range(7, 10))
    assert set(scored) == {
        ("season", "with"),
        ("season", "without"),
        ("recent", "with"),
        ("recent", "without"),
    }
    keys = [[(p, n, t) for p, n, t, *_ in rows] for rows in scored.values()]
    assert all(k == keys[0] for k in keys)
    assert len(keys[0]) == 6


def rotation_season():
    """Used in rounds 1-5, dropped in 6, beside a man who plays every week."""
    return {
        "starter": rows_for(*(row(n, used=True, points=4) for n in range(1, 7))),
        "rotates": rows_for(
            *(row(n, used=n <= 5, points=3 if n <= 5 else -1) for n in range(1, 7))
        ),
    }


def test_the_early_rounds_are_what_moves_a_rotation_players_chance(replay):
    """Under the season rule, without the five he looks dropped for good."""
    positions = {"starter": Position.MID, "rotates": Position.MID}
    scored = replay.replay(
        rotation_season(), positions, {}, every_round=True, rounds=range(7, 8)
    )
    playing = {
        key: {p: chance for p, _, _, _, chance, _ in rows} for key, rows in scored.items()
    }
    assert playing[("season", "with")]["rotates"] > playing[("season", "without")]["rotates"]


def test_the_recent_rule_marks_down_the_drop_the_season_rule_averages_away(replay):
    positions = {"starter": Position.MID, "rotates": Position.MID}
    scored = replay.replay(
        rotation_season(), positions, {}, every_round=True, rounds=range(7, 8)
    )
    playing = {
        key: {p: chance for p, _, _, _, chance, _ in rows} for key, rows in scored.items()
    }
    assert playing[("recent", "with")]["rotates"] < playing[("season", "with")]["rotates"]


def test_correlation_is_the_harness_formula(replay):
    pairs = [(1.0, 2.0), (2.0, 2.5), (3.0, 4.5), (-1.0, 0.0), (4.0, 3.0)]
    truth = [a for a, _ in pairs]
    guess = [g for _, g in pairs]
    mean_a, mean_g = statistics.mean(truth), statistics.mean(guess)
    cov = sum((a - mean_a) * (g - mean_g) for a, g in pairs) / len(pairs)
    expected = cov / (statistics.pstdev(truth) * statistics.pstdev(guess))
    assert replay.correlation(pairs) == pytest.approx(expected)


# --- a wider archive, and on whom it lands ------------------------------------


def scored_rows(values):
    """(player, round, truth, expected) rows, the shape the replay scores."""
    return [(who, number, truth, guess, 0.0, False) for who, number, truth, guess in values]


def test_only_keeps_the_players_asked_for(replay):
    rows = scored_rows([("a", 7, 1.0, 1.0), ("b", 7, 2.0, 2.0)])
    assert [r[0] for r in replay.only(rows, {"b"})] == ["b"]


def truthful(who, *, from_round=7, to_round=34, off_by=0.0):
    """A player whose estimate tracks the truth, `off_by` away from it."""
    return [
        (who, n, float(n % 7), float(n % 7) + off_by * ((n % 3) - 1), 0.0, False)
        for n in range(from_round, to_round + 1)
    ]


def test_a_wider_archive_passes_when_it_helps_and_harms_nobody(replay, capsys):
    gained = {"new1", "new2"}
    wider = truthful("new1") + truthful("new2") + truthful("old1") + truthful("old2")
    # The same men, worse, and worst where the archive was missing.
    narrow = (
        truthful("new1", off_by=3.0)
        + truthful("new2", off_by=3.0)
        + truthful("old1", off_by=0.2)
        + truthful("old2", off_by=0.2)
    )
    assert replay.wider_archive(wider, narrow, gained, draws=50) is True
    printed = capsys.readouterr().out
    assert "ganharam arquivo (2)" in printed and "nao ganharam (2)" in printed


def test_a_wider_archive_fails_when_it_hurts_those_who_had_one(replay):
    gained = {"new1"}
    wider = truthful("new1") + truthful("old1", off_by=4.0) + truthful("old2", off_by=4.0)
    narrow = truthful("new1", off_by=1.0) + truthful("old1") + truthful("old2")
    assert replay.wider_archive(wider, narrow, gained, draws=50) is False


def test_a_wider_archive_fails_when_nothing_moves(replay):
    gained = {"new1"}
    same = truthful("new1") + truthful("old1")
    assert replay.wider_archive(same, list(same), gained, draws=50) is False


def test_the_harm_guard_reads_the_pessimistic_end_of_the_interval(replay):
    """Ten men gain an archive and clear the gain bar on their own, while one
    of the ten who gained nothing is hurt: the optimistic end of that group's
    interval sits inside the bar and only the pessimistic end says so. Read
    the wrong end — as this did until the review of 23/09/2026 — and a wider
    archive passes while hurting the players it was meant to leave alone."""
    gained = {f"new{i}" for i in range(1, 11)}
    rest = {f"calm{i}" for i in range(1, 10)} | {"hurt"}
    wider: list = []
    narrow: list = []
    for who in sorted(gained):
        wider += truthful(who)
        narrow += truthful(who, off_by=1.5)
    for who in sorted(rest - {"hurt"}):
        wider += truthful(who)
        narrow += truthful(who)
    wider += truthful("hurt", off_by=1.0)
    narrow += truthful("hurt")

    gain = replay.summary(wider, 7, 34)["r"] - replay.summary(narrow, 7, 34)["r"]
    spread = replay.gain_interval(wider, narrow, 7, 34, draws=200)
    assert gain >= replay.BAR and spread[0] > 0, "the gain bar must be met, or this is vacuous"
    low, high = replay.gain_interval(
        replay.only(wider, rest), replay.only(narrow, rest), 7, 34, draws=200
    )
    assert low < -replay.BAR <= high, "only the pessimistic end may show the harm"

    assert replay.wider_archive(wider, narrow, gained, draws=200) is False
