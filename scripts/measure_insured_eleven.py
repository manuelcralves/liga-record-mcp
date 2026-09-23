"""Does naming the eleven with the bench priced in beat naming it on the numbers?

A player's estimate charges him §10.3(i)'s -1 for the weeks he does not play.
For a STARTER that is false: §11 sends on the substitute of his position and
the manager collects that man's points. So an uncertain starter with cover
behind him is worth more than his own number, and a certain one with no cover
is worth less. `optimise.best_eleven` can price that, given each man's chance
of playing. This asks whether it pays.

HOW. The two reconstructed seasons, played round by round with a FIXED squad —
no transfers, so nothing but the eleven moves. Each round the sheet is named
twice from the same forecast, by the plain rule and by the priced one, and both
are scored on what actually happened, §11 substitutions included. Paired over
many starting squads, because one squad's season is luck.

TWO WORLDS OF KNOWLEDGE, and the question lives in the first:

    nobody knows     absences are a surprise, which is what rotation is. The
                     chance of playing is all the model has, and the bench is
                     the only protection. THE DECISION IS READ HERE.
    knows the out    the manager knows exactly who will not play, so he never
                     starts them and §11 never fires. The priced rule must not
                     hurt here, and cannot help much.

The live model sits between them: the bulletin names the injured, and nobody
names who will be rested.

    python scripts/measure_insured_eleven.py
    python scripts/measure_insured_eleven.py --paths 32 --season data/season-2024-25.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from backtest_transfers import (  # noqa: E402
    MATCHDAYS,
    SEASON_PATH,
    blended,
    prepare,
    projection_halves,
    starting_squads,
)
from liga_record_mcp.backtest import play_round  # noqa: E402
from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import LigaRecordClient  # noqa: E402

#: How the manager's knowledge of absences is set, and which one decides.
WORLDS = (("ninguem sabe", False), ("sabe quem falta", True))


def season_for(
    squad_ids, market, history, per_round, chances, *, knows: bool, insured: bool
) -> tuple[float, int]:
    """A whole season for one squad under one rule: points, and substitutions made."""
    total, subs = 0.0, 0
    for matchday in MATCHDAYS:
        played = play_round(
            squad_ids,
            market,
            history,
            matchday,
            forecast=per_round[matchday],
            knows_availability=knows,
            chances=chances[matchday] if insured else None,
        )
        total += played["points"]
        subs += played["substitutions"]
    return total, subs


def paired(gains: list[float]) -> tuple[float, float]:
    """The mean gain and its standard error, over the paths."""
    if len(gains) < 2:
        return (gains[0] if gains else 0.0), 0.0
    return mean(gains), stdev(gains) / (len(gains) ** 0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--paths", type=int, default=24, help="starting squads")
    parser.add_argument("--seed", type=int, default=20260923)
    parser.add_argument(
        "--season",
        type=Path,
        help="a reconstructed season other than the default, so a finding can "
        "be checked against a year it was not tuned on",
    )
    args = parser.parse_args()

    client = LigaRecordClient(timeout=60.0)
    live = {
        p.id: p.as_player() for position in Position for p in client.search(position)
    }
    history, minutes, cells, _table, market, rows, mine = prepare(live, args.season)
    print(f"{len(market)} players; matchdays {MATCHDAYS[0]}-{MATCHDAYS[-1]}")

    halves = projection_halves(
        "valuation", history, minutes, cells, args.season or SEASON_PATH
    )
    chances = {m: halves[m][0] for m in MATCHDAYS}
    per_round = {m: blended(halves[m][0], halves[m][1]) for m in MATCHDAYS}
    squads = starting_squads(per_round, rows, market, mine, args.paths, args.seed)

    for world, knows in WORLDS:
        print(f"\n  {world}")
        print(f"    {'plantel de partida':<26}{'onze':>8}{'seguro':>8}{'dif':>7}   trocas")
        gains = []
        for label, squad_ids in squads:
            plain, plain_subs = season_for(
                squad_ids, market, history, per_round, chances,
                knows=knows, insured=False,
            )
            priced, priced_subs = season_for(
                squad_ids, market, history, per_round, chances,
                knows=knows, insured=True,
            )
            gains.append(priced - plain)
            print(
                f"    {label:<26}{plain:>8.0f}{priced:>8.0f}{priced - plain:>+7.0f}"
                f"   {plain_subs}/{priced_subs}"
            )
        gain, error = paired(gains)
        better = sum(1 for g in gains if g > 0)
        print(
            f"    o seguro vale {gain:+.1f} +- {error:.1f} pontos por epoca, "
            f"melhor em {better} de {len(gains)}"
        )


if __name__ == "__main__":
    main()
