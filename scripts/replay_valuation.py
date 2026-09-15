"""Which chance of playing, and which evidence, for the model that advises.

Two questions, one replay, both asked of `advice.valuation` — the estimator the
ledger and the pages use.

THE FIRST WAS ABOUT ROUNDS 1-5. On 15 September 2026 Liga Record put every
player's total back to zero for the official phase, and rounds 1-5 of this
season went with it. The weekly emails the model now reads begin at matchday
6, and the files kept of the trial rounds hold only the squad's 23. Rebuilding
those rounds for all 542 players means reading zerozero again for every one of
them, so this asked first whether they would change anything.

THE ANSWER EXPOSED THE SECOND. This was the first replay of `valuation`: every
other measurement script replays `backtest.two_part_projection`. On the same
player-rounds `valuation` trailed it by 0.07 and 0.09 of correlation, and
swapping the two halves between them put all of it in the chance of playing.
`valuation` weighed every round of the season alike; the harness weights the
last two — which is also the likeliest reason early rounds looked harmful:
they made a slow rule slower. So the replay compares valuation's two rules:

    season     this season's rate, anchored to the archive, every round alike
    recent     his last two rounds, pulled toward his whole record

Stand before matchday M of a reconstructed season, knowing only its rounds
before M and, for 2025/26, the whole of 2024/25 as archive. Value every player
under both rules and in two arms, the arms differing in nothing but where this
season starts:

    with       rounds 1 .. M-1
    without    rounds 6 .. M-1    what the model has had since the reset

and score each against what he did at M, from matchday 7 — the first round the
reset leaves any evidence for — to 34.

Two readings of "a round he was there for", because the truth sits between them:

    every round    a round without a row is a -1. The existing harness reads a
                   season this way, and so does the weekly email, which lists
                   every player in the market whether he was used or not.
    rows only      only the rounds the reconstruction has a row for count, the
                   way `archive_records` reads an archive. A January signing is
                   not charged for the autumn he spent elsewhere.

What it leaves out, on purpose. No fixture adjustment: it scales what a player
returns identically everywhere and would only blur the comparison. And rounds
1-5 here come from the same reconstruction as every other round, so this
measures what those rounds are worth, not how closely zerozero estimates them.

    python scripts/replay_valuation.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from liga_record_mcp.advice import valuation  # noqa: E402
from liga_record_mcp.models import (  # noqa: E402
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    Position,
)
from liga_record_mcp.source.last_season import archive_records  # noqa: E402
from liga_record_mcp.stats import UNUSED_PENALTY  # noqa: E402

DATA = ROOT / "data"

#: Each replayed season, and the archive it is allowed to remember. 2024/25 has
#: none: no earlier season was ever reconstructed.
SEASONS = (
    ("2025/26", "last-season.json", "season-2024-25.json"),
    ("2024/25", "season-2024-25.json", None),
)

#: The two rules for the chance of playing, as `valuation(recency=...)`.
RULES = (("season", False), ("recent", True))

#: Where this season's evidence begins, in each arm.
ARMS = (("with", 1), ("without", FIRST_SCORING_MATCHDAY))

READINGS = ((True, "every round counts"), (False, "rows only"))

#: The first round the reset leaves any evidence for.
FIRST_PREDICTED = FIRST_SCORING_MATCHDAY + 1

#: In rounds 7-12 the first five rounds are most of the season; by 34 they are a
#: seventh of it. Decisions are read on the first window and on the whole.
WINDOWS = (
    ("7-12", FIRST_PREDICTED, 12),
    ("13-34", 13, LAST_MATCHDAY),
    ("7-34", FIRST_PREDICTED, LAST_MATCHDAY),
)

#: The bar every change to this model has been held to, on correlation.
BAR = 0.003

ABSENT = float(UNUSED_PENALTY)

DRAWS = 500
SEED = 20260915

Scored = tuple[str, int, float, float, float, bool]


def rows_by_round(
    players: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[int, Mapping[str, Any]]]:
    """Each player's rows keyed by round, for every player with a row at all."""
    out: dict[str, dict[int, Mapping[str, Any]]] = {}
    for player_id, player in players.items():
        by_round: dict[int, Mapping[str, Any]] = {}
        for row in player.get("matches") or []:
            number = int(row["round"])
            # Checked on both files the day this was written: none. A second
            # row would be a cup tie the calendar check let through, and keeping
            # either one silently would score a match that did not count.
            if number in by_round:
                raise ValueError(f"{player_id} has two rows for round {number}")
            by_round[number] = row
        if by_round:
            out[player_id] = by_round
    return out


