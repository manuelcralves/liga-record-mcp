"""Deadlines, and the two ways a deadline can be worse than none at all.

It can point at the wrong round, and it can invent a date. Both produce a line
that reads like advice, which is why they are tested rather than eyeballed: a
wrong deadline is acted on, and a missing one is only noticed afterwards.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from liga_record_mcp.models import Fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "pending_decisions", ROOT / "scripts" / "pending_decisions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def at(moment: datetime) -> str:
    """A kickoff label in the site's own shape."""
    months = "JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ".split()
    return f"{moment.day:02d} {months[moment.month - 1]} {moment:%H:%M}"


def fixture(round_number, home, away, *, kickoff=None, played=False):
    goals = {"home_goals": 1, "away_goals": 0} if played else {}
    return Fixture(
        round_number=round_number, home=home, away=away, kickoff=kickoff, **goals
    )


# --- reading a label the site never dated -------------------------------------


def test_a_label_is_read_into_the_nearest_year(mod):
    soon = datetime.now() + timedelta(days=3)
    read = mod.when(at(soon))
    assert read is not None
    assert (read - soon.replace(second=0, microsecond=0)).days == 0


def test_a_far_off_label_is_read_as_the_past_and_then_ignored(mod):
    """The honest account of a day-and-month label with no year.

    "12 MAR" read in August is genuinely ambiguous, and nearest-year resolves it
    to last March. That is the right guess far more often than not — a fixture
    list is read while the season is running — but it means a date two hundred
    days out comes back as one in the past.

    Which is exactly why `deadlines` drops anything already gone rather than
    trusting the year: the cost is no warning about something six months away,
    which nobody wants, instead of a confident wrong date, which is acted on.
    """
    far = datetime.now() + timedelta(days=200)
    read = mod.when(at(far))
    assert read is None or read < datetime.now()

    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(far)),
        ]
    )
    assert not [line for line in said if "§6.13" in line], (
        "a label that could not be dated confidently became a deadline anyway"
    )


def test_nonsense_is_refused_rather_than_guessed(mod):
    assert mod.when(None) is None
    assert mod.when("") is None
    assert mod.when("brevemente") is None
    assert mod.when("32 XXX 25:99") is None


def test_how_far_off_is_said_in_human_units(mod):
    now = datetime.now()
    assert "dias" in mod.in_words(now + timedelta(days=4))
    assert "horas" in mod.in_words(now + timedelta(hours=5))
    assert "minutos" in mod.in_words(now + timedelta(minutes=30))
    assert mod.in_words(now - timedelta(days=1)) == "já passou"


# --- which round's sheet is actually open -------------------------------------


def test_a_round_with_a_result_in_it_is_closed(mod):
    """The bug this exists for.

    Round 2 has one postponed fixture left, so "the lowest round with an
    unplayed match" points at it — and its sheet shut days ago. A round is
    closed the moment its first match starts, however many are still to come.
    """
    soon = datetime.now() + timedelta(days=4)
    said = mod.deadlines(
        [
            fixture(2, "Sp. Braga", "Gil Vicente"),          # postponed, no date
            fixture(2, "Sporting", "Alverca", played=True),  # so round 2 is closed
            fixture(3, "FC Porto", "Arouca", played=True),
            fixture(4, "Rio Ave", "Sporting", kickoff=at(soon)),
        ]
    )
    sheet = [line for line in said if "§6.13" in line]
    assert len(sheet) == 1
    assert "jornada 4" in sheet[0]
    assert "já passou" not in sheet[0]


def test_the_sheet_closes_before_the_first_match_not_the_earliest_unplayed(mod):
    """§6.13 — fifteen minutes before the round's first match."""
    first = datetime.now() + timedelta(days=4)
    later = first + timedelta(days=1)
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(later)),
            fixture(4, "e", "f", kickoff=at(first)),
        ]
    )
    sheet = next(line for line in said if "§6.13" in line)
    shuts = first - mod.SHEET_CLOSES_BEFORE
    assert f"{shuts:%d/%m às %H:%M}" in sheet


def test_only_the_next_round_is_named(mod):
    """Six deadlines at once is a list nobody reads."""
    now = datetime.now()
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(now + timedelta(days=4))),
            fixture(5, "e", "f", kickoff=at(now + timedelta(days=11))),
            fixture(6, "g", "h", kickoff=at(now + timedelta(days=18))),
        ]
    )
    assert len([line for line in said if "§6.13" in line]) == 1


# --- the one that does not come round again -----------------------------------


