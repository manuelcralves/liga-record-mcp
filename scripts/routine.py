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
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():  # a POSIX checkout, or a bare interpreter
    PYTHON = Path(sys.executable)

#: Refusals are expected. Each carries a phrase the step prints when it is
#: declining on purpose, so a decline is never reported as a failure.
STEPS = [
    (
        "registar projeções",
        ["scripts/record_projection.py"],
        ("already on file", "has already begun"),
    ),
    ("liquidar a jornada", ["scripts/record_projection.py", "--settle"], ()),
    ("histórico por jornada", ["scripts/record_history.py"], ()),
    ("página privada", ["scripts/build_dashboard.py"], ()),
    ("página pública", ["scripts/build_dashboard.py", "--public"], ()),
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
    args = parser.parse_args()

    if not os.environ.get("LIGA_RECORD_LEAGUE"):
        print("LIGA_RECORD_LEAGUE is not set — the private league will be missing")

    failures = []
    for label, command, declines in STEPS:
        code, output = run(command)
        tail = output.splitlines()[-1] if output else ""
        declined = any(phrase in output for phrase in declines)

        if code == 0:
            print(f"  ✓ {label}: {tail}")
        elif declined:
            if not args.quiet:
                print(f"  · {label}: nada a fazer ({tail})")
        else:
            failures.append(label)
            print(f"  ✗ {label}: {tail}")

    if failures:
        raise SystemExit(f"falhou: {', '.join(failures)}")


if __name__ == "__main__":
    main()
