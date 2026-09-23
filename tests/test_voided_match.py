"""A match §15.3 has already struck out, seen before the round rather than after.

§15.3 scores a match at nothing for both clubs when it is not played before the
next round begins. The history has read it that way since 15/09/2026; the
projection did not, and round 3's Moreirense-Benfica — played on 9 September,
after round 4 had started — went onto the page with Pavlidis at 9.19 for a
match that could pay him nothing.

The calendar says it in advance, and the risk is reading its silence as an
answer: the far end of a season carries dates with no times at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.advice import round_projection, round_weeks
from liga_record_mcp.models import Fixture, Position, kickoff_at
from liga_record_mcp.stats import voided_in

ROOT = Path(__file__).resolve().parents[1]

#: Near today, so the year the label does not carry is not in doubt.
SOON = datetime.now() + timedelta(days=20)


def label(moment: datetime) -> str:
    months = {
        1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
        7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ",
    }
    return f"{moment.day:02d} {months[moment.month]} {moment:%H:%M}"


def fixture(round_number, home, away, moment=None):
    return Fixture(
        round_number=round_number,
        home=home,
        away=away,
        kickoff=None if moment is None else label(moment),
    )


def calendar(*, late=None, unscheduled=False):
    """Round 8 on time, round 9 a fortnight later, and one match under test."""
    eight = [fixture(8, "Benfica", "Arouca", SOON), fixture(8, "Sporting", "Estoril", SOON)]
    if late is not None:
        eight.append(fixture(8, "Moreirense", "Casa Pia", late))
    elif unscheduled:
        eight.append(fixture(8, "Moreirense", "Casa Pia", None))
    return eight + [
        fixture(9, "Arouca", "Benfica", SOON + timedelta(days=14)),
        fixture(9, "Estoril", "Sporting", SOON + timedelta(days=15)),
    ]


# --- the rule ------------------------------------------------------------------


def test_a_match_after_the_next_round_begins_is_voided():
    struck = voided_in(calendar(late=SOON + timedelta(days=20)), 8)
    assert struck == {"Moreirense", "Casa Pia"}


def test_a_match_before_it_is_not():
    struck = voided_in(calendar(late=SOON + timedelta(days=3)), 8)
    assert struck == set()


def test_a_match_with_no_date_in_a_scheduled_round_is_voided():
    """Sp. Braga against Gil Vicente, round 2, postponed on 16 August to a date
    nobody has set. Nobody plays a match that has not been arranged."""
    assert voided_in(calendar(unscheduled=True), 8) == {"Moreirense", "Casa Pia"}


def test_a_round_nobody_has_scheduled_voids_nothing():
    """May is published as dates without times. Read as 'after the next round',
    it would put half the run-in at zero."""
    far = [fixture(30, "Benfica", "Arouca"), fixture(30, "Sporting", "Estoril")]
    far += [fixture(31, "Arouca", "Benfica"), fixture(31, "Estoril", "Sporting")]
    assert voided_in(far, 30) == set()


def test_a_next_round_nobody_has_scheduled_voids_nothing():
    fixtures = [fixture(8, "Benfica", "Arouca", SOON), fixture(9, "Arouca", "Benfica")]
    assert voided_in(fixtures, 8) == set()


def test_the_last_round_of_all_voids_nothing():
    """There is no round after it to be late for."""
    assert voided_in([fixture(34, "Benfica", "Arouca", SOON)], 34) == set()


# --- what the model does with it -------------------------------------------------


def test_a_voided_club_has_no_week_and_scores_the_zero():
    fixtures = calendar(late=SOON + timedelta(days=20))
    weeks = round_weeks({}, fixtures, 8)
    assert "Moreirense" not in weeks and "Casa Pia" not in weeks
    assert "Benfica" in weeks

    man = SimpleNamespace(id="p", club="Moreirense", position=Position.FWD)
    row = round_projection(
        [man],
        {"p": {"returns": 6.0, "playing": 0.9, "expected": 5.3, "appearances": 40}},
        weeks,
        voided=voided_in(fixtures, 8),
    )["p"]
    assert row["expected"] == 0.0
    assert row["no_fixture"] is True
    # And it says which zero it is, because the calendar has lied before.
    assert row["voided"] is True


def test_a_club_simply_without_a_match_is_not_marked_voided():
    weeks = round_weeks({}, calendar(), 8)
    man = SimpleNamespace(id="p", club="Nacional", position=Position.DEF)
    row = round_projection(
        [man],
        {"p": {"returns": 3.0, "playing": 0.9, "expected": 2.6, "appearances": 40}},
        weeks,
        voided=voided_in(calendar(), 8),
    )["p"]
    assert (row["expected"], row["no_fixture"], row["voided"]) == (0.0, True, False)


# --- one reading of the label ------------------------------------------------------


def test_the_label_is_read_in_one_place():
    """The deadline and the voided match hang off the same reading of a label
    that carries no year, and two copies of that would drift."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pending_decisions", ROOT / "scripts" / "pending_decisions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.when is kickoff_at
    source = (ROOT / "scripts" / "pending_decisions.py").read_text(encoding="utf-8")
    assert "MONTHS" not in source


