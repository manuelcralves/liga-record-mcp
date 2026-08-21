"""Does the weekly transfer advice actually work?

The one question the project exists to answer, and the one it had never asked.
Everything measured so far — the reconstruction, the shrinkage weight, the
season benchmarks — tests how well the model DESCRIBES football. This tests
whether acting on it is better than not.

THE EXPERIMENT. Stand before matchday 6 of last season with a squad chosen from
the five matchdays already played, and nothing else. Then, before every round:

    1. rebuild the projection from matchdays strictly earlier
    2. take the one transfer §6.8 allows, if the model wants one
    3. pick the XI from the same projection, and play

Nothing the manager could not have known reaches any decision. At the end,
compare against a squad that never transferred, against random legal transfers,
and against transfers made knowing the results — which bounds what the channel
is worth to anyone.

WHAT IT IS AND IS NOT GOOD FOR. It is good at finding bugs. Judging a transfer
by the two players' own rates rather than by what the eleven returns, and a
projection flickering between two near-identical defenders until four transfers
had been spent swapping them back and forth — both surfaced here and nowhere
else, because both look fine until a season is actually played out.

It is NOT good at ranking one estimate against another. A season is one path
with about twenty-five decisions on it, and run across two seasons, two squads
and several thresholds the ordering of the signals changes every time, swinging
by a hundred points with no pattern. Anything read off it about which signal is
better is noise wearing a number.

For that question use `measure_projection_accuracy.py`, which asks it directly
over ten thousand predictions instead of one season's outcome, and which gives
the same answer twice.

WHAT IT DOES NOT MODEL. Prices are frozen at today's quotes: last season's are
gone, and §12.3's price movement cannot be replayed without them. So this
measures whether the model picks better PLAYERS, not whether it plays the
market. §6.9's February window is left out too — six swaps in a month is a
different problem and blurring it into "one a round" would answer neither.

    python scripts/backtest_transfers.py
    python scripts/backtest_transfers.py --thresholds 0 5 10 20
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.backtest import (  # noqa: E402
    ABSENT,
    replay,
    replay_with_transfers,
    settle_transfers,
    shrunk_projection,
    two_part_projection,
)
from liga_record_mcp.models import (  # noqa: E402
    BASE_BUDGET,
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    Position,
)
from liga_record_mcp.optimise import best_squad_under_budget  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, ManualSquadSource  # noqa: E402

SQUAD_PATH = ROOT / "data" / "squad.yaml"

SEASON_PATH = ROOT / "data" / "last-season.json"

MATCHDAYS = list(range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1))
ALL_MATCHDAYS = list(range(1, LAST_MATCHDAY + 1))


def load(market, season_path=None):
    """Per-matchday points, and the club each player is pooled with."""
    path = season_path or SEASON_PATH
    if not path.exists():
        raise SystemExit(f"no {path} — run scripts/build_last_season.py first")
    loaded = json.loads(path.read_text(encoding="utf-8"))["players"]

    history, minutes, cells = {}, {}, {}
    for player_id, player in loaded.items():
        if player_id not in market or not player["matches"]:
            continue
        history[player_id] = {m: ABSENT for m in ALL_MATCHDAYS}
        minutes[player_id] = {m: 0 for m in ALL_MATCHDAYS}
        clubs = defaultdict(int)
        for match in player["matches"]:
            history[player_id][int(match["round"])] = float(match["points"])
            minutes[player_id][int(match["round"])] = int(match.get("minutes") or 0)
            if match.get("club"):
                clubs[match["club"]] += 1
        cells[player_id] = (
            max(clubs, key=clubs.get) if clubs else "",
            market[player_id].position.value,
        )
    return history, minutes, cells


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thresholds", type=float, nargs="*", default=[0, 10, 25])
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument(
        "--season",
        type=Path,
        help="a reconstructed season other than the default, so a finding can "
        "be checked against a year it was not tuned on",
    )
    parser.add_argument(
        "--story",
        action="store_true",
        help="one season told as decisions rather than as a benchmark table",
    )
    args = parser.parse_args()

    client = LigaRecordClient(timeout=60.0)
    market = {
        p.id: p.as_player() for position in Position for p in client.search(position)
    }
    history, minutes, cells = load(market, args.season)
    market = {i: market[i] for i in history}
    rows = [
        {"id": p.id, "position": p.position, "value": p.value} for p in market.values()
    ]

    mine_players = ManualSquadSource(SQUAD_PATH).load().squad.players
    mine = [p.id for p in mine_players]
    for player in mine_players:
        if player.id in market:
            continue
        # Promoted from the second division, or abroad. §10.3(i) charged a
        # manager -1 for every round he owned them.
        market[player.id] = player
        history[player.id] = {m: ABSENT for m in ALL_MATCHDAYS}
        minutes[player.id] = {m: 0 for m in ALL_MATCHDAYS}
        cells[player.id] = (player.club, player.position.value)

    print(f"{len(market)} players; matchdays {MATCHDAYS[0]}-{MATCHDAYS[-1]}")

    # Three ways of estimating what a player is about to be worth. The first is
    # what the projection has always done; the others split it in two.
    signals = {
        "form only": lambda upto: shrunk_projection(history, cells, upto=upto),
        "plays x returns": lambda upto: two_part_projection(
            history, minutes, cells, upto=upto
        ),
        "plays x returns, minutes": lambda upto: two_part_projection(
            history, minutes, cells, upto=upto, minutes_weighted=True
        ),
    }
    views = {
        name: {m: build(m) for m in MATCHDAYS} for name, build in signals.items()
    }

    if args.story:
        tell_the_season(market, history, minutes, cells, rows, views)
        return

    opening_view = views["form only"][MATCHDAYS[0]]
    bought = best_squad_under_budget(
        rows, {i: v * len(MATCHDAYS) for i, v in opening_view.items()}
    )
    if bought is None:
        raise SystemExit("no legal squad fits the budget")

    for label, squad in (("a squad the model bought", bought["players"]), ("YOUR 23", mine)):
        print()
        print(f"  {label}")
        print(
            f"  {'signal':<28}{'XI only':>10}{'+injury news':>14}"
            + "".join(f"{'trf ' + format(t, 'g'):>10}" for t in args.thresholds)
        )
        for name, per_round in views.items():
            blind = replay(squad, market, history, MATCHDAYS, forecasts=per_round)
            informed = replay(
                squad,
                market,
                history,
                MATCHDAYS,
                forecasts=per_round,
                knows_availability=True,
            )
            cells_out = [f"{blind['points']:>10.0f}", f"{informed['points']:>14.0f}"]
            for threshold in args.thresholds:
                played = replay_with_transfers(
                    squad,
                    market,
                    history,
                    MATCHDAYS,
                    cells=cells,
                    budget=BASE_BUDGET,
                    min_gain=threshold,
                    forecasts=per_round,
                    knows_availability=True,
                )
                cells_out.append(f"{played['points']:>10.0f}")
            print(f"  {name:<28}" + "".join(cells_out))

    perfect = {
        m: {
            i: statistics.mean([history[i][later] for later in MATCHDAYS if later >= m])
            for i in market
        }
        for m in MATCHDAYS
    }
    ceiling = replay_with_transfers(
        mine, market, history, MATCHDAYS, cells=cells, budget=BASE_BUDGET,
        forecasts=perfect, knows_availability=True,
    )
    print()
    print(
        f"  knowing the results, from YOUR 23: {ceiling['points']:.0f} "
        f"({len(ceiling['transfers'])} transfers) — the ceiling for the channel"
    )

    # What the rotation signal actually sees, so the numbers above can be
    # argued with rather than believed.
    late = MATCHDAYS[len(MATCHDAYS) // 2]
    form = views["form only"][late]
    split = views["plays x returns"][late]
    moved = sorted(market, key=lambda i: split[i] - form[i])
    print()
    print(f"  what the split says that form alone does not, at matchday {late}:")
    print(f"    {'player':<24}{'form':>7}{'split':>8}{'played 5':>10}")
    for player_id in moved[:4] + moved[-4:]:
        recent = sum(
            1 for m in range(late - 5, late) if minutes[player_id].get(m, 0) > 0
        )
        print(
            f"    {market[player_id].name[:23]:<24}{form[player_id]:>7.2f}"
            f"{split[player_id]:>8.2f}{recent:>7}/5"
        )


def tell_the_season(market, history, minutes, cells, rows, views):
    """One season, told as the decisions a manager would have had to take.

    A benchmark table says a strategy was worth so many points. It does not say
    what it would have had you do on a Tuesday in October, which is the only
    form in which advice is any use.
    """
    # The plain split, not the minutes-weighted one: it measures better on both
    # reconstructed seasons, over far more predictions than any squad backtest.
    signal = views["plays x returns"]
    opening_view = signal[MATCHDAYS[0]]
    bought = best_squad_under_budget(
        rows, {i: v * len(MATCHDAYS) for i, v in opening_view.items()}
    )
    if bought is None:
        raise SystemExit("no legal squad fits the budget")
    squad = bought["players"]

    print("THE SQUAD IT WOULD HAVE BOUGHT")
    print("  chosen knowing five matchdays and nothing after them")
    print(f"  {bought['cost']:,} of {BASE_BUDGET:,}")
    print()
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    for player_id in sorted(
        squad, key=lambda i: (order[market[i].position], -market[i].value)
    ):
        player = market[player_id]
        print(
            f"    {player.position.value:<4}{player.name[:22]:<24}"
            f"{player.club[:14]:<15}{player.value / 1e6:>5.2f}M"
            f"{opening_view[player_id]:>7.2f}"
        )

    frozen = replay(
        squad, market, history, MATCHDAYS, forecasts=signal, knows_availability=True
    )
    played = replay_with_transfers(
        squad,
        market,
        history,
        MATCHDAYS,
        cells=cells,
        budget=BASE_BUDGET,
        min_gain=0.0,
        forecasts=signal,
        knows_availability=True,
    )
    settled = settle_transfers(played["transfers"], history, MATCHDAYS)

    print()
    print("THE TRANSFERS IT WOULD HAVE MADE")
    print(f"    {'md':>3}  {'out':<22}{'in':<22}{'gained':>8}")
    for transfer in settled:
        print(
            f"    {transfer['matchday']:>3}  {transfer['out'][:21]:<22}"
            f"{transfer['in'][:21]:<22}{transfer['actual_gain']:>+8.0f}"
        )
    if not settled:
        print("    none — it never saw an upgrade worth a transfer")

    hits = sum(1 for t in settled if t["right"])
    print()
    print(f"    {hits} of {len(settled)} gained points.")

    print()
    print("HOW IT WOULD HAVE DONE")
    print(
        f"    with those transfers      {played['points']:>7.0f}"
        f"   ({played['per_round']:.1f} a round)"
    )
    print(f"    never transferring        {frozen['points']:>7.0f}")
    print(
        f"    the transfers were worth  "
        f"{played['points'] - frozen['points']:>+7.0f}"
    )
    print(f"    best round {played['best']:.0f}, worst round {played['worst']:.0f}")


if __name__ == "__main__":
    main()
