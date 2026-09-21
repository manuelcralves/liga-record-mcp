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
    python scripts/backtest_transfers.py --lookahead 5 --paths 64
    python scripts/backtest_transfers.py --lookahead 5 --paths 64 --search improve --workers 16
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from liga_record_mcp.advice import valuation  # noqa: E402
from liga_record_mcp.backtest import (  # noqa: E402
    ABSENT,
    replay,
    replay_with_search,
    replay_with_transfers,
    rescale_for_fixture,
    settle_transfers,
    shrunk_projection,
    two_part_projection,
    fixture_table,
)
from liga_record_mcp.models import (  # noqa: E402
    BASE_BUDGET,
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    Position,
)
from liga_record_mcp.optimise import best_squad_under_budget  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, ManualSquadSource  # noqa: E402
from liga_record_mcp.source.last_season import archive_records  # noqa: E402
from replay_valuation import (  # noqa: E402
    SEASONS as REPLAYED,
    club_before,
    rows_by_round,
    season_so_far,
)

SQUAD_PATH = ROOT / "data" / "squad.yaml"

SEASON_PATH = ROOT / "data" / "last-season.json"

MATCHDAYS = list(range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1))
ALL_MATCHDAYS = list(range(1, LAST_MATCHDAY + 1))

#: The season each reconstruction may remember, as `replay_valuation` has it:
#: 2025/26 remembers 2024/25, and 2024/25 remembers nothing.
ARCHIVE_FOR = {season: archive for _, season, archive in REPLAYED}

#: What the page plays each candidate squad through: `SWAP_DRAWS` in
#: build_dashboard.py. The page's search at another count is another search.
PAGE_DRAWS = 1200


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
    # The opponent and the venue are on every match row already, and this
    # backtest has always thrown them away — so the transfer it measured was
    # chosen without knowing who anybody plays. `fixture_table` recovers them
    # from the same rows, which is what a horizon has to have.
    return history, minutes, cells, fixture_table([(loaded, 0)])


def projection_halves(estimator, history, minutes, cells, season_path):
    """{matchday: (playing, returns, cells)}, each from rounds strictly before it.

    `harness` is `two_part_projection`, the estimator the 8/9 measurement used.
    `valuation` is the one the page uses: `advice.valuation` with the recent
    rule, remembering the season before where one was reconstructed, and
    pooling each man with his club AS IT STOOD before the round. So the cells
    come back per matchday, and the fixture step has to be handed those.
    """
    if estimator == "harness":
        return {
            m: (*two_part_projection(history, minutes, cells, upto=m, parts=True), cells)
            for m in MATCHDAYS
        }
    loaded = json.loads(season_path.read_text(encoding="utf-8"))["players"]
    by_round = rows_by_round(loaded)
    # Everyone the replay carries, the held men with no row this season among
    # them: every round counts, so they read as never having played.
    season = {i: by_round.get(i, {}) for i in history}
    remembered = ARCHIVE_FOR.get(season_path.name)
    archive = archive_records(ROOT / "data", names=(remembered,)) if remembered else {}
    halves = {}
    for m in MATCHDAYS:
        clubs = {
            i: (club_before(rows, m) if rows else cells[i][0])
            for i, rows in season.items()
        }
        view = valuation(
            {
                i: SimpleNamespace(club=clubs[i], position=Position(cells[i][1]))
                for i in season
            },
            archive,
            season_so_far(season, first=1, upto=m, every_round=True),
        )
        halves[m] = (
            {i: view[i]["playing"] for i in season},
            {i: view[i]["returns"] for i in season},
            {i: (clubs[i], cells[i][1]) for i in season},
        )
    return halves


#: What every path needs, handed to each worker process once rather than once
#: a path.
_SHARED: dict = {}


def _share(shared):
    _SHARED.clear()
    _SHARED.update(shared)


def _play_the_arms(start):
    """One starting squad through every arm: {arm: (points, transfers made)}."""
    name, held = start
    shared = _SHARED
    common = dict(
        market=shared["market"], history=shared["history"], matchdays=MATCHDAYS,
        budget=BASE_BUDGET, forecasts=shared["per_round"], knows_availability=True,
    )
    if shared["search"] == "improve":
        played = {
            arm: replay_with_search(
                held, parts=shared["parts"], horizons=horizons,
                draws=shared["draws"], **common,
            )
            for arm, horizons in (("epoca", None), ("ahead", shared["ahead"]))
        }
    else:
        played = {
            arm: replay_with_transfers(
                held, cells=shared["cells"], horizons=horizons, **common
            )
            for arm, horizons in (
                ("semana", None),
                ("epoca", shared["season_value"]),
                ("ahead", shared["ahead"]),
            )
        }
    return name, {arm: (r["points"], len(r["transfers"])) for arm, r in played.items()}


