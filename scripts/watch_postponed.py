"""Watch a postponed fixture, and settle the one rule nobody has verified.

THE QUESTION. §15.3 reads as scoring a club's players zero when their match is
not played before the next round begins. If that reading is right, ten of the
twenty-three in a squad can be worth nothing for a round through no fault of
anyone's, and the projection on the page — which estimates them as if they will
play — is wrong for those rounds. If it is wrong, and the points simply arrive
late, then everything already does the right thing.

WHAT IS KNOWN, which is not enough. When a match is not played, the fifty-seven
players of the two clubs sit at 0, while 48.5% of the players whose clubs did
play sit at -1. §10.3(i)'s -1 is the code for "did not play", so a 0 looks like
"not yet assigned" rather than "assigned zero". That is a reading of a pattern,
not a proof, and the project has carried it as an open question for days.

WHAT SETTLES IT. Sp. Braga against Gil Vicente, round 2, postponed on 16 August
for an outbreak in the Braga squad and not yet rescheduled. When it is played,
either points appear for round 2 or they do not, and the question is answered
by the league rather than by argument.

HOW THIS ANSWERS IT. Only totals are public — `points_total` is a running sum
and `points_round` is the latest round alone. So a single reading proves
nothing; the evidence is in the CHANGE across the moment the match is played.
This records a snapshot whenever something moves, and when an outstanding
fixture turns played it reports what happened to those players against what
happened to a control group whose clubs played every round.

    python scripts/watch_postponed.py            # observe, and report a verdict
    python scripts/watch_postponed.py --quiet    # only speak when something moved

Safe on a timer, and it writes nothing on a run that sees nothing new — the
same rule the rest of the routine follows, and for the same reason: this file
has two writers.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import LigaRecordClient  # noqa: E402
from liga_record_mcp.stats import clubs_playing_in  # noqa: E402

WATCH_PATH = ROOT / "data" / "postponed-watch.json"

#: How many unaffected players to carry as a control. Their clubs played every
#: round, so whatever their totals do between two readings is what a round of
#: ordinary football does — which is the only way to read the affected group's
#: change as meaning anything.
CONTROL_SIZE = 40


def outstanding(fixtures) -> list[dict]:
    """Fixtures whose round has been left behind by the calendar.

    Not merely unplayed: unplayed while a LATER round has already been scored.
    That is the §15.3 condition, and it is also what makes the observation
    worth anything — a fixture postponed within its own week tells us nothing,
    because nobody has moved on yet.
    """
    scored = {
        fixture.round_number for fixture in fixtures if fixture.played
    }
    if not scored:
        return []
    latest = max(scored)
    return [
        {
            "round": fixture.round_number,
            "home": fixture.home,
            "away": fixture.away,
            "played": False,
        }
        for fixture in fixtures
        if not fixture.played and fixture.round_number < latest
    ]


def read_market(market) -> dict[str, dict]:
    return {
        player.id: {
            "name": player.name,
            "club": player.club,
            "total": player.points_total,
            "round": player.points_round,
        }
        for position in Position
        for player in market.search(position)
    }


def observe(market) -> dict:
    fixtures = market.fixtures()
    waiting = outstanding(fixtures)
    everyone = read_market(market)

    affected_clubs = {c for f in waiting for c in (f["home"], f["away"])}

    # The control: clubs with nothing outstanding anywhere. Sorted by id so the
    # same forty come back every run — a control group that changes membership
    # between readings measures the membership, not the football.
    played_every = {
        club
        for f in fixtures
        if f.played
        for club in (f.home, f.away)
    } - affected_clubs
    control = sorted(
        (i for i, p in everyone.items() if p["club"] in played_every),
    )[:CONTROL_SIZE]

    return {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "outstanding": waiting,
        "affected": {
            i: p for i, p in everyone.items() if p["club"] in affected_clubs
        },
        "control": {i: everyone[i] for i in control},
    }


def moved(before: dict, after: dict) -> dict[str, int]:
    """How much each player's running total changed between two readings."""
    return {
        i: after[i]["total"] - before[i]["total"]
        for i in after
        if i in before and after[i]["total"] != before[i]["total"]
    }


def verdict(before: dict, after: dict) -> str | None:
    """What the pair of readings says, if it says anything.

    A fixture that was outstanding and is now gone from the list has been
    played. The affected players either gained points across that moment or
    they did not, and the control says how much a round of football moves a
    total in the same window.
    """
    was = {(f["round"], f["home"], f["away"]) for f in before["outstanding"]}
    now = {(f["round"], f["home"], f["away"]) for f in after["outstanding"]}
    settled = was - now
    if not settled:
        return None

    hit = moved(before["affected"], after["affected"])
    ctrl = moved(before["control"], after["control"])
    played = ", ".join(f"{h}–{a} (jornada {r})" for r, h, a in sorted(settled))

    lines = [f"  O jogo em falta realizou-se: {played}"]
    lines.append(
        f"  Dos {len(after['affected'])} jogadores desses clubes, "
        f"{len(hit)} mudaram de total."
    )
    if ctrl:
        lines.append(
            f"  No grupo de controlo, {len(ctrl)} de {len(after['control'])} "
            "mudaram — foi jogada mais alguma coisa neste intervalo, portanto "
            "a mudança não é atribuível só ao jogo adiado."
        )
    else:
        lines.append(
            "  No grupo de controlo não mexeu ninguém, portanto o que mudou "
            "acima só pode vir do jogo adiado."
        )

    if not hit:
        lines.append(
            "  VEREDICTO: nenhum ganhou nada. O §15.3 zera mesmo os jogadores "
            "de um jogo adiado, e a projeção tem de deixar de os estimar como "
            "se fossem jogar."
        )
    elif not ctrl:
        lines.append(
            "  VEREDICTO: os pontos da jornada adiada foram atribuídos. O 0 era "
            "«ainda não atribuído», e a projeção está certa como está."
        )
    else:
        lines.append(
            "  VEREDICTO: por decidir — os dois grupos mexeram. Compara os "
            "totais em data/postponed-watch.json à mão."
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    market = LigaRecordClient(timeout=60.0)
    now = observe(market)

    log = (
        json.loads(WATCH_PATH.read_text(encoding="utf-8"))
        if WATCH_PATH.exists()
        else {"readings": []}
    )
    readings = log["readings"]
    previous = readings[-1] if readings else None

    said = verdict(previous, now) if previous else None

    if not now["outstanding"] and not said:
        if not args.quiet:
            print("  nenhum jogo em falta — nada a observar")
        return

    # Written only when something actually moved, for the same reason the
    # settle step is: a no-op that still dirties the file makes the laptop and
    # the job on GitHub diverge on every single run.
    changed = previous is None or (
        moved(previous["affected"], now["affected"])
        or moved(previous["control"], now["control"])
        or previous["outstanding"] != now["outstanding"]
    )
    if changed:
        readings.append(now)
        WATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        WATCH_PATH.write_text(
            json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    if said:
        print(said)
        return

    waiting = ", ".join(
        f"{f['home']}–{f['away']} (jornada {f['round']})" for f in now["outstanding"]
    )
    if changed and not args.quiet:
        print(f"  a observar {waiting} — {len(now['affected'])} jogadores em espera")
    elif changed:
        print(f"  a observar {waiting}")
    elif not args.quiet:
        print(f"  {waiting} continua em falta, sem alterações")


if __name__ == "__main__":
    main()