def test_the_year_is_the_nearest_one_and_beyond_that_it_refuses():
    """The label carries no year, so the nearest one wins, forwards or back —
    and past a horizon of half a year the guess is deciding rather than the
    label. A round this cannot place is a round `voided_in` leaves alone."""
    now = datetime(2026, 9, 23, 12, 0)
    assert kickoff_at("23 SET 20:15", now=now) == datetime(2026, 9, 23, 20, 15)
    # February is five months ahead: next year.
    assert kickoff_at("23 FEV 20:15", now=now) == datetime(2027, 2, 23, 20, 15)
    # May is four months BEHIND and seven ahead, so it reads as the May gone.
    # Harmless here: a moment in the past is never after the next round.
    assert kickoff_at("23 MAI 20:15", now=now) == datetime(2026, 5, 23, 20, 15)
    # March is six months and a day either way, and is refused.
    assert kickoff_at("23 MAR 20:15", now=now) is None
    assert kickoff_at("32 XXX 25:99", now=now) is None
    assert kickoff_at(None) is None


def test_a_round_with_only_a_kickoff_or_two_confirmed_voids_nothing():
    """Broadcasters confirm slots a few at a time. A round with one match
    placed is not a scheduled round, and reading the rest as struck out would
    zero sixteen clubs about to play."""
    piecemeal = [
        fixture(8, "Benfica", "Arouca", SOON),
        fixture(8, "Sporting", "Estoril"),
        fixture(8, "Moreirense", "Casa Pia"),
        fixture(8, "Nacional", "Rio Ave"),
    ]
    piecemeal += [fixture(9, "Arouca", "Benfica", SOON + timedelta(days=14))]
    assert voided_in(piecemeal, 8) == set()

    # One left to place among four is the round being arranged, not silent.
    nearly = piecemeal[:1] + [
        fixture(8, "Sporting", "Estoril", SOON),
        fixture(8, "Moreirense", "Casa Pia", SOON),
        fixture(8, "Nacional", "Rio Ave"),
    ] + piecemeal[4:]
    assert voided_in(nearly, 8) == {"Nacional", "Rio Ave"}


def test_a_club_with_two_matches_in_one_round_keeps_the_one_it_plays():
    """A postponed match rebucketed into the round of its new date puts a club
    in two fixtures. Striking out one of them must not erase the other, nor
    the week of the club it is against."""
    fixtures = [
        fixture(8, "Benfica", "Arouca", SOON),
        fixture(8, "Sporting", "Estoril", SOON),
        # Benfica's catch-up, late enough to be voided.
        fixture(8, "Benfica", "Casa Pia", SOON + timedelta(days=20)),
        fixture(9, "Arouca", "Benfica", SOON + timedelta(days=14)),
        fixture(9, "Estoril", "Sporting", SOON + timedelta(days=15)),
    ]
    weeks = round_weeks({}, fixtures, 8)
    # Arouca still has its match against Benfica, and so does Benfica.
    assert weeks["Arouca"]["opponent"] == "Benfica"
    assert weeks["Benfica"]["opponent"] == "Arouca"
    # Casa Pia, whose only match is the voided one, has no week.
    assert "Casa Pia" not in weeks
