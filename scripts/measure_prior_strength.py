"""Measure how much a player's own form is worth against what his club is.

`PRIOR_STRENGTH` decides everything the projection says early in a season. It
is how many rounds of a player's own record it takes before that record
outweighs the prior built from his club and position. It has been 4.0 since the
projection was written, and the comment beside it admits what it is:

    4 is deliberately more conservative than the data alone would justify.

There was no way to do better. Liga Record publishes no past scores, so there
was no season to test a hot start against. Now there is one — reconstructed,
match by match, from §10.3 and zerozero's record — and the constant can be
measured instead of chosen.

THE MEASUREMENT. Stand at the end of round R, knowing only rounds 1 to R.
Predict each player's rate over the rest of the season two ways: from his own
record so far, and from the record of everyone at his club in his position.
Blend them at weight K and see which K predicts the rest of the season best.
Repeat at every R, so the answer is not an artefact of one moment in the year.

WHAT IT CANNOT SETTLE. The production prior also uses the price Record set
before the season, and last season's prices are gone. So this measures the
club-and-position half of the prior, which is the half that carries the
shrinkage. Read it as the shape of the answer, not the last word on it.

    python scripts/measure_prior_strength.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.stats import PRIOR_STRENGTH  # noqa: E402

SEASON_PATH = ROOT / "data" / "last-season.json"

#: The weights to try. Zero trusts the player's own record completely; a large
#: number ignores it until very late in the season. Whatever the code is
#: actually using is always among them, so this can never report a best value
#: without reporting what it is being compared against.
WEIGHTS = tuple(
    sorted({0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 20.0, 40.0, PRIOR_STRENGTH})
)

#: Rounds to stand at. Before three there is nothing to learn from, and after
#: twenty-six there is too little season left to be judged on.
SPLITS = range(3, 27)

#: A player needs to be around for the verdict to mean anything about him.
MINIMUM_AFTER = 4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=Path, default=SEASON_PATH)
    parser.add_argument(
        "--objective-only",
        action="store_true",
        help=(
            "score on the exact half only, dropping the estimated mark. If the "
            "answer moves, the estimate is driving it rather than the football"
        ),
    )
    args = parser.parse_args()

    if not args.season.exists():
        raise SystemExit(f"no {args.season} — run scripts/build_last_season.py first")
    loaded = json.loads(args.season.read_text(encoding="utf-8"))
    players = loaded["players"]

    # Rounds keyed by player, so a split is a slice rather than a scan.
    scores: dict[str, dict[int, float]] = {}
    cell: dict[str, tuple[str, str]] = {}
    for player_id, player in players.items():
        by_round = {
            int(m["round"]): float(m["points"])
            - (float(m["mark"]) if args.objective_only else 0.0)
            for m in player["matches"]
        }
        if by_round:
            scores[player_id] = by_round
            cell[player_id] = (player["club"], player["position"])

    print(f"{len(scores)} players with a top-flight season {loaded.get('season')}")
    print()

    errors: dict[float, list[float]] = defaultdict(list)
    best_by_split: list[tuple[int, float]] = []

    for split in SPLITS:
        # What everyone at a club in a position has returned so far. This is
        # the prior: it knows nothing about the individual, and because it is
        # pooled over a dozen players it is far better estimated than any one
        # man's fortnight.
        pooled: dict[tuple[str, str], list[float]] = defaultdict(list)
        for player_id, by_round in scores.items():
            pooled[cell[player_id]].extend(
                points for rnd, points in by_round.items() if rnd <= split
            )
        everyone = [p for values in pooled.values() for p in values]
        league_mean = statistics.mean(everyone) if everyone else 0.0
        prior_of = {
            key: statistics.mean(values) if values else league_mean
            for key, values in pooled.items()
        }

        split_errors: dict[float, list[float]] = defaultdict(list)
        for player_id, by_round in scores.items():
            before = [p for rnd, p in by_round.items() if rnd <= split]
            after = [p for rnd, p in by_round.items() if rnd > split]
            if not before or len(after) < MINIMUM_AFTER:
                continue
            observed = statistics.mean(before)
            prior = prior_of[cell[player_id]]
            truth = statistics.mean(after)
            for weight in WEIGHTS:
                blended = (observed * len(before) + prior * weight) / (
                    len(before) + weight
                )
                split_errors[weight].append(abs(blended - truth))

        if not split_errors[WEIGHTS[0]]:
            continue
        for weight, values in split_errors.items():
            errors[weight].extend(values)
        best = min(WEIGHTS, key=lambda w: statistics.mean(split_errors[w]))
        best_by_split.append((split, best))

    print("  K = how many rounds the club-and-position prior is worth")
    print(f"  {'K':>6} {'error':>8}   {'':<30}")
    ranked = sorted(WEIGHTS, key=lambda w: statistics.mean(errors[w]))
    floor = statistics.mean(errors[ranked[0]])
    for weight in WEIGHTS:
        mean_error = statistics.mean(errors[weight])
        bar = "#" * round((mean_error - floor) * 200)
        marks = []
        if weight == ranked[0]:
            marks.append("best")
        if weight == PRIOR_STRENGTH:
            marks.append("in use today")
        note = ("  <- " + ", ".join(marks)) if marks else ""
        print(f"  {weight:>6.1f} {mean_error:>8.3f}   {bar}{note}")

    print()
    print(f"  measured over {len(errors[WEIGHTS[0]])} player-splits")
    counted = defaultdict(int)
    for _, best in best_by_split:
        counted[best] += 1
    order = sorted(counted, key=lambda w: -counted[w])
    print(
        "  best at each round of the season: "
        + ", ".join(f"K={w:g} in {counted[w]}" for w in order)
    )

    pure_form = statistics.mean(errors[0.0])
    pure_prior = statistics.mean(errors[max(WEIGHTS)])
    print()
    print(f"  trusting his own record alone   {pure_form:.3f}")
    print(f"  trusting the club alone         {pure_prior:.3f}")
    print(f"  best blend (K={ranked[0]:g})              {floor:.3f}")
    print(f"  in use today  (K={PRIOR_STRENGTH:g})            "
          f"{statistics.mean(errors[PRIOR_STRENGTH]):.3f}")


if __name__ == "__main__":
    main()