def season_so_far(
    season: Mapping[str, Mapping[int, Mapping[str, Any]]],
    *,
    first: int,
    upto: int,
    every_round: bool,
) -> dict[str, dict[str, Any]]:
    """`played`, `points`, `available` and `rounds` over rounds `first` to `upto - 1`.

    The shape `valuation` reads as `current`, and never a round from `upto` on.
    `points` counts only the rounds he played, as the live reading does: the -1
    for a round on the bench belongs to the other half of the estimate.
    `rounds` maps each round he was there for to whether he played, which is
    what the recent rule reads.
    """
    out: dict[str, dict[str, Any]] = {}
    for player_id, rows in season.items():
        took_part: dict[int, bool] = {}
        points = 0.0
        for number in range(first, upto):
            row = rows.get(number)
            if row is None and not every_round:
                continue
            used = row is not None and bool(row.get("used"))
            took_part[number] = used
            if used:
                points += float(row["points"])
        if took_part:
            out[player_id] = {
                "played": sum(took_part.values()),
                "points": points,
                "available": len(took_part),
                "rounds": took_part,
            }
    return out


def club_before(rows: Mapping[int, Mapping[str, Any]], upto: int) -> str:
    """His club as it stood before matchday `upto`.

    The latest row before it; failing that, his first row of the season, which
    names the club he was about to play for. `valuation` pools by the CURRENT
    club, and a man sold in January is not his old club's player in February.
    """
    earlier = [n for n in rows if n < upto and rows[n].get("club")]
    if earlier:
        return rows[max(earlier)]["club"]
    return rows[min(rows)].get("club") or ""


def replay(
    season: Mapping[str, Mapping[int, Mapping[str, Any]]],
    positions: Mapping[str, Position],
    archive: Mapping[str, Mapping[str, Any]],
    *,
    every_round: bool,
    rounds: range,
) -> dict[tuple[str, str], list[Scored]]:
    """Each rule and arm's scored player-rounds: (player, round, truth, expected, playing, used).

    Every rule and arm scores exactly the same player-rounds, so the difference
    between any two is the rule or the evidence, and nothing else.
    """
    out: dict[tuple[str, str], list[Scored]] = {
        (rule, arm): [] for rule, _ in RULES for arm, _ in ARMS
    }
    for upto in rounds:
        players = {
            player_id: SimpleNamespace(
                club=club_before(rows, upto), position=positions[player_id]
            )
            for player_id, rows in season.items()
        }
        truths: dict[str, tuple[float, bool]] = {}
        for player_id, rows in season.items():
            row = rows.get(upto)
            if row is not None:
                truths[player_id] = (float(row["points"]), bool(row.get("used")))
            elif every_round:
                truths[player_id] = (ABSENT, False)
        for arm, first in ARMS:
            current = season_so_far(
                season, first=first, upto=upto, every_round=every_round
            )
            for rule, recency in RULES:
                view = valuation(players, archive, current, recency=recency)
                for player_id, (truth, used) in truths.items():
                    entry = view[player_id]
                    out[(rule, arm)].append(
                        (player_id, upto, truth, entry["expected"], entry["playing"], used)
                    )
    return out


