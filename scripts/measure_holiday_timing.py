"""Can the model tell which weeks a §6.17 holiday should go on?

A holiday pays half the round winner, whatever the team does, so it is worth
taking in a week the team is expected to fall below that. Whether the model
can find those weeks is a question about its forecast of the TEAM: does a week
it expects to be poor turn out poor?

That can be measured without knowing any winner. Against spending the three
holidays at random, a rule gains exactly what its chosen weeks fall short of
the squad's average week — the payout is the same either way and cancels. So
each replayed season reports how far below its own average each path's chosen
weeks actually scored.

THE REPLAY. The paths of `backtest_transfers.py` (the manager's 23, the model's
opening pick and random legal squads), trading one player a round on the
five-round lookahead, 2024/25 and 2025/26, rounds 6 to 31 (§6.17 closes the
last three). Before each round, from data before it:

    expected   what the squad's sheet is expected to make this round
               (`squad_value`, absences and §11 played out, on this round's
               fixture-adjusted values)
    typical    the same over the next five rounds, averaged

and the rule of `liga_record_mcp.holiday` takes a holiday when the payout less
the expected score beats the value of waiting. The payout is set at the gap
this season has shown: 42.5 against a team expected near 45, so typical - 2.5.

TWO READINGS OF WHAT IS KNOWN before kickoff:

    blind      only the chance of playing each man carries (the bar is here)
    informed   also who did not play that round, as the transfer backtest
               assumes — an upper bound, since it knows rotation as well as
               injury; the page's bulletin knows injuries and bans only

The coach is left out: these seasons do not tie coaches to the clubs of the
reconstruction, and a coach moves a round by a point or two either way.

    python scripts/measure_holiday_timing.py
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from backtest_transfers import (  # noqa: E402
    MATCHDAYS,
    SEASON_PATH,
    blended,
    prepare,
    priced_views,
    projection_halves,
    starting_squads,
)
from liga_record_mcp.backtest import appeared, replay_with_transfers  # noqa: E402
from liga_record_mcp.holiday import LAST_HOLIDAY_ROUND, holiday_bar  # noqa: E402
from liga_record_mcp.models import BASE_BUDGET, HOLIDAY_ROUNDS, Position  # noqa: E402
from liga_record_mcp.optimise import squad_value  # noqa: E402
from liga_record_mcp.source import LigaRecordClient  # noqa: E402

SEASONS = (
    ("2025/26", SEASON_PATH),
    ("2024/25", ROOT / "data" / "season-2024-25.json"),
)
#: The payout less the team's typical expected score, as this season has run:
#: winners of 78 and 91 pay 39 and 46, against a squad expected near 45.
GAP = -2.5
HORIZON = 5
DRAWS = 400
ELIGIBLE = [m for m in MATCHDAYS if m <= LAST_HOLIDAY_ROUND]


def squads_by_round(opening, transfers):
    """The twenty-three held in each round, replayed from the transfers made."""
    squad = list(opening)
    moves = {t["matchday"]: t for t in transfers}
    held = {}
    for number in MATCHDAYS:
        move = moves.get(number)
        if move:
            squad[squad.index(move["out_id"])] = move["in_id"]
        held[number] = list(squad)
    return held


def path_weeks(held, market, history, halves, moved, ahead_of, actual):
    """Each eligible round's expected (blind and informed), typical and actual."""
    weeks = []
    for number in ELIGIBLE:
        squad = held[number]
        playing = halves[number][0]
        views = [moved[number][f] for f in ahead_of[number]]
        blind = [
            squad_value(squad, market, view, playing, draws=DRAWS) for view in views
        ]
        known = {
            i: (playing.get(i, 0.0) if appeared(history, i, number) else 0.0)
            for i in squad
        }
        informed = squad_value(squad, market, views[0], {**playing, **known}, draws=DRAWS)
        weeks.append(
            {
                "round": number,
                "blind": blind[0],
                "informed": informed,
                "typical": statistics.fmean(blind),
                "actual": actual[number],
            }
        )
    return weeks