def test_the_squad_lock_is_announced_while_it_is_still_ahead(mod):
    now = datetime.now()
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(now + timedelta(days=4))),
            fixture(5, "e", "f", kickoff=at(now + timedelta(days=11))),
        ]
    )
    lock = [line for line in said if "FECHA TUDO" in line]
    assert len(lock) == 1
    assert "ilimitadas" in lock[0]


def test_an_undated_lock_says_so_rather_than_going_quiet(mod):
    """Matchday 5 has no dates published yet, and that is worth saying."""
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(datetime.now() + timedelta(days=4))),
            fixture(5, "e", "f", kickoff=None),
        ]
    )
    lock = next(line for line in said if "FECHA TUDO" in line)
    assert "sem data" in lock


def test_the_lock_is_not_announced_once_it_has_passed(mod):
    said = mod.deadlines(
        [
            fixture(5, "a", "b", played=True),
            fixture(6, "c", "d", kickoff=at(datetime.now() + timedelta(days=4))),
        ]
    )
    assert not [line for line in said if "FECHA TUDO" in line]


def test_the_sheet_deadline_is_the_last_line(mod):
    """routine.py logs the final line of a step, so the order is the message.

    On a day Manuel does not open the page, that one line is everything he
    sees. Everything else pending_decisions prints describes work that can be
    done whenever; the sheet closing is the only thing with a clock on it, so
    it goes last and survives the truncation.
    """
    now = datetime.now()
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(now + timedelta(days=4))),
            fixture(5, "e", "f", kickoff=at(now + timedelta(days=11))),
        ]
    )
    assert len(said) == 2
    assert "§6.13" in said[-1], "the line with a clock on it is not the one logged"
    assert "FECHA TUDO" in said[0]


# --- the round order is not the calendar order --------------------------------
#
# The line picked `sorted(open_rounds)[:1]` — the LOWEST round number with an
# open sheet — which is only the next deadline while the calendar runs in
# order. It does not: this project has already carried a round-2 fixture with
# no date at all while rounds 3 and 4 were played.
#
# A round postponed wholesale has no played fixture, so it survives the filter
# above, and then sorts first on its number. The line would read "faltam 220
# dias" for a sheet in April while the one closing in six days went unsaid —
# and this is the LAST line the routine prints, put there deliberately so it
# survives the output being truncated.


def test_a_round_moved_to_april_does_not_hide_the_one_closing_this_week(mod):
    """The defect: lowest number wins, and the number is not the order."""
    soon = datetime.now() + timedelta(days=6)
    # Four months, not eight. A label carries no year, so `when` reads it into
    # the nearest one — and a date further out than about six months comes back
    # as the PAST and is dropped before it ever reaches the ordering. Written
    # with 220 days this test passed against the broken code, for that reason
    # and not for the one it claims.
    far = datetime.now() + timedelta(days=120)
    said = mod.deadlines(
        [
            fixture(6, "a", "b", played=True),
            # Round 7 lifted out of its slot and replayed months later: nothing
            # in it has been played, so it is "open", and 7 < 8.
            fixture(7, "c", "d", kickoff=at(far)),
            fixture(8, "e", "f", kickoff=at(soon)),
        ]
    )
    sheet = next(line for line in said if "§6.13" in line)
    assert "jornada 8" in sheet, (
        "the deadline named the round with the lowest number instead of the "
        "one that closes first"
    )
    assert "jornada 7" not in sheet


def test_the_soonest_kickoff_wins_even_from_a_higher_round(mod):
    """Same fact stated the other way: a high number can be next."""
    soon = datetime.now() + timedelta(days=2)
    later = datetime.now() + timedelta(days=90)
    said = mod.deadlines(
        [
            fixture(9, "a", "b", kickoff=at(later)),
            fixture(30, "c", "d", kickoff=at(soon)),
        ]
    )
    sheet = next(line for line in said if "§6.13" in line)
    assert "jornada 30" in sheet


def test_the_ordinary_calendar_still_names_the_next_round(mod):
    """The fix must not change the case that was already right."""
    first = datetime.now() + timedelta(days=3)
    second = first + timedelta(days=7)
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d", kickoff=at(first)),
            fixture(5, "e", "f", kickoff=at(second)),
        ]
    )
    sheet = next(line for line in said if "§6.13" in line)
    assert "jornada 4" in sheet


def test_no_open_round_says_nothing_rather_than_raising(mod):
    """Every fixture played, or every remaining one undated — the end of a
    season, and the state this project was in for round 5 all August."""
    said = mod.deadlines(
        [
            fixture(3, "a", "b", played=True),
            fixture(4, "c", "d"),  # no kickoff label at all
        ]
    )
    assert not [line for line in said if "§6.13" in line]