def correlation(pairs: list[tuple[float, float]]) -> float:
    """Pearson's r between truth and estimate — the number that picks an eleven.

    The same quantity `measure_projection_accuracy` prints, in one pass, because
    the interval below computes it a thousand times.
    """
    n = len(pairs)
    if n < 2:
        return 0.0
    st = se = stt = see = ste = 0.0
    for truth, estimate in pairs:
        st += truth
        se += estimate
        stt += truth * truth
        see += estimate * estimate
        ste += truth * estimate
    covariance = ste - st * se / n
    var_truth = stt - st * st / n
    var_estimate = see - se * se / n
    if var_truth <= 0 or var_estimate <= 0:
        return 0.0
    return covariance / math.sqrt(var_truth * var_estimate)


def summary(scored: list[Scored], low: int, high: int) -> dict[str, float]:
    """n, r, mean absolute error and the Brier score of `playing`, over low-high."""
    chosen = [s for s in scored if low <= s[1] <= high]
    n = len(chosen)
    if not n:
        return {"n": 0, "r": 0.0, "error": 0.0, "brier": 0.0}
    pairs = [(truth, expected) for _, _, truth, expected, _, _ in chosen]
    return {
        "n": n,
        "r": correlation(pairs),
        "error": sum(abs(t - e) for t, e in pairs) / n,
        "brier": sum((p - (1.0 if used else 0.0)) ** 2 for *_, p, used in chosen) / n,
    }


def gain_interval(
    better: list[Scored],
    worse: list[Scored],
    low: int,
    high: int,
    *,
    draws: int = DRAWS,
    seed: int = SEED,
) -> tuple[float, float]:
    """A 90% interval for r(better) - r(worse), resampling PLAYERS.

    Paired: a resample takes the same players' rounds from both sides, so a gain
    that belongs to a handful of men comes out as a wide interval instead of a
    confident number.
    """

    def grouped(rows: list[Scored]) -> dict[str, list[tuple[float, float]]]:
        out: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for player_id, number, truth, expected, _, _ in rows:
            if low <= number <= high:
                out[player_id].append((truth, expected))
        return out

    better_by, worse_by = grouped(better), grouped(worse)
    ids = sorted(better_by)
    if not ids:
        return 0.0, 0.0
    rng = random.Random(seed)
    gains = []
    for _ in range(draws):
        pick = [ids[rng.randrange(len(ids))] for _ in ids]
        gains.append(
            correlation([p for i in pick for p in better_by[i]])
            - correlation([p for i in pick for p in worse_by.get(i, [])])
        )
    gains.sort()
    return gains[int(0.05 * draws)], gains[int(0.95 * draws) - 1]


def harness_reference(
    season_path: Path, archive_path: Path | None, rounds: range
) -> list[Scored]:
    """The harness's own estimator, scored on the same player-rounds.

    `two_part_projection` sees every round before M, so it has no arm without.
    It is here for scale: the rule it uses for the chance of playing is the one
    `valuation` is being measured against.
    """
    import measure_projection_accuracy as harness

    from liga_record_mcp.backtest import two_part_projection

    points, minutes, cells, *_ = harness.load(season_path, archive_path)
    out: list[Scored] = []
    for upto in rounds:
        view = two_part_projection(points, minutes, cells, upto=upto)
        for player_id, by_matchday in points.items():
            out.append((player_id, upto, by_matchday[upto], view[player_id], 0.0, False))
    return out


