"""Choose WORTH_MOVING against noise that cancels instead of noise that stacks.

WHY THIS EXISTS. `WORTH_MOVING = 7.0` was chosen from a table of four seeds per
setting, and every one of those seasons had been played with a single Monte
Carlo stream restarted at every matchday — twenty-five chip decisions against
twenty-five copies of one sample. The error stopped cancelling across a season
and accumulated common-mode instead, which inflated exactly the spread the
constant was read off. With the stream varied per matchday the ordering
reverses: greedy stopped tying and started winning by fifty.

Neither table decides anything. Both are single draws.

WHAT THIS DOES DIFFERENTLY, and it is the whole point: the distribution at a
matchday does not depend on the threshold. Same season, same seed, same table,
same remaining fixtures — the spread is identical whether the policy is greedy
or miserly. So it is computed ONCE and every threshold is scored against the
same one.

That makes the comparison PAIRED. Season-to-season variance is over a hundred
points and swamps any policy difference when the means are compared unpaired;
paired, that variance is differenced away and what is left is the thing being
measured. The tables below therefore lead with the DIFFERENCE from a baseline
and its standard error, not with the raw scores.

    python scripts/sweep_worth_moving.py
    python scripts/sweep_worth_moving.py --seeds 20 --draws 4000
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from liga_record_mcp.final_table import (  # noqa: E402
    BONUS_REACH,
    BONUS_ROUNDS,
    LAST_CHIP_ROUND,
    WEEKLY_REACH,
    best_order,
    chip_plan,
    distribution,
    score,
    strengths,
)
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY  # noqa: E402
from liga_record_mcp.source import OpenFootballClient  # noqa: E402

from backtest_final_table import PAIRS, records_from, table_from  # noqa: E402

#: The settings under test. 0.0 takes every improving move; 20 takes almost
#: none. Seven is the incumbent and is the baseline the differences are against.
THRESHOLDS = [0.0, 0.5, 2.0, 4.0, 7.0, 10.0, 15.0, 20.0]
BASELINE = 7.0


def spreads_for(season, clubs, archive, *, draws, seed, weight=1.0) -> dict:
    """Every matchday's place distribution for one season under one seed.

    Nothing here looks forward: at matchday r the table is built from rounds
    before r only, the same information a chip decision has on the Thursday.
    """
    out = {}
    for matchday in range(FIRST_SCORING_MATCHDAY, LAST_CHIP_ROUND + 1):
        played = [f for f in season if f.round_number < matchday]
        remaining = [(f.home, f.away) for f in season if f.round_number >= matchday]
        table = table_from(played, clubs)
        strength = strengths(archive, table, recent_weight=weight)
        out[matchday] = distribution(
            table, remaining, strength, draws=draws, seed=seed * 1000 + matchday
        )
    return out


def play_with(spreads, clubs, threshold: float) -> dict:
    """The entry a threshold produces, given distributions already drawn."""
    order = best_order(spreads[FIRST_SCORING_MATCHDAY], clubs=clubs)
    chips = places = 0
    # `chip_plan` is the policy production plays. Measuring a second copy of it
    # would answer a question about this script rather than about the model.
    for matchday in range(FIRST_SCORING_MATCHDAY + 1, LAST_CHIP_ROUND + 1):
        order, plays = chip_plan(
            order, spreads[matchday], matchday, threshold=threshold
        )
        for play in plays:
            if play["club"] is not None:
                chips += 1
                places += play["places"]
    return {"order": order, "chips": chips, "places": places}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=2000)
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args()

    client = OpenFootballClient(timeout=60.0)
    needed = sorted({s for pair in PAIRS for s in (pair[0], *pair[1])})
    fixtures = {tag: client.season_fixtures(tag) for tag in needed}

    print(
        f"{len(PAIRS)} epocas x {args.seeds} sementes x {len(THRESHOLDS)} limiares, "
        f"{args.draws} sorteios"
    )
    print("as distribuicoes sao partilhadas entre limiares, logo a comparacao e emparelhada")
    print()

    got = {t: [] for t in THRESHOLDS}
    cost = {t: [] for t in THRESHOLDS}
    moved = {t: [] for t in THRESHOLDS}

    for target, archive_tags in PAIRS:
        season = fixtures[target]
        clubs = sorted({c for f in season for c in (f.home, f.away)})
        archive = records_from([f for tag in archive_tags for f in fixtures[tag]])
        actual = [row["club"] for row in table_from(season, clubs)]

        for seed in range(args.seeds):
            drawn = spreads_for(season, clubs, archive, draws=args.draws, seed=seed)
            for threshold in THRESHOLDS:
                run = play_with(drawn, clubs, threshold)
                got[threshold].append(score(run["order"], actual)["total"])
                cost[threshold].append(run["chips"])
                moved[threshold].append(run["places"])
        print(f"  {target} feita")

    print()
    print(f"  {'limiar':>7}{'media':>8}{'chips':>7}{'lugares':>9}"
          f"{'vs 7':>8}{'erro padrao':>13}{'':>4}")
    base = got[BASELINE]
    for t in THRESHOLDS:
        gap = [a - b for a, b in zip(got[t], base)]
        mean_gap = statistics.fmean(gap)
        # Paired standard error: the season-to-season variance is differenced
        # away, so this is the error on the POLICY difference and nothing else.
        se = (
            statistics.stdev(gap) / len(gap) ** 0.5
            if len(gap) > 1 and any(g != gap[0] for g in gap)
            else 0.0
        )
        mark = ""
        if se and abs(mean_gap) > 2 * se:
            mark = "  <-" if mean_gap > 0 else "  (pior)"
        print(
            f"  {t:>7.1f}{statistics.fmean(got[t]):>8.0f}"
            f"{statistics.fmean(cost[t]):>7.1f}{statistics.fmean(moved[t]):>9.1f}"
            f"{mean_gap:>+8.1f}{se:>13.1f}{mark}"
        )
    print()
    print("  <- = melhor que 7.0 por mais de dois erros padrao, emparelhado")
    print(f"  n = {len(base)} observacoes por limiar")


if __name__ == "__main__":
    main()
