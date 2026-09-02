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


# --- the one deadline with no second chance -----------------------------------
#
# Miss a team sheet and a round is lost. Miss matchday 5 and the squad is fixed
# for the season, the top-scorer bet with it, and unlimited transfers become one
# a round — the gap between a good squad and a careless one runs to several
# hundred points.
#
# And nobody can plan around it: Record had published no date for matchday 5 as
# late as 26 August, four days after matchday 3 was played. It appears when it
# appears, and if that week is one Manuel is not running the routine, the first
# he hears of it is when it has gone.
#
# A step that PRINTS a warning changes nothing — the Actions log is a page
# nobody opens. A step that FAILS sends an email, which is the only push this
# repository has. So `--alerta` exits non-zero on purpose, and these say exactly
# when.


def lock_fixtures(kickoff, mod):
    """Matchday 5, dated or not, with an earlier round already played."""
    return [
        fixture(3, "a", "b", played=True),
        fixture(mod.FIRST_SCORING_MATCHDAY, "c", "d", kickoff=kickoff),
    ]


def test_no_date_is_not_an_alarm(mod):
    """The state all August: the lock exists and nobody has scheduled it."""
    near, said = mod.lock_is_near(lock_fixtures(None, mod), 7)
    assert near is False
    assert "ainda nao tem data" in said


def test_a_distant_date_is_not_an_alarm(mod):
    far = datetime.now() + timedelta(days=30)
    near, said = mod.lock_is_near(lock_fixtures(at(far), mod), 7)
    assert near is False
    assert "faltam" in said


def test_a_date_inside_the_window_is(mod):
    """The whole point: it fires, and says what to do about it."""
    soon = datetime.now() + timedelta(days=3)
    near, said = mod.lock_is_near(lock_fixtures(at(soon), mod), 7)
    assert near is True
    assert "FECHA TUDO" in said
    assert "ilimitadas" in said
    assert "rotina-diaria" in said, "it fires without saying what to run"


def test_a_deadline_already_gone_stops_shouting(mod):
    """Failing every run for the rest of the season teaches him to ignore it."""
    past = datetime.now() - timedelta(days=2)
    near, said = mod.lock_is_near(lock_fixtures(at(past), mod), 7)
    assert near is False
    assert "ja passou" in said


def test_it_measures_from_the_sheet_shutting_not_the_kickoff(mod):
    """§6.13 shuts fifteen minutes before the first match, and that is the
    moment that matters — the difference decides the edge of the window."""
    edge = datetime.now() + timedelta(days=7) + mod.SHEET_CLOSES_BEFORE
    assert mod.lock_is_near(lock_fixtures(at(edge - timedelta(hours=1)), mod), 7)[0]
    assert not mod.lock_is_near(lock_fixtures(at(edge + timedelta(hours=2)), mod), 7)[0]


def test_the_earliest_match_of_the_round_is_the_one_that_counts(mod):
    """A round spread over four days shuts on the first of them."""
    first = datetime.now() + timedelta(days=2)
    later = first + timedelta(days=3)
    fixtures = [
        fixture(3, "a", "b", played=True),
        fixture(mod.FIRST_SCORING_MATCHDAY, "c", "d", kickoff=at(later)),
        fixture(mod.FIRST_SCORING_MATCHDAY, "e", "f", kickoff=at(first)),
    ]
    near, said = mod.lock_is_near(fixtures, 3)
    assert near is True


def test_the_scheduled_job_runs_it():
    """A check nobody calls is a check that does not exist."""
    workflow = (
        ROOT / ".github" / "workflows" / "registar-previsoes.yml"
    ).read_text(encoding="utf-8")
    assert "--alerta" in workflow
    assert "if: always()" in workflow, (
        "the warning is skipped on exactly the days something else went wrong"
    )


# --- a date does not know what has been done ----------------------------------
#
# The alarm fired twice a day for two days after Manuel had already rebuilt the
# whole squad and filed the sheet, telling him each time to go and do a thing he
# had finished. That is how a warning becomes something you learn to ignore, and
# this is the one warning of the season that must not become that.
#
# The squad file carries the round it was entered for, so it knows.


def test_a_squad_filed_for_the_locking_round_counts_as_done(mod, tmp_path, monkeypatch):
    from liga_record_mcp.source import ManualSquadSource

    class Filed:
        round_number = mod.FIRST_SCORING_MATCHDAY

    monkeypatch.setattr(mod, "ManualSquadSource", lambda p: type("L", (), {"load": lambda s: Filed()})())
    assert mod.lock_is_settled(mod.FIRST_SCORING_MATCHDAY)


def test_a_squad_still_on_an_earlier_round_is_not_done(mod, monkeypatch):
    class Filed:
        round_number = mod.FIRST_SCORING_MATCHDAY - 1

    monkeypatch.setattr(mod, "ManualSquadSource", lambda p: type("L", (), {"load": lambda s: Filed()})())
    assert not mod.lock_is_settled(mod.FIRST_SCORING_MATCHDAY)


def test_a_squad_file_that_will_not_load_is_not_a_decision(mod, monkeypatch):
    """Refusing to load is a reason to warn, not a reason to fall silent."""
    def boom(_):
        raise RuntimeError("illegal squad")

    monkeypatch.setattr(mod, "ManualSquadSource", boom)
    assert not mod.lock_is_settled(mod.FIRST_SCORING_MATCHDAY)


def test_the_real_squad_is_filed_for_the_lock():
    """Against the file: he rebuilt it on 1 September."""
    from liga_record_mcp.source import ManualSquadSource

    snap = ManualSquadSource(ROOT / "data" / "squad.yaml").load()
    assert snap.round_number >= 5, "the squad file is behind the locking round"


def test_the_alarm_consults_it():
    source = (ROOT / "scripts" / "pending_decisions.py").read_text(encoding="utf-8")
    assert "lock_is_settled(FIRST_SCORING_MATCHDAY)" in source, (
        "the alarm is a date again, and a date does not know what has been done"
    )
