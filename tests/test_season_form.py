"""Reading a reconstructed season: what a player was worth, and when.

A season total is the least interesting thing a season can tell you. Two
forwards on 105 points are the same player to a total and different bets
entirely — one returned six a round all year, the other returned twelve until
January and one after it. Liga Record allows a single transfer a round from
round five, so knowing which is which is the whole game.
"""

from __future__ import annotations

from liga_record_mcp.stats import (
    FORM_WINDOW,
    fill_absent_rounds,
    form_curve,
    season_summary,
)


def match(round_number: int, points: float, **extra):
    base = dict(round=round_number, points=points, used=True, minutes=90, goals=0)
    return {**base, **extra}


# --------------------------------------------------------------------------
# What a season came to
# --------------------------------------------------------------------------


def test_a_score_is_divided_by_rounds_in_the_squad_not_by_appearances():
    """A manager pays for a player every round, started or not. Dividing by
    appearances turns a forward who plays a third of the time into a star, and
    that is exactly the mistake the minutes data was gathered to stop."""
    sporadic = [match(1, 9), match(2, -1, used=False, minutes=0), match(3, -1, used=False, minutes=0)]
    summary = season_summary(sporadic)
    assert summary["per_match"] == round(7 / 3, 2)
    assert summary["played"] == 1
    assert summary["matches"] == 3


def test_how_good_he_is_when_he_plays_is_a_different_question_and_kept_apart():
    """Both numbers are real and they answer different things: one is what he
    returned, the other is whether he is worth starting once fit."""
    sporadic = [match(1, 9), match(2, -1, used=False, minutes=0)]
    summary = season_summary(sporadic)
    assert summary["per_match"] == 4.0
    assert summary["per_90"] == 9.0


def test_two_players_on_the_same_average_are_told_apart():
    """The captaincy doubles a score (§10.3(l)), so it is a bet on the spread
    and not on the mean."""
    steady = [match(n, 4) for n in range(1, 11)]
    spiky = [match(n, 12 if n % 5 == 0 else 2) for n in range(1, 11)]
    assert season_summary(steady)["per_match"] == 4.0
    assert season_summary(spiky)["per_match"] == 4.0
    assert season_summary(steady)["consistency"] < season_summary(spiky)["consistency"]
    assert season_summary(spiky)["hauls"] == 2
    assert season_summary(steady)["hauls"] == 0


def test_a_season_that_never_happened_reports_nothing_rather_than_zero():
    """Promoted with Marítimo or Académico: no top-flight season at all. A rate
    of 0.0 would read as a player who was terrible, which is a different and
    much more actionable claim than having no record."""
    empty = season_summary([])
    assert empty["matches"] == 0
    assert empty["per_match"] is None
    assert empty["per_90"] is None
    assert empty["best"] is None


def test_a_player_who_never_left_the_bench_has_no_rate_per_90():
    """Zero minutes is not a small number of minutes."""
    benched = [match(n, -1, used=False, minutes=0) for n in range(1, 6)]
    summary = season_summary(benched)
    assert summary["per_90"] is None
    assert summary["per_match"] == -1.0
    assert summary["blanks"] == 5


# --------------------------------------------------------------------------
# When he was worth it
# --------------------------------------------------------------------------


def test_the_curve_follows_the_season_and_not_the_order_it_arrived_in():
    """zerozero lists a season newest first. Taken as given, the window would
    run backwards through the year and never once look wrong."""
    season = [match(n, float(n)) for n in range(1, 11)]
    forwards = form_curve(season)
    backwards = form_curve(list(reversed(season)))
    assert forwards == backwards
    assert [row["round"] for row in forwards] == list(range(1, 11))


def test_the_window_is_short_early_and_says_how_short():
    """Round three cannot have five rounds behind it. Padding it with zeros
    would invent a slow start for every player in the league."""
    curve = form_curve([match(n, 6) for n in range(1, 9)])
    assert curve[0]["window"] == 1
    assert curve[0]["form"] == 6.0
    assert curve[FORM_WINDOW - 1]["window"] == FORM_WINDOW
    assert curve[-1]["window"] == FORM_WINDOW


def test_the_curve_shows_a_collapse_a_total_would_hide():
    """The reason for reconstructing match by match at all."""
    season = [match(n, 12) for n in range(1, 18)] + [match(n, 0) for n in range(18, 35)]
    curve = {row["round"]: row["form"] for row in form_curve(season)}
    assert curve[17] == 12.0
    assert curve[34] == 0.0
    assert season_summary(season)["per_match"] == 6.0


def test_each_round_keeps_its_own_score_beside_the_average():
    """A form line that dropped the actual scores could not be checked against
    anything."""
    curve = form_curve([match(1, 2), match(2, 8)])
    assert [row["points"] for row in curve] == [2.0, 8.0]
    assert curve[1]["form"] == 5.0


# --------------------------------------------------------------------------
# The weeks the archive has no row for
# --------------------------------------------------------------------------


def test_the_weeks_he_was_injured_cost_a_manager_a_point_each():
    """A match archive has no row for a week nothing happened to a player.
    Liga Record has a row for every week, and §10.3(i) pays -1."""
    filled = fill_absent_rounds([match(1, 6), match(4, 2)])
    assert [row["round"] for row in filled] == [1, 2, 3, 4]
    assert [row["points"] for row in filled] == [6, -1.0, -1.0, 2]
    assert [row["used"] for row in filled] == [True, False, False, True]


def test_a_filled_week_is_marked_so_it_is_never_mistaken_for_evidence():
    """An unused substitute was at the ground and was watched. A man who was
    not in the squad was not. Both score -1 and they are not the same fact."""
    filled = fill_absent_rounds([match(1, 6), match(3, 2)])
    invented = [row for row in filled if row.get("absent")]
    assert len(invented) == 1
    assert invented[0]["rating"] is None
    assert invented[0]["mark_source"] == "not in the squad"


def test_nothing_is_invented_before_he_arrived_or_after_he_left():
    """A January signing could not have been owned in August. Filling from
    round one would charge a manager for a player who was in another country."""
    filled = fill_absent_rounds([match(18, 5), match(20, 5)])
    assert [row["round"] for row in filled] == [18, 19, 20]


def test_a_season_he_never_missed_is_left_exactly_as_it_was():
    whole = [match(n, 4) for n in range(1, 35)]
    assert fill_absent_rounds(whole) == whole


def test_a_season_that_never_happened_stays_empty():
    assert fill_absent_rounds([]) == []


def test_filling_changes_what_a_season_was_worth_and_is_meant_to():
    """The reason it exists. Six good afternoons in a thirty-four-round season
    is not a six-point-a-round player, and the archive alone says he was."""
    cameo = [match(n, 6) for n in (1, 8, 15, 22, 29, 34)]
    assert season_summary(cameo)["per_match"] == 6.0
    assert season_summary(fill_absent_rounds(cameo))["per_match"] < 0.5
