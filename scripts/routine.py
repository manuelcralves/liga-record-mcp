"""The weekly routine, safe to run every day.

Every step below refuses to do the wrong thing rather than doing it quietly,
which is what makes running this on a timer sound rather than reckless:

  record   refuses once the round has kicked off, and refuses to overwrite a
           round already on file. So it writes exactly once per round, on the
           first run after the squad file names a new one.
  settle   only fills in players whose club has actually played, leaves the
           rest pending, and never re-settles what is already recorded.
  history  skips rounds it already holds.
  build    regenerates both pages from whatever is now true.

Nothing here is destructive and nothing overwrites a prediction after the
fact, so a run that does nothing is a normal outcome, not a failure. The exit
code is 0 unless a step failed for a reason other than refusing.

    python scripts/routine.py            # do whatever is due
    python scripts/routine.py --quiet    # only report what changed

ON A TIMER. Everything above is why this is safe to schedule: a run with
nothing to do is the normal outcome, not a failure. Pass --log so the run
leaves a trace — a scheduled task has nowhere to print, and a job nobody can
read is one that fails silently for as long as it takes somebody to notice.

The scheduled task is "Liga Record", daily at 09:00, and it does NOT invoke
this file directly — it runs scripts/rotina-diaria.bat, which invokes it. That
indirection is not cosmetic. Pointed straight at python.exe the task died with
0xC000013A having written nothing anywhere: no log entry, no traceback, only a
number in the scheduler, while the same command line by hand exited 0. Going
through the batch file fixed it and, more to the point, made the next failure
legible — it redirects the raw output to data/routine.err, so whatever kills a
run leaves its reason on disk instead of taking it down with the process.

    Get-ScheduledTask -TaskName 'Liga Record'        # what is scheduled
    Get-ScheduledTaskInfo -TaskName 'Liga Record'    # last result, next run

The private league needs LIGA_RECORD_LEAGUE in the environment. Set it once at
user scope rather than writing it into the task, so the guid stays out of every
file: it grants read access to twenty-nine other people's results. A task
starting from the user's environment picks it up — confirmed by the private
league's page appearing in a scheduled run's output, and by the warning about
the variable being absent from it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # a POSIX checkout, or a bare interpreter
    PYTHON = Path(sys.executable)

class Step(NamedTuple):
    """One thing the routine does, and what it means when it declines.

    The slug is what --only takes. It exists because not every place that runs
    this wants every step: the scheduled task here wants all six, and the job
    on GitHub wants the two that write the ledger and nothing that writes a
    page. Selecting steps beats a second copy of the refusal table somewhere
    else — a copy drifts, and the drift shows up as a job reporting failure
    for a step that declined exactly as designed.
    """

    slug: str
    label: str
    command: list[str]
    #: A phrase the step prints when it is declining ON PURPOSE. Refusals are
    #: expected — most runs have nothing to do — so a decline is never reported
    #: as a failure.
    declines: tuple[str, ...] = ()
    #: Whether this step is the poorer for LIGA_RECORD_LEAGUE being absent.
    #: Only the private page reads the private league; warning about it while
    #: running steps that never touch it trains the reader to ignore warnings.
    needs_league: bool = False


STEPS = [
    Step(
        "registar",
        "registar projeções",
        ["scripts/record_projection.py"],
        ("already on file", "has already begun"),
    ),
    Step("liquidar", "liquidar a jornada", ["scripts/record_projection.py", "--settle"]),
    Step("historico", "histórico por jornada", ["scripts/record_history.py"]),
    # Watches the one rule nobody has verified: whether a postponed fixture
    # eventually awards points for its round, or §15.3 zeroes it. The answer
    # arrives whenever Sp. Braga–Gil Vicente is finally played, which is a date
    # nobody has set — so it has to be waiting rather than remembered.
    Step("adiado", "jogo adiado", ["scripts/watch_postponed.py", "--quiet"]),
    Step("privada", "página privada", ["scripts/build_dashboard.py"], needs_league=True),
    Step("publica", "página pública", ["scripts/build_dashboard.py", "--public"]),
    # Last, and it changes nothing: it says what the ledger is still waiting
    # for. Everything above records what the site says; this names the part
    # only Manuel knows, which is the part that goes missing.
    Step("pendentes", "decisões em falta", ["scripts/pending_decisions.py"]),
]


def run(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        [str(PYTHON), "-X", "utf8", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    lines = [ln for ln in output.splitlines() if ln.strip() and "HTTP Request" not in ln]
    return result.returncode, "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="PASSO",
        choices=[step.slug for step in STEPS],
        help=(
            "run only these steps, in the order listed here: "
            + ", ".join(step.slug for step in STEPS)
        ),
    )
    parser.add_argument(
        "--log",
        type=Path,
        help=(
            "append this run to a file as well as printing it. A scheduled "
            "task has nowhere to print to, and a run nobody can read is a run "
            "that failed silently for however long it took someone to notice"
        ),
    )
    args = parser.parse_args()

    said: list[str] = []

    def say(line: str) -> None:
        print(line)
        said.append(line)

    chosen = [s for s in STEPS if args.only is None or s.slug in args.only]

    if any(step.needs_league for step in chosen) and not os.environ.get(
        "LIGA_RECORD_LEAGUE"
    ):
        say("LIGA_RECORD_LEAGUE is not set — the private league will be missing")

    failures = []
    for slug, label, command, declines, _ in chosen:
        code, output = run(command)
        tail = output.splitlines()[-1] if output else ""
        declined = any(phrase in output for phrase in declines)

        if code == 0:
            say(f"  ✓ {label}: {tail}")
        elif declined:
            if not args.quiet:
                say(f"  · {label}: nada a fazer ({tail})")
        else:
            failures.append(label)
            say(f"  ✗ {label}: {tail}")

    if args.log:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as handle:
            handle.write(f"=== {stamp} UTC" + chr(10))
            handle.write(chr(10).join(said) + chr(10) + chr(10))

    if failures:
        raise SystemExit(f"falhou: {', '.join(failures)}")


if __name__ == "__main__":
    main()