def calibration(paths, key):
    """Bias, and the slope of actual on expected within each path's weeks."""
    bias = statistics.fmean(w["actual"] - w[key] for weeks in paths for w in weeks)
    sxy = sxx = 0.0
    for weeks in paths:
        me = statistics.fmean(w[key] for w in weeks)
        ma = statistics.fmean(w["actual"] for w in weeks)
        sxy += sum((w[key] - me) * (w["actual"] - ma) for w in weeks)
        sxx += sum((w[key] - me) ** 2 for w in weeks)
    spread = statistics.fmean(statistics.pstdev(w[key] for w in weeks) for weeks in paths)
    return bias, (sxy / sxx if sxx else 0.0), spread


def play_the_rule(weeks, key, spread):
    """The rule over one path: the weeks chosen, their shortfall, the net."""
    left = HOLIDAY_ROUNDS
    average = statistics.fmean(w["actual"] for w in weeks)
    chosen = []
    for week in weeks:
        if left <= 0:
            break
        payout = week["typical"] + GAP
        gain = payout - week[key]
        rounds = LAST_HOLIDAY_ROUND - week["round"] + 1
        if gain > holiday_bar(left, rounds, GAP, spread):
            chosen.append((week, payout))
            left -= 1
    shortfall = sum(average - w["actual"] for w, _ in chosen)
    net = sum(payout - w["actual"] for w, payout in chosen)
    return len(chosen), shortfall, net


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paths", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260820)
    args = parser.parse_args()

    client = LigaRecordClient(timeout=120.0)
    live = {p.id: p.as_player() for position in Position for p in client.search(position)}
    verdicts = []
    for label, season_path in SEASONS:
        history, minutes, cells, table, market, rows, mine = prepare(live, season_path)
        halves = projection_halves("valuation", history, minutes, cells, season_path)
        per_round, moved, ahead_of = priced_views(halves, table, HORIZON)
        horizons = {
            m: {f: blended(halves[m][0], moved[m][f]) for f in ahead_of[m]}
            for m in MATCHDAYS
        }
        paths = []
        for name, opening in starting_squads(per_round, rows, market, mine, args.paths, args.seed):
            played = replay_with_transfers(
                opening, market, history, MATCHDAYS, cells=cells, budget=BASE_BUDGET,
                forecasts=per_round, horizons=horizons, knows_availability=True,
            )
            actual = dict(zip(MATCHDAYS, played["rounds"]))
            held = squads_by_round(opening, played["transfers"])
            paths.append(path_weeks(held, market, history, halves, moved, ahead_of, actual))

        print()
        print(f"{label}: {len(paths)} caminhos, jornadas {ELIGIBLE[0]}-{ELIGIBLE[-1]}")
        for key, reading in (("blind", "sem saber quem falta"), ("informed", "sabendo quem falta")):
            bias, slope, spread = calibration(paths, key)
            played_out = [play_the_rule(weeks, key, spread) for weeks in paths]
            used = statistics.fmean(p[0] for p in played_out)
            shortfalls = [p[1] for p in played_out]
            nets = [p[2] for p in played_out]
            error = statistics.stdev(shortfalls) / len(shortfalls) ** 0.5
            print(f"  {reading}:")
            print(
                f"    real menos esperado {bias:+.2f} por jornada; declive {slope:.2f}; "
                f"o esperado varia {spread:.2f} de semana para semana"
            )
            print(
                f"    a regra usa {used:.1f} ferias por epoca; as semanas escolhidas "
                f"ficam {statistics.fmean(shortfalls):+.1f} abaixo da media do "
                f"caminho (erro padrao {error:.1f}); com o pagamento desta epoca, "
                f"{statistics.fmean(nets):+.1f} por epoca"
            )
            if key == "blind":
                verdicts.append(statistics.fmean(shortfalls))

    print()
    passed = all(v > 0 for v in verdicts)
    print(
        "BARRA (sem saber quem falta, as semanas escolhidas abaixo da media nas "
        "duas epocas): " + ", ".join(f"{v:+.1f}" for v in verdicts)
        + (" — PASSA" if passed else " — FALHA")
    )


if __name__ == "__main__":
    main()
