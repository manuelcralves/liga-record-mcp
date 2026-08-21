"""Measure how well zerozero's match rating predicts Record's editorial mark.

Record's mark is the largest single term in a Liga Record score and is
published nowhere — only the total appears. §10.1 and §10.3 let it be
recovered for a round that has been played, by computing the objective half
exactly and subtracting. That works for this season, and only this season.

To reconstruct a PAST season we need the mark for matches whose totals we
never saw. zerozero rates every match on its own scale, so the question is
whether one rating predicts the other well enough to stand in.

Both are Portuguese newsrooms watching the same games without coordinating, so
some agreement is expected and perfect agreement is not. A first pass over one
squad — 29 pairs — gave r = 0.79. This runs the same measurement over the
whole market, which is the sample worth trusting.

Two things are measured, and the first matters more. Because zerozero also
publishes the events of each match — goals, cards, minutes — the objective half
of a Liga Record score can be computed in full and the mark read off exactly,
with nothing guessed. Every reading that lands outside §10.1's 0-7 is an
assumption caught failing, and that share is the honest test of whether last
season can be rebuilt at all. The correlation is the second measurement, and
only means anything if the first one holds.

Nothing is reconstructed here. This only measures, and prints enough for the
relationship to be argued with rather than taken on faith.

    python scripts/measure_rating_bridge.py            # the whole market
    python scripts/measure_rating_bridge.py --limit 60 # a quicker read
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

from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import LigaRecordClient  # noqa: E402
from liga_record_mcp.source.zerozero import (  # noqa: E402
    ZeroZeroClient,
    ZeroZeroError,
    ZeroZeroPlayer,
)
from liga_record_mcp.stats import last_scored_round, read_rating  # noqa: E402

HISTORY_PATH = ROOT / "data" / "players.json"
OUT_PATH = ROOT / "data" / "rating-bridge.json"
CACHE_DIR = ROOT / "data" / "zerozero-cache"

#: zerozero's id for the 2026/27 season.
CURRENT_SEASON = 156


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pause", type=float, default=2.0)
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        raise SystemExit("no data/players.json — run build_player_history.py first")
    known = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["players"]

    market_client = LigaRecordClient(timeout=60.0)
    market = {p.id: p for pos in Position for p in market_client.search(pos)}
    fixtures = market_client.fixtures()
    latest = last_scored_round(fixtures)
    if latest is None:
        raise SystemExit("no round has been scored yet")

    results: dict[tuple[str, int], tuple[int, int]] = {}
    for fixture in fixtures:
        if fixture.played:
            results[(fixture.home, fixture.round_number)] = (
                fixture.home_goals,
                fixture.away_goals,
            )
            results[(fixture.away, fixture.round_number)] = (
                fixture.away_goals,
                fixture.home_goals,
            )

    # §10.3(k) names the round's highest scorer rather than a jury, so Player
    # of the Week is derivable. A tie leaves it undecidable, and guessing would
    # put five points on the wrong man, so a tied round simply goes without.
    def scorer_of(player, round_number: int) -> int | None:
        if round_number == latest:
            return player.points_round
        if round_number == 1 and latest == 2:
            return player.points_total - player.points_round
        return None

    player_of_the_week: dict[int, str] = {}
    for round_number in (1, latest):
        scores = {
            p.id: got
            for p in market.values()
            if (got := scorer_of(p, round_number)) is not None
        }
        if not scores:
            continue
        best = max(scores.values())
        leaders = [pid for pid, got in scores.items() if got == best]
        if len(leaders) == 1:
            player_of_the_week[round_number] = leaders[0]

    zero = ZeroZeroClient(CACHE_DIR, pause=args.pause)
    wanted = list(known.items())[: args.limit] if args.limit else list(known.items())
    print(f"{len(wanted)} players to read")

    pairs: list[dict] = []
    unexplained: list[dict] = []
    unread = 0
    foreign = 0
    for n, (player_id, row) in enumerate(wanted, 1):
        player = market.get(player_id)
        if player is None:
            continue
        # The slug is decorative — zerozero resolves a player by the numeric id
        # alone, verified against three different slugs.
        handle = ZeroZeroPlayer(
            zid=row["zerozero_id"],
            path=f"/jogador/p/{row['zerozero_id']}",
            name=row["name"],
            club=row["club"],
        )
        try:
            matches = zero.matches(handle, CURRENT_SEASON)
        except ZeroZeroError:
            unread += 1
            continue

        for match in matches:
            if match.round_number is None:
                continue
            key = (player.club, match.round_number)
            if key not in results:
                continue
            scored, conceded = results[key]
            # zerozero labels every country's first division "D1", so a player
            # who arrived from abroad brings rounds of a different league with
            # him — and they would be paired here with his new club's result.
            # Requiring the scoreline to agree makes that impossible.
            if (match.scored, match.conceded) != (scored, conceded):
                foreign += 1
                continue

            # Only two rounds are separable from a running total: the latest is
            # points_round, and the first is what is left of the total.
            points = scorer_of(player, match.round_number)
            if points is None:
                continue

            reading = read_rating(
                points,
                player.position,
                conceded=conceded,
                won=scored > conceded,
                used=match.used,
                goals=match.goals,
                minutes=match.minutes,
                red_card=match.red_card,
                second_yellow=match.yellow_cards >= 2,
                player_of_the_week=player_of_the_week.get(match.round_number)
                == player.id,
            )
            entry = {
                "player": row["name"],
                "position": player.position.value,
                "round": match.round_number,
                "points": points,
                "goals": match.goals,
                "minutes": match.minutes,
                "zerozero": match.rating,
                "record": reading["rating"],
                "residual": reading["residual"],
            }
            if not reading["certain"]:
                unexplained.append(entry)
                continue
            if match.rating is None or reading["rating"] is None:
                continue
            pairs.append(entry)
        if n % 40 == 0:
            print(f"  {n}/{len(wanted)} — {len(pairs)} pairs")

    if len(pairs) < 2:
        raise SystemExit(f"only {len(pairs)} pairs — nothing to measure")

    xs = [p["zerozero"] for p in pairs]
    ys = [float(p["record"]) for p in pairs]
    n = len(xs)
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(xs, ys)) / n
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    r = cov / (sx * sy) if sx and sy else 0.0
    slope = cov / statistics.pvariance(xs) if sx else 0.0
    intercept = mean_y - slope * mean_x

    read = len(pairs) + len(unexplained)
    print()
    print("READING THE MARK — the test that has to pass first")
    print(f"  player-rounds     {read}   ({unread} players unreadable)")
    print(f"  another league    {foreign}  (scoreline disagreed, so not this match)")
    print(
        f"  mark recovered    {len(pairs)}  "
        f"({100 * len(pairs) / read:.1f}%)"
    )
    print(f"  residual illegal  {len(unexplained)}")
    if unexplained:
        print()
        print("  the ones §10.3 could not account for:")
        for e in sorted(unexplained, key=lambda e: -abs(e["residual"] or 0))[:12]:
            print(
                f"    J{e['round']} {e['player'][:20]:<20} {e['position']:<3} "
                f"{e['points']:>4} pts  {e['goals']}g {e['minutes']:>3}min  "
                f"residual {e['residual']}"
            )

    print()
    print("THE BRIDGE — zerozero's rating against Record's mark")
    print(f"pairs        {n}   ({unread} players unreadable)")
    print(f"zerozero     mean {mean_x:.2f}  sd {sx:.2f}")
    print(f"Record       mean {mean_y:.2f}  sd {sy:.2f}")
    print(f"correlation  r = {r:.3f}   r2 = {r * r:.3f}")
    print(f"fit          Record = {intercept:.2f} + {slope:.2f} x zerozero")

    print()
    print("  zerozero -> the Record marks actually given")
    grouped = defaultdict(list)
    for p in pairs:
        grouped[round(p["zerozero"] * 2) / 2].append(p["record"])
    for band in sorted(grouped):
        marks = sorted(grouped[band])
        spread = f"{min(marks)}-{max(marks)}" if len(set(marks)) > 1 else str(marks[0])
        print(f"    {band:>4}  n={len(marks):>3}  mean {statistics.mean(marks):.2f}  range {spread}")

    OUT_PATH.write_text(
        json.dumps(
            {
                "pairs": n,
                "correlation": round(r, 4),
                "slope": round(slope, 4),
                "intercept": round(intercept, 4),
                "zerozero_mean": round(mean_x, 3),
                "record_mean": round(mean_y, 3),
                "recovered": len(pairs),
                "unexplained": len(unexplained),
                "samples": pairs,
                "unexplained_samples": unexplained,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