def line(rule: str, arm: str, scored: list[Scored], *, brier: bool = True) -> str:
    """One row of the table: r in every window, error and brier over 7-34."""
    rs = "".join(f"{summary(scored, low, high)['r']:>9.4f}" for _, low, high in WINDOWS)
    whole = summary(scored, FIRST_PREDICTED, LAST_MATCHDAY)
    tail = f"{whole['brier']:>8.4f}" if brier else ""
    return f"    {rule:<8}{arm:<9}{rs}{whole['error']:>8.3f}{tail}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draws", type=int, default=DRAWS, help="bootstrap resamples")
    args = parser.parse_args()

    rounds = range(FIRST_PREDICTED, LAST_MATCHDAY + 1)
    decisive = (WINDOWS[0], WINDOWS[2])
    gains: dict[str, list[tuple[str, str, str, float]]] = defaultdict(list)
    brier_lower: dict[str, list[bool]] = defaultdict(list)
    for label, season_name, archive_name in SEASONS:
        season_path = DATA / season_name
        if not season_path.is_file():
            raise SystemExit(f"no {season_path} — run scripts/build_last_season.py first")
        loaded = json.loads(season_path.read_text(encoding="utf-8"))["players"]
        season = rows_by_round(loaded)
        positions = {i: Position(loaded[i]["position"]) for i in season}
        archive = archive_records(DATA, names=(archive_name,)) if archive_name else {}
        late = sum(1 for rows in season.values() if min(rows) >= FIRST_SCORING_MATCHDAY)

        print()
        print(
            f"{label}: {len(season)} players with a row, archive "
            f"{archive_name or 'none'} ({len(archive)} players), "
            f"{late} first seen after round {FIRST_SCORING_MATCHDAY - 1}"
        )
        reference = harness_reference(
            season_path, DATA / archive_name if archive_name else None, rounds
        )
        for every_round, reading in READINGS:
            scored = replay(
                season, positions, archive, every_round=every_round, rounds=rounds
            )
            print(f"  reading: {reading}")
            print(
                f"    {'rule':<8}{'arm':<9}"
                + "".join(f"{name:>9}" for name, _, _ in WINDOWS)
                + f"{'error':>8}{'brier':>8}   r by rounds; error and brier over 7-34"
            )
            for rule, _ in RULES:
                for arm, _ in ARMS:
                    print(line(rule, arm, scored[(rule, arm)]))
            if every_round:
                print(line("harness", "", reference, brier=False))

            comparisons = [
                (f"recent over season, {arm}", "rule", scored[("recent", arm)], scored[("season", arm)])
                for arm, _ in ARMS
            ] + [
                (
                    "rounds 1-5 under recent",
                    "rounds",
                    scored[("recent", "with")],
                    scored[("recent", "without")],
                )
            ]
            for title, question, better, worse in comparisons:
                cells = []
                for name, low, high in decisive:
                    gain = summary(better, low, high)["r"] - summary(worse, low, high)["r"]
                    spread = gain_interval(better, worse, low, high, draws=args.draws)
                    cells.append(
                        f"{name} {gain:+.4f} (90%: {spread[0]:+.4f} to {spread[1]:+.4f})"
                    )
                    gains[reading].append((label, question, name, gain))
                print(f"    {title:<28}" + "   ".join(cells))
            for arm, _ in ARMS:
                brier_lower[reading].append(
                    summary(scored[("recent", arm)], FIRST_PREDICTED, LAST_MATCHDAY)["brier"]
                    < summary(scored[("season", arm)], FIRST_PREDICTED, LAST_MATCHDAY)["brier"]
                )

    print()
    for _, reading in READINGS:
        found = gains[reading]
        rule = [g for _, question, _, g in found if question == "rule"]
        lower = brier_lower[reading]
        passed = all(g >= BAR for g in rule) and all(lower)
        print(
            f"the recent rule, {reading}: gains over the season rule "
            + ", ".join(f"{g:+.4f}" for g in rule)
            + f" against +{BAR}; brier lower in {sum(lower)} of {len(lower)}"
            + (" — PASSES" if passed else " — FAILS")
        )
        early = [g for _, question, name, g in found if question == "rounds" and name == "7-12"]
        whole = [g for _, question, name, g in found if question == "rounds" and name == "7-34"]
        passed = all(g >= BAR for g in early) and all(g >= 0 for g in whole)
        print(
            f"rounds 1-5 under the recent rule, {reading}: 7-12 gains "
            + ", ".join(f"{g:+.4f}" for g in early)
            + "; 7-34 gains "
            + ", ".join(f"{g:+.4f}" for g in whole)
            + (" — PASSES" if passed else " — FAILS")
        )


if __name__ == "__main__":
    main()
