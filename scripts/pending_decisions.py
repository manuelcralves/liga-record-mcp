"""What the ledger is still waiting for.

The rest of the routine records what the SITE says: who played, what each
player scored, where the team finished. None of it can record what only the
manager knows — which transfer he actually made, whether it was the one that
was suggested, whether a holiday round was spent.

So this asks. Run after a round is scored and it names exactly what is missing,
in the form of the call that would fill it in. Nothing else in the project
prompts for input, and this does not either: it prints and exits.

Why it matters more than it looks. A ledger with the good weeks written down
and the bad ones forgotten is worse than no ledger, because it will be believed.
The only defence is asking every week rather than when something went well.

    python scripts/pending_decisions.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import (  # noqa: E402
    FIRST_SCORING_MATCHDAY,
    HOLIDAY_ROUNDS,
)
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    SiteError,
    holidays_used,
    load_decisions,
)
from liga_record_mcp.stats import last_scored_round  # noqa: E402

DECISIONS_PATH = ROOT / "data" / "decisions.json"


#: The site labels a kickoff "28 AGO 20:15" and carries no year, which is why
#: models.py keeps it as text rather than inventing one. To say how far away a
#: deadline is, a year has to be chosen — so it picks the one that puts the
#: date nearest to today, and refuses to say anything when that is ambiguous.
MONTHS = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}

#: §6.13 — the sheet closes fifteen minutes before the round's first match.
SHEET_CLOSES_BEFORE = timedelta(minutes=15)


def when(label: str | None) -> datetime | None:
    """A kickoff label as a moment, or None if it cannot be read confidently."""
    if not label:
        return None
    parts = label.split()
    if len(parts) < 3 or parts[1].upper() not in MONTHS:
        return None
    try:
        day, hour_minute = int(parts[0]), parts[2]
        hour, minute = (int(x) for x in hour_minute.split(":"))
    except ValueError:
        return None

    now = datetime.now()
    best = None
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            moment = datetime(year, MONTHS[parts[1].upper()], day, hour, minute)
        except ValueError:
            continue
        if best is None or abs(moment - now) < abs(best - now):
            best = moment
    # More than half a year away in either direction means the year guess is
    # doing the work rather than the label, and a wrong deadline is worse than
    # no deadline.
    if best is None or abs(best - now) > timedelta(days=180):
        return None
    return best


def in_words(moment: datetime) -> str:
    """How far off, in the units a person would use."""
    left = moment - datetime.now()
    if left.total_seconds() < 0:
        return "já passou"
    days, seconds = left.days, left.seconds
    if days >= 2:
        return f"faltam {days} dias"
    hours = days * 24 + seconds // 3600
    if hours >= 2:
        return f"faltam {hours} horas"
    return f"faltam {max(1, seconds // 60)} minutos"


def still_open(fixtures) -> dict[int, "datetime"]:
    """Rounds whose team sheet has not shut yet, and when each one shuts.

    A ROUND'S SHEET IS OPEN UNTIL ITS FIRST MATCH, so any round with a result
    already in it is closed — however many of its fixtures are still to come.
    The naive reading, "the lowest round with an unplayed fixture", points at
    round 2, whose sheet shut on 20 August and whose one remaining match was
    postponed to a date nobody has set. That is a deadline that has passed
    presented as one to act on.

    Lives here, in one function, because two callers now need the same answer
    and disagreeing about which round is open is how a decision gets filed
    against a round that has already been played.
    """
    played_in = {f.round_number for f in fixtures if f.played}
    found: dict[int, datetime] = {}
    for fixture in fixtures:
        if fixture.round_number in played_in:
            continue
        moment = when(fixture.kickoff)
        if moment is None or moment < datetime.now():
            continue
        first = found.get(fixture.round_number)
        if first is None or moment < first:
            found[fixture.round_number] = moment
    return found


def next_open(fixtures) -> int | None:
    """The round being decided right now — the open one that shuts soonest."""
    found = still_open(fixtures)
    return min(found, key=found.get) if found else None


def deadlines(fixtures) -> list[str]:
    """What is about to close, and what closes for good.

    Two kinds. The weekly one is §6.13's team sheet, which shuts fifteen
    minutes before the round's first match and is the one he has already missed
    once. The other is matchday 5, where the squad, the top-scorer bet and the
    one-transfer-a-round rule all begin at once — the single decision on this
    project worth the most points, and the only one with no second chance.
    """
    said: list[str] = []

    # A ROUND'S SHEET IS OPEN UNTIL ITS FIRST MATCH, so any round with a result
    # already in it is closed — however many of its fixtures are still to come.
    # The naive reading, "the lowest round with an unplayed fixture", points at
    # round 2, whose sheet shut on 20 August and whose one remaining match was
    # postponed to a date nobody has set. That is a deadline that has passed
    # presented as one to act on.
    open_rounds = still_open(fixtures)

    # BY KICKOFF, NOT BY NUMBER. This was `sorted(open_rounds)[:1]`, which
    # assumes round order is calendar order — and the comments in this very
    # project say it is not. A round postponed wholesale to April has no played
    # fixture, survives the filter above, and sorts first on its number: the
    # line would read "faltam 220 dias" while the sheet closing in six days
    # went unmentioned. And this is the LAST line of the routine, put there on
    # purpose to survive being truncated.
    if open_rounds:
        round_number = min(open_rounds, key=open_rounds.get)
        shuts = open_rounds[round_number] - SHEET_CLOSES_BEFORE
        said.append(
            f"  §6.13: a folha da jornada {round_number} fecha "
            f"{shuts:%d/%m às %H:%M} — {in_words(shuts)}"
        )

    upcoming = sorted({f.round_number for f in fixtures if not f.played})
    locked = [r for r in upcoming if r >= FIRST_SCORING_MATCHDAY]
    lock_line = None
    if locked and locked[0] == FIRST_SCORING_MATCHDAY:
        starts = [
            moment
            for moment in (
                when(f.kickoff)
                for f in fixtures
                if f.round_number == FIRST_SCORING_MATCHDAY
            )
            if moment is not None
        ]
        where = (
            f"{min(starts):%d/%m} — {in_words(min(starts))}"
            if starts
            else "ainda sem data marcada"
        )
        lock_line = (
            f"  A JORNADA {FIRST_SCORING_MATCHDAY} FECHA TUDO: {where}. Até lá as "
            "transferências são ilimitadas; depois é uma por jornada, o plantel "
            "fica fechado e o melhor marcador também."
        )
    # The lock first, the sheet last: the sheet is the one with a clock on it,
    # and the last line is the one that reaches the log.
    return ([lock_line] if lock_line else []) + said


def lock_is_near(fixtures, within_days: int) -> tuple[bool, str]:
    """Whether the matchday-5 lock is dated and close, and what to say about it.

    THE ONLY DEADLINE THAT CANNOT BE RECOVERED. Miss a team sheet and you lose
    a round; miss this and the squad is fixed for the season, the top-scorer bet
    is fixed, and unlimited transfers become one a round. Measured, the gap
    between a good squad and a careless one runs to several hundred points.

    It is also the one nobody can plan around: Record had published no date for
    matchday 5 as late as 26 August, four days after matchday 3 was played. So
    the date appears when it appears, and if it appears in a week Manuel is not
    running the routine, the first he hears of it is when it has gone.
    """
    starts = [
        moment
        for moment in (
            when(f.kickoff)
            for f in fixtures
            if f.round_number == FIRST_SCORING_MATCHDAY
        )
        if moment is not None
    ]
    if not starts:
        return False, (
            f"a jornada {FIRST_SCORING_MATCHDAY} ainda nao tem data — "
            "nada a avisar"
        )
    first = min(starts)
    shuts = first - SHEET_CLOSES_BEFORE
    days = (shuts - datetime.now()).total_seconds() / 86400
    if days < 0:
        return False, f"o prazo da jornada {FIRST_SCORING_MATCHDAY} ja passou"
    if days > within_days:
        return False, (
            f"a jornada {FIRST_SCORING_MATCHDAY} fecha {shuts:%d/%m as %H:%M}, "
            f"faltam {days:.0f} dias — ainda nao e para avisar"
        )
    return True, (
        f"O PRAZO QUE FECHA TUDO: a jornada {FIRST_SCORING_MATCHDAY} fecha "
        f"{shuts:%d/%m as %H:%M}, {in_words(shuts)}.\n"
        "Ate la as transferencias sao ilimitadas e o plantel inteiro pode "
        "ser reconstruido de uma vez.\n"
        "Depois disso e uma por jornada, o plantel fica fechado e o melhor "
        "marcador tambem.\n"
        "Corre  scripts/rotina-diaria.bat  e ve a proposta em docs/private."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, default=DECISIONS_PATH)
    parser.add_argument(
        "--alerta",
        type=int,
        metavar="DIAS",
        help="sair com erro se o prazo da jornada 5 estiver a menos de DIAS. "
             "Um trabalho que falha manda email; um que passa nao manda nada, "
             "e este e o unico prazo da epoca que nao tem segunda hipotese.",
    )
    args = parser.parse_args()

    if args.alerta is not None:
        try:
            fixtures = LigaRecordClient(timeout=40.0).fixtures()
        except SiteError as exc:
            raise SystemExit(f"could not read the calendar: {exc}")
        near, said = lock_is_near(fixtures, args.alerta)
        print(said)
        raise SystemExit(1 if near else 0)

    store = load_decisions(args.decisions)
    recorded = {int(r) for r in (store.get("rounds") or {})}

    try:
        fixtures = LigaRecordClient(timeout=40.0).fixtures()
        latest = last_scored_round(fixtures)
    except SiteError as exc:
        raise SystemExit(f"could not read the calendar: {exc}")
    if latest is None:
        print("no round has been scored yet")
        return

    missing = [r for r in range(1, latest + 1) if r not in recorded]
    used = holidays_used(store)


    if not missing:
        print(f"nothing pending — rounds 1-{latest} are all on file")
    else:
        print(f"rounds scored but not recorded: {', '.join(map(str, missing))}")
        print()
        print("  the score and the rank are read from the site. What is needed is")
        print("  what only you know — the transfer you made, whether it was the")
        print("  one suggested, and whether a holiday round was spent:")
        for round_number in missing:
            print(f"    settle_decision({round_number}, transfer_out=..., transfer_in=...)")

    if not store.get("season", {}).get("top_scorer"):
        print()
        print("  §10.3(m): no top-scorer bet on file. It is free, it is made once")
        print("  when the team is first submitted, and it is worth up to 20 points.")

    print()
    print(
        f"  §6.17: {len(used)} of {HOLIDAY_ROUNDS} holiday rounds used"
        + (f" (rounds {', '.join(map(str, used))})" if used else "")
        + f", {HOLIDAY_ROUNDS - len(used)} left"
    )

    # LAST, and that is not a style choice. routine.py logs the final line of
    # each step, so whatever ends this output is what he sees on a day he does
    # not open the page. Everything above describes work that can be done
    # whenever; a deadline is the only thing here that stops being true, and
    # the nearest one goes last so it is the line that survives.
    said = deadlines(fixtures)
    if said:
        print()
        for line in said:
            print(line)


if __name__ == "__main__":
    main()
