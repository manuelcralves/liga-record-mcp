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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import HOLIDAY_ROUNDS  # noqa: E402
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    SiteError,
    holidays_used,
    load_decisions,
)
from liga_record_mcp.stats import last_scored_round  # noqa: E402

DECISIONS_PATH = ROOT / "data" / "decisions.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", type=Path, default=DECISIONS_PATH)
    args = parser.parse_args()

    store = load_decisions(args.decisions)
    recorded = {int(r) for r in (store.get("rounds") or {})}

    try:
        latest = last_scored_round(LigaRecordClient(timeout=40.0).fixtures())
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


if __name__ == "__main__":
    main()
