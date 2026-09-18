"""Propose the twenty-three to hold when the squad locks.

Transfers are unlimited until matchday 6 and one a round afterwards, so this is
the last moment the whole team can be chosen at once. Everything else the
project does is worth less than getting this right: measured over a season, the
gap between a good squad and a careless one is several hundred points, and the
gap between good weekly transfers and no transfers at all is a few dozen.

WHAT IT KNOWS. Two reconstructed seasons of what each player returns on the
days he plays, this season's rounds from the weekly score emails, and who
actually took the field — from §10.3(i)'s -1, the only signal Liga Record gives
about appearances.

WHAT IT DOES WITH IT. Every player is valued by `advice.valuation`, the function
the pages and the ledger use, in two halves:

    expected = P(plays) x (what he returns when he plays) + P(not) x -1

This file kept its own copy of that arithmetic until 15/09/2026. The copy still
weighed every round of the season alike after the replay had measured that rule
0.06 to 0.09 of correlation worse than one leaning on the last two rounds, which
is the argument for one copy, made for the second time in a week.

Then the squad is chosen under §6.6's quota and §6.4's budget, and repaired:
the exact optimiser maximises the sum of twenty-three, which is not the game,
so a search over the real objective — what the eleven returns once absences and
§11 substitutions are played out — finishes the job.

    python scripts/propose_squad.py
    python scripts/propose_squad.py --draws 800   # a slower, steadier search
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import (  # noqa: E402
    BASE_BUDGET,
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    SQUAD_SIZE,
    Position,
)
from liga_record_mcp.advice import transfer_candidates, valuation  # noqa: E402
from liga_record_mcp.rules import transfers_allowed  # noqa: E402
from liga_record_mcp.optimise import (  # noqa: E402
    best_eleven,
    left_the_league,
    best_squad_under_budget,
    improve_squad,
    squad_value,
)
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    ManualSquadSource,
    load_official_rounds,
)
from liga_record_mcp.source.appearances import current_records  # noqa: E402
from liga_record_mcp.source.last_season import archive_records  # noqa: E402
from liga_record_mcp.source.bulletin import bulletin_out, load_bulletin  # noqa: E402

SQUAD_PATH = ROOT / "data" / "squad.yaml"
#: The Premium bulletin and suspensions board, copied each week; gitignored.
BULLETIN_DIR = ROOT / "data" / "boletim"

#: Rounds still to be played once the squad locks. Used only to turn a rate
#: into a season, never to choose anything.
ROUNDS_LEFT = LAST_MATCHDAY - FIRST_SCORING_MATCHDAY + 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=400)
    parser.add_argument("--budget", type=int, default=BASE_BUDGET)
    parser.add_argument(
        "--moves",
        type=int,
        default=None,
        help="how many rungs of the ladder to work out "
             "(default: whatever the window allows)",
    )
    parser.add_argument(
        "--jornada",
        type=int,
        default=None,
        help="which matchday to price the window for (default: the squad file)",
    )
    args = parser.parse_args()

    client = LigaRecordClient(timeout=60.0)
    # The market view is kept alongside the squad view: `as_player` drops how
    # many teams hold a player, and that is the whole basis of a differential.
    quoted = {
        p.id: p for position in Position for p in client.search(position)
    }
    market = {i: p.as_player() for i, p in quoted.items()}
    # EVERY PLAYER VALUED BY THE ONE FUNCTION the pages and the ledger use, on
    # this season as the one reading of the weekly emails gives it. This file
    # kept its own copy of both until 15/09/2026. The reading went wrong on the
    # morning the site reset its totals. The valuation kept weighing every round
    # alike after the replay had measured that rule worse than one leaning on
    # the last two, and kept a fallback that shrank a man toward a position
    # average he was still part of.
    season = current_records(
        market,
        load_official_rounds(
            ROOT / "data" / "pontuacoes", first_round=FIRST_SCORING_MATCHDAY
        ),
    )
    view = valuation(
        market,
        archive_records(ROOT / "data"),
        season,
        owned={i: q.owned_percent for i, q in quoted.items()},
    )
    returns = {i: entry["returns"] for i, entry in view.items()}
    playing = {i: entry["playing"] for i, entry in view.items()}
    expected = {i: entry["expected"] for i, entry in view.items()}

    # NOBODY THE BULLETIN SAYS IS OUT, in the whole twenty-three as in the ladder
    # below. Found by the code review of 18/09/2026: the twenty-three were
    # built before the filter and went on proposing whoever the bulletin listed.
    snapshot = ManualSquadSource(SQUAD_PATH).load()
    matchday = args.jornada or snapshot.round_number
    held_ids = {p.id for p in snapshot.squad.players}
    left_out = bulletin_out(
        load_bulletin(BULLETIN_DIR, matchday),
        (market[i] for i in market if i not in held_ids),
    )
    rows = [
        {"id": p.id, "position": p.position, "value": p.value}
        for p in market.values()
        if p.id not in left_out
    ]
    bought = best_squad_under_budget(
        rows, {i: v * ROUNDS_LEFT for i, v in expected.items()}, budget=args.budget
    )
    if bought is None:
        raise SystemExit("no legal squad fits the budget")
    proposed = improve_squad(
        bought["players"],
        market,
        returns,
        playing,
        budget=args.budget,
        candidates=[i for i in market if i not in left_out],
        draws=args.draws,
    )

    # WHICH WINDOW THIS IS. The changes below used to be listed one a round,
    # best first, with a closing line citing §6.8 — which is the wrong rule in
    # two of the three windows, and the wrong ADVICE in the one that matters
    # most. Before matchday 5 §6.7 lets the whole twenty-three be rebuilt at
    # once, so a ladder is a list nobody has to climb; in February §6.9 gives
    # six for the window and switches §6.8 off entirely.
    window, article = transfers_allowed(matchday)
    rungs = args.moves if args.moves is not None else (window or SQUAD_SIZE)

    mine = [p.id for p in snapshot.squad.players]
    gone = left_the_league(mine, market)
    covered = [i for i in mine if i not in gone]
    yours = squad_value(covered, market, returns, playing, draws=args.draws)

    print(f"{len(market)} players on the market, {ROUNDS_LEFT} rounds to play")
    if gone:
        by_id = {p.id: p for p in snapshot.squad.players}
        print()
        print("FORA DA LIGA — o mercado ja nao os lista, e nao voltam a pontuar:")
        for i in gone:
            held = by_id[i]
            print(f"  {held.name} ({held.club}) — o lugar dele esta morto ate o venderes")
        print(f"  o resto do plantel abaixo sao {len(covered)}, nao {len(mine)}.")
    print()
    print("THE PROPOSED 23")
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    for player_id in sorted(
        proposed["players"], key=lambda i: (order[market[i].position], -returns[i])
    ):
        player = market[player_id]
        mark = "<- tens" if player_id in mine else ""
        said = view[player_id]
        print(
            f"  {player.position.value:<4}{player.name[:19]:<21}{player.club[:12]:<13}"
            f"{player.value / 1e6:>5.2f}M"
            f"{expected[player_id]:>6.2f}"
            f"{quoted[player_id].owned_percent:>6.1f}%  {said['label']:<10}"
            f"{said['field_label']:<13}{mark}"
        )
    print("                                     price  proj  posse  o que é")

    print()
    print(f"  costs {proposed['cost']:,} of {args.budget:,}")
    print(f"  expected {proposed['expected_round']:.1f} a round")
    print(f"  your 23 expect {yours:.1f} a round — a gap of "
          f"{proposed['expected_round'] - yours:+.1f}, or "
          f"{(proposed['expected_round'] - yours) * ROUNDS_LEFT:+.0f} over the season")

    # The changes, one at a time, best first — so the list can be stopped
    # anywhere rather than taken whole. A squad rebuilt in fifteen moves on a
    # model that explains a quarter of the week-to-week variation is a lot of
    # conviction to spend at once, and the gains are not evenly spread.
    #
    # THE SEARCH IS THE ONE THE PAGE RUNS. It was restricted to the ideal
    # squad's players, which made the first rung here and the page's
    # recommendation two different men — and a model that answers the same
    # question twice, differently, is worse than one that answers it wrongly
    # once, because neither answer is its opinion. Both now search the whole
    # market, minus anyone with too little record to recommend.
    # And nobody the bulletin says is out, found once above.
    known = transfer_candidates(market, view, covered, left_out)
    print()
    if window is None:
        print(f"AS MUDANÇAS — {article}, sem limite ate a jornada "
              f"{FIRST_SCORING_MATCHDAY}")
        print("  Podes fazer as que quiseres de uma vez. A escada abaixo e so")
        print("  para poderes parar a meio se quiseres.")
    elif window > 1:
        print(f"AS MUDANÇAS — {article}, ate {window} para a janela toda")
        print("  Seis para o periodo inteiro, nao seis por jornada, e a "
              "transferencia")
        print("  semanal do §6.8 esta desligada enquanto isto corre.")
    else:
        print(f"AS MUDANÇAS, MELHOR PRIMEIRO — {article}, uma por jornada")
    print(f"  {'':4}{'sai':<20}{'entra':<20}{'ganho':>8}   o que é")

    working = list(covered)
    running = yours
    ladder = []
    while len(ladder) < rungs:
        step = improve_squad(
            working,
            market,
            returns,
            playing,
            budget=args.budget,
            candidates=known,
            max_swaps=1,
            draws=args.draws,
        )
        if not step["swaps"]:
            break
        held = set(working)
        going = next(i for i in held if i not in step["players"])
        coming = next(i for i in step["players"] if i not in held)
        ladder.append((going, coming, step["expected_round"] - running))
        working, running = step["players"], step["expected_round"]
        # A man sold is not bought back: two near-identical players make the
        # estimate flicker between them, and the ladder would spend its rungs
        # swapping one for the other and back.
        known = [i for i in known if i != going]

    for going, coming, gain in ladder:
        said = view[coming]
        print(
            f"  {market[going].position.value:<4}{market[going].name[:18]:<20}"
            f"{market[coming].name[:18]:<20}{gain:>+8.2f}   "
            f"{said['label']:<10}{said['field_label']}"
        )
    if not ladder:
        print("  none — the model does not beat your 23 with any single swap")
    else:
        print()
        print()
        print(f"  as {len(ladder)}: {running:.1f} por jornada "
              f"({running - yours:+.1f} sobre o teu plantel de agora)")
        if window is None:
            # The ladder is greedy and budget-bound, so it stops short of the
            # squad chosen whole. Saying so matters: the gap between the two is
            # the reason not to climb a ladder when nothing forces you to.
            short = proposed["expected_round"] - running
            if short > 0.05:
                print(f"  o plantel proposto la em cima faz "
                      f"{proposed['expected_round']:.1f} — mais {short:+.1f} do "
                      "que esta escada")
                print("  chega a dar. Uma escada gulosa presa ao orcamento para "
                      "antes do melhor;")
                print("  se podes comprar tudo de uma vez, compra o de la em "
                      "cima e nao este.")
        elif window > 1:
            print(f"  {article} deixa {window} nesta janela; as primeiras "
                  f"{min(window, len(ladder))} sao estas.")
        else:
            print(f"  {article} deixa uma por jornada — faz a primeira.")

    sheet = best_eleven(
        [
            {"id": i, "position": market[i].position, "value": market[i].value}
            for i in proposed["players"]
        ],
        expected,
    )
    print()
    print(f"  it would line up {sheet['formation']}, captain {market[sheet['captain']].name}")


if __name__ == "__main__":
    main()