def paired(label, diffs):
    """How often one arm beat the other, and by how much — never the mean alone."""
    wins = sum(1 for d in diffs if d > 0)
    error = statistics.stdev(diffs) / len(diffs) ** 0.5 if len(diffs) > 1 else 0.0
    return (
        f"  {label:<18}ganha em {wins:>3} de {len(diffs)}, media "
        f"{statistics.mean(diffs):+6.1f}, erro padrao {error:4.1f}, mediana "
        f"{statistics.median(diffs):+6.1f}"
    )


def looking_ahead(
    history, minutes, cells, table, market, rows, mine, *,
    horizon, paths, seed, estimator, search, workers, draws, season_path,
):
    """Does pricing a transfer over the next few rounds beat what the page does?

    THREE WAYS OF PRICING THE SAME TRANSFER, paired over the same starting
    squads. Every arm picks the eleven each round on that round's fixture, as
    the page does, so the arms differ in one thing only:

        semana   this round's fixture, times the rounds left
        epoca    the season value with no fixture at all, times the rounds
                 left — THE PAGE'S RULE since 21/08/2026 ("Season values, not
                 this week's", in build_dashboard.model_sheet)
        ahead    the mean over the next `horizon` rounds, each against its own
                 opponent, times the rounds left

    `epoca` WAS MISSING UNTIL 21/09/2026, and it is the arm that matters. The
    8/9 version compared `ahead` with `semana` alone and called `semana` "what
    the model does today", which it was not, then or since. Its +16.7 a season
    did not survive either: run again on 21/09, unchanged, against that day's
    market, 2025/26 at K=5 gave 6 paths in 16 and -15.3 where 8/9 had 12 and
    +14.2. Its p of 0.016 had counted six configurations as independent that
    shared their paths.

    Measured on 21/09/2026 through `best_transfer`, 64 paths a season, on the
    model's own estimator:

                                  2025/26       2024/25        both
        ahead over epoca   K=3   +2.7 ± 4.7    +5.1 ± 4.1    +3.9 ± 3.1
                           K=5   +8.2 ± 4.8   +12.1 ± 4.3   +10.1 ± 3.2
                           K=8   +8.3 ± 4.6   +13.3 ± 4.3   +10.8 ± 3.2
        epoca over semana       +30.6 ± 5.9    +0.9 ± 4.4   +15.8 ± 3.7

    Positive in every cell, the harness's included, and small: at K=5 it wins
    71 paths of 128, with medians of +2.6 and +10.6 — a tail of paths that gain
    a lot. The last line is the first measurement of the page's own rule, and
    it holds; on the harness it flips (-8.9 ± 3.8), which is why the line
    quoted is the model's.

    `search="improve"` asks it of the search the page runs, `improve_squad`,
    and plays only `epoca` and `ahead`: at the page's draws the horizon costs
    five times the search, and `semana` answers nothing the page asks.

    A SEASON IS ONE PATH and this script's own docstring says so: about
    twenty-five decisions, swinging a hundred points with no pattern, and
    anything read off a single run is noise wearing a number. So the answer
    here is a PAIRED DISTRIBUTION over many starting squads — same season, same
    projections, same draws, one squad at a time — and the thing reported is
    how often one arm beats another, not what either scored.
    """
    # The expensive half once per matchday; the fixture step is a dictionary
    # walk and can be repeated for every round in the horizon.
    halves = projection_halves(estimator, history, minutes, cells, season_path)

    def blended(playing, returns):
        return {
            i: playing[i] * rate + (1 - playing[i]) * ABSENT
            for i, rate in returns.items()
        }

    # What each man returns when he plays in every round ahead, from data
    # before the decision: `upto` fixes the strengths and only the calendar
    # moves.
    ahead_of = {
        m: list(range(m, min(m + horizon, MATCHDAYS[-1] + 1))) for m in MATCHDAYS
    }
    moved = {
        m: {
            f: rescale_for_fixture(
                halves[m][1], halves[m][2], table, upto=m, for_matchday=f
            )
            for f in ahead_of[m]
        }
        for m in MATCHDAYS
    }
    per_round = {m: blended(halves[m][0], moved[m][m]) for m in MATCHDAYS}

    shared = dict(
        market=market, history=history, cells=cells, per_round=per_round,
        search=search, draws=draws,
    )
    if search == "improve":
        arms = ("epoca", "ahead")
        shared["parts"] = {m: (halves[m][0], halves[m][1]) for m in MATCHDAYS}
        shared["ahead"] = {m: [moved[m][f] for f in ahead_of[m]] for m in MATCHDAYS}
    else:
        arms = ("semana", "epoca", "ahead")
        # The season value as a horizon of one round: priced with no fixture,
        # while the eleven is still picked on this week's.
        shared["season_value"] = {
            m: {m: blended(halves[m][0], halves[m][1])} for m in MATCHDAYS
        }
        shared["ahead"] = {
            m: {f: blended(halves[m][0], moved[m][f]) for f in ahead_of[m]}
            for m in MATCHDAYS
        }

    # The starting squads. The manager's own and the model's opening pick are the
    # two that matter, and random legal twenty-threes supply the spread — one
    # pair of seasons and two squads could not tell a real edge from a lucky
    # one.
    opening = per_round[MATCHDAYS[0]]
    model = best_squad_under_budget(
        rows, {i: v * len(MATCHDAYS) for i, v in opening.items()}
    )
    starts = [("o teu 23", mine)]
    if model is not None:
        starts.append(("o do modelo", model["players"]))
    rng = random.Random(seed)
    while len(starts) < paths:
        noise = {i: rng.random() for i in market}
        drawn = best_squad_under_budget(rows, noise)
        if drawn is not None:
            starts.append((f"aleatorio {len(starts) - 1}", drawn["players"]))

    print()
    print(
        f"  LOOKAHEAD DE {horizon} JORNADAS, {len(starts)} caminhos emparelhados, "
        f"estimador {estimator}, busca {search}"
    )
    print(
        f"  {'plantel de partida':<22}"
        + "".join(f"{arm:>9}" for arm in arms)
        + "   trocas"
    )
    results = []

    def show(name, row):
        results.append(row)
        print(
            f"  {name:<22}"
            + "".join(f"{row[arm][0]:>9.0f}" for arm in arms)
            + "   "
            + "/".join(str(row[arm][1]) for arm in arms),
            flush=True,
        )

    if workers > 1:
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_share, initargs=(shared,)
        ) as pool:
            for name, row in pool.map(_play_the_arms, starts):
                show(name, row)
    else:
        _share(shared)
        for start in starts:
            show(*_play_the_arms(start))

    diffs = {
        "ahead - epoca": [r["ahead"][0] - r["epoca"][0] for r in results],
    }
    if "semana" in arms:
        diffs["ahead - semana"] = [r["ahead"][0] - r["semana"][0] for r in results]
        diffs["epoca - semana"] = [r["epoca"][0] - r["semana"][0] for r in results]
    print()
    for label, values in diffs.items():
        print(paired(label, values))
    return diffs


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
        "--lookahead",
        type=int,
        metavar="K",
        help="price each transfer across the next K rounds of real calendar "
        "instead of the one being decided, and report the paired distribution",
    )
    parser.add_argument(
        "--paths",
        type=int,
        default=12,
        help="how many starting squads the lookahead comparison is paired over",
    )
    parser.add_argument(
        "--estimator",
        choices=("valuation", "harness"),
        default="valuation",
        help="what the lookahead prices with: the model's valuation, or the "
        "two-part projection the 8/9 measurement used",
    )
    parser.add_argument(
        "--search",
        choices=("best", "improve"),
        default="best",
        help="which search picks each transfer in the lookahead comparison: "
        "best_transfer, or improve_squad as the page runs it",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="processes to spread the lookahead's paths over",
    )
    parser.add_argument(
        "--draws",
        type=int,
        default=PAGE_DRAWS,
        help="draws per candidate squad under --search improve",
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
    history, minutes, cells, table = load(market, args.season)
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

    if args.lookahead:
        looking_ahead(
            history, minutes, cells, table, market, rows, mine,
            horizon=args.lookahead, paths=args.paths, seed=args.seed,
            estimator=args.estimator, search=args.search, workers=args.workers,
            draws=args.draws, season_path=args.season or SEASON_PATH,
        )
        return

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

    # EACH SIGNAL BUYS ITS OWN SQUAD. This used to buy one squad with `form
    # only` and then measure every signal's WEEKLY decisions on it — so the
    # column headed `plays x returns` described a season begun by a signal it
    # does not use, and the squad choice of the model that actually gives the
    # advice was never tested at all. The natural assumption was that the test
    # started from five matchdays and ran the whole thing forward, which is
    # what it does for transfers and was not what it did for the twenty-three.
    #
    # Buying with the same signal that then manages the season is the only
    # comparison that isolates the signal rather than confounding it with
    # whichever one happened to open.
    squads = {}
    for name, per_round in views.items():
        opening = per_round[MATCHDAYS[0]]
        bought = best_squad_under_budget(
            rows, {i: v * len(MATCHDAYS) for i, v in opening.items()}
        )
        if bought is None:
            raise SystemExit(f"no legal squad fits the budget for {name}")
        squads[name] = bought["players"]

    for label, squad in (("cada sinal compra o SEU plantel", None), ("YOUR 23", mine)):
        print()
        print(f"  {label}")
        print(
            f"  {'signal':<28}{'XI only':>10}{'+injury news':>14}"
            + "".join(f"{'trf ' + format(t, 'g'):>10}" for t in args.thresholds)
        )
        for name, per_round in views.items():
            held = squad if squad is not None else squads[name]
            blind = replay(held, market, history, MATCHDAYS, forecasts=per_round)
            informed = replay(
                held,
                market,
                history,
                MATCHDAYS,
                forecasts=per_round,
                knows_availability=True,
            )
            cells_out = [f"{blind['points']:>10.0f}", f"{informed['points']:>14.0f}"]
            for threshold in args.thresholds:
                played = replay_with_transfers(
                    held,
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
