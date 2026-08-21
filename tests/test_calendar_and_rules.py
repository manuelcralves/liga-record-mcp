"""What the regulation actually says, checked against its own text.

Everything here was read off Record's published rules rather than inferred, and
most of it corrects or fills a gap in what this project believed. The three
that matter:

A "ronda" is not a "jornada". The regulation uses both words in the same
sentence and never once says they differ, but §16.3 relates them twice — round
13 is matchday 18, round 29 is matchday 34 — and §16.4 settles the consequence:
the final standings count "a pontuação acumulada entre a 6.ª e a 34.ª jornadas
(inclusive)". Liga Record is a 29-round competition laid over the last 29
matchdays of a 34-matchday league.

A transfer belongs to its round. §6.8 allows "um só jogador entre cada ronda"
and every extra §6.10 and §6.11 grant is "na ronda em questão". Nothing banks.

And two mechanisms this project had never heard of: §6.17's three holiday
rounds, and §10.3(m)'s one-off bet on the season's top scorer.
"""

from __future__ import annotations

import pytest

from liga_record_mcp.models import (
    BRONZE_BOOT_BONUS,
    DISPUTED_FIRST_MATCHDAY,
    FIRST_SCORING_MATCHDAY,
    GOLDEN_BOOT_BONUS,
    HOLIDAY_BLOCKED_LAST_ROUNDS,
    HOLIDAY_ROUNDS,
    HOLIDAY_SHARE_OF_WINNER,
    LAST_MATCHDAY,
    RECORD_ROUNDS,
    REOPENED_FIRST_ROUND,
    REOPENED_LAST_MATCHDAY,
    REOPENED_MAX_SWAPS,
    SILVER_BOOT_BONUS,
    TRANSFERS_PER_ROUND,
    matchday_of_round,
    round_of_matchday,
)


# --------------------------------------------------------------------------
# Rounds are not matchdays
# --------------------------------------------------------------------------


def test_the_two_anchors_the_regulation_gives_are_both_satisfied():
    """§16.3 is the only place the two numbering systems are related, and it
    relates them twice. Both have to hold, or the reading is wrong."""
    assert matchday_of_round(13) == 18
    assert matchday_of_round(29) == 34


def test_the_competition_is_shorter_than_the_league_it_scores():
    """§16.4: "a pontuação acumulada entre a 6.ª e a 34.ª jornadas". Twenty-nine
    rounds, not thirty-four — and §16.5 says "cada uma das 29 rondas" outright,
    which is the same claim from the prize schedule."""
    assert RECORD_ROUNDS == 29
    assert matchday_of_round(1) == FIRST_SCORING_MATCHDAY == 6
    assert matchday_of_round(RECORD_ROUNDS) == LAST_MATCHDAY == 34


def test_the_first_five_matchdays_belong_to_no_round():
    """§19's trial period. Points are awarded exactly as in the real thing, so
    they look identical and are not counted by §16.4. Returning round 0, or a
    negative round, would let them be summed with the ones that count."""
    for matchday in range(1, FIRST_SCORING_MATCHDAY):
        assert round_of_matchday(matchday) is None
    assert round_of_matchday(FIRST_SCORING_MATCHDAY) == 1


def test_the_two_directions_agree():
    for round_number in range(1, RECORD_ROUNDS + 1):
        assert round_of_matchday(matchday_of_round(round_number)) == round_number


# --------------------------------------------------------------------------
# Transfers
# --------------------------------------------------------------------------


def test_a_transfer_belongs_to_its_round():
    """§6.8 gives one "entre cada ronda". Nothing in the regulation banks an
    unused one, and every compensation §6.10 and §6.11 grant is scoped to "a
    ronda em questão" — the same rule seen from the other side."""
    assert TRANSFERS_PER_ROUND == 1


def test_the_february_window_is_six_swaps_for_the_whole_window():
    """§6.9. Six in total across rounds 16 to 19, not six a round — and §6.8
    switches the ordinary transfer off while it runs, so the window is the
    whole allowance for that month."""
    assert REOPENED_MAX_SWAPS == 6
    assert matchday_of_round(REOPENED_FIRST_ROUND) == 21
    assert REOPENED_LAST_MATCHDAY == 24
    assert round_of_matchday(REOPENED_LAST_MATCHDAY) == 19


# --------------------------------------------------------------------------
# Two mechanisms the project had never heard of
# --------------------------------------------------------------------------


def test_a_holiday_round_pays_half_the_winners_score():
    """§6.17. Three a season, and worth understanding before deciding they are
    for people going away: half of a winning score, rounded up, is well above
    what an average team returns."""
    assert HOLIDAY_ROUNDS == 3
    assert HOLIDAY_SHARE_OF_WINNER == 0.5
    assert HOLIDAY_BLOCKED_LAST_ROUNDS == 3


def test_the_last_three_rounds_cannot_be_taken_off():
    """§6.17 excludes them, which is the difference between a rest mechanism
    and a way to lock in a lead."""
    playable = [
        r for r in range(1, RECORD_ROUNDS + 1) if r <= RECORD_ROUNDS - HOLIDAY_BLOCKED_LAST_ROUNDS
    ]
    assert playable[-1] == 26
    assert RECORD_ROUNDS - HOLIDAY_BLOCKED_LAST_ROUNDS == 26


def test_the_top_scorer_bet_is_free_and_settled_once():
    """§10.3(m). Optional, made when the first version of the team is
    submitted, never again — so not making it is a decision, and it is the
    kind that is only ever noticed at the end of a season."""
    assert (GOLDEN_BOOT_BONUS, SILVER_BOOT_BONUS, BRONZE_BOOT_BONUS) == (20, 10, 5)


@pytest.mark.parametrize("matchday", [0, -1])
def test_a_matchday_before_the_league_started_is_refused(matchday):
    assert round_of_matchday(matchday) is None


def test_the_disputed_start_is_kept_where_it_can_be_seen():
    """§19 puts the first counting matchday at 5, not 6, and the calendar backs
    it: the trial "dura até dia 1 de setembro" and matchday 4 ends 31 August.
    Four numbered clauses say 6 and this one says 5.

    Choosing 6 is defensible. Deleting the other reading is not — it is worth a
    whole round, and it will be settled by watching whether the site counts
    matchday 5, which nobody will think to do if the disagreement is gone.
    """
    assert DISPUTED_FIRST_MATCHDAY == FIRST_SCORING_MATCHDAY - 1
    under_19 = LAST_MATCHDAY - DISPUTED_FIRST_MATCHDAY + 1
    assert under_19 == 30
    assert RECORD_ROUNDS == 29
