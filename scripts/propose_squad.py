"""Propose the twenty-three to hold when the squad locks.

Transfers are unlimited until matchday 5 and one a round afterwards, so this is
the last moment the whole team can be chosen at once. Everything else the
project does is worth less than getting this right: measured over a season, the
gap between a good squad and a careless one is several hundred points, and the
gap between good weekly transfers and no transfers at all is a few dozen.

WHAT IT KNOWS. Two reconstructed seasons of what each player returns on the
days he plays, this season's two rounds, and who actually took the field —
from §10.3(i)'s -1, the only signal Liga Record gives about appearances.

WHAT IT DOES WITH IT. A player is estimated in two halves, because they behave
differently and because folding them together hides the larger one:

    expected = P(plays) x (what he returns when he plays) + P(not) x -1

His returns are shrunk toward what players at HIS CURRENT CLUB in his position
return — his old club's numbers are his, the club's are not, and a man who has
moved to Porto is not the player his Arouca record says he is. His chance of
playing leans on this season, because being in the side is news and last
season's team sheet is not.

Then the squad is chosen under §6.6's quota and §6.4's budget, and repaired:
the exact optimiser maximises the sum of twenty-three, which is not the game,
so a search over the real objective — what the eleven returns once absences and
§11 substitutions are played out — finishes the job.

    python scripts/propose_squad.py
    python scripts/propose_squad.py --draws 800   # a slower, steadier search
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

from liga_record_mcp.models import (  # noqa: E402
    BASE_BUDGET,
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    SQUAD_SIZE,
    Position,
)
from liga_record_mcp.advice import MIN_OWN_HISTORY  # noqa: E402
from liga_record_mcp.rules import transfers_allowed  # noqa: E402
from liga_record_mcp.optimise import (  # noqa: E402
    best_eleven,
    best_squad_under_budget,
    improve_squad,
    squad_value,
)
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    ManualSquadSource,
    load_appearances,
)
from liga_record_mcp.stats import (  # noqa: E402
    APPEARANCE_PRIOR,
    describe_pick,
    NO_MATCH,
    PLAYED,
    PRIOR_STRENGTH,
    ROTATION_PRIOR,
    UNUSED_PENALTY,
    matches_played,
)

SEASONS = (ROOT / "data" / "last-season.json", ROOT / "data" / "season-2024-25.json")
SQUAD_PATH = ROOT / "data" / "squad.yaml"
APPEARANCES_PATH = ROOT / "data" / "appearances.json"

#: Rounds still to be played once the squad locks. Used only to turn a rate
#: into a season, never to choose anything.
ROUNDS_LEFT = LAST_MATCHDAY - FIRST_SCORING_MATCHDAY + 1

#: How many appearances a club-and-position group needs before it is worth
#: more than the league's average for that position. Ten is about three
#: players' worth of a fortnight — enough to say something, and far short
#: of enough to override it.
MIN_POOL_APPEARANCES = 10


def history():
    """Appearances, points-when-playing, and the spread, across the archives.

    The spread is kept because §10.3(l) doubles the captain: two players on the
    same average are the same signing and very different armbands.
    """
    played = defaultdict(int)
    scored = defaultdict(float)
    available = defaultdict(int)
    each = defaultdict(list)
    for path in SEASONS:
        if not path.exists():
            continue
        for player_id, player in json.loads(
            path.read_text(encoding="utf-8")
        )["players"].items():
            for match in player["matches"]:
                available[player_id] += 1
                if match.get("used"):
                    played[player_id] += 1
                    scored[player_id] += float(match["points"])
                    each[player_id].append(float(match["points"]))
    spread = {
        i: statistics.pstdev(v) for i, v in each.items() if len(v) >= 15
    }
    return played, scored, available, spread


def this_season(market, counts):
    """The same two numbers from the rounds already played.

    Round 2 is on file from `record_appearances`. Round 1 is read off §10.3(i):
    a score of exactly -1 for a player whose club played is a man who did not.
    That reading is not certain — a player who did take the field and whose
    rating and events netted to -1 is indistinguishable — but it is right far
    more often than it is wrong, and there are only two rounds of it.
    """
    recorded = {}
    if APPEARANCES_PATH.exists():
        store = json.loads(APPEARANCES_PATH.read_text(encoding="utf-8"))
        for rnd, entry in (store.get("rounds") or {}).items():
            for player_id, status in (entry.get("players") or {}).items():
                recorded.setdefault(player_id, {})[int(rnd)] = status

    played, scored, available = defaultdict(int), defaultdict(float), defaultdict(int)
    for player in market.values():
        rounds = counts.get(player.club, 0)
        if rounds <= 0:
            continue
        seen = recorded.get(player.id, {})
        appearances = 0
        for rnd in range(1, rounds + 1):
            status = seen.get(rnd)
            if status == NO_MATCH:
                continue
            if status is None:
                # Only the latest round is separable from a running total.
                points = (
                    player.points_round
                    if rnd == rounds
                    else player.points_total - player.points_round
                )
                status = "unused" if points == UNUSED_PENALTY else PLAYED
            available[player.id] += 1
            if status == PLAYED:
                appearances += 1
        played[player.id] = appearances
        # §10.3(i) charged -1 for each round he sat; adding those back leaves
        # what he scored on the days he played.
        scored[player.id] = player.points_total + (available[player.id] - appearances)
    return played, scored, available


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
    counts = matches_played(client.fixtures())

    old_played, old_scored, old_available, spread = history()
    new_played, new_scored, new_available = this_season(market, counts)

    # What a player at this club in this position returns when he plays, and
    # the club is the CURRENT one: a man's old numbers are his, his old club's
    # are not, and the pool is what carries a move from Arouca to Porto.
    #
    # Two things have to be right here and neither is obvious.
    #
    # HE MUST NOT BE IN HIS OWN POOL. Shrinking an estimate toward a group he
    # belongs to shrinks him toward himself, which is not shrinkage. In a group
    # of one it does nothing at all — and that is not hypothetical: it proposed
    # three goalkeepers from the same club at the floor price, on one match
    # between them, because each was his own prior and the prior was a good
    # afternoon.
    #
    # AND THE POOL MUST BE WEIGHTED BY EVIDENCE. Unweighted, a player with one
    # appearance moves the group as much as one with seventy, so a single loud
    # afternoon becomes the club's expected return and every team-mate inherits
    # it.
    tally: dict[tuple[str, str], tuple[float, int]] = defaultdict(lambda: (0.0, 0))
    for player in market.values():
        appearances = old_played[player.id] + new_played[player.id]
        if appearances:
            points, seen = tally[(player.club, player.position.value)]
            tally[(player.club, player.position.value)] = (
                points + old_scored[player.id] + new_scored[player.id],
                seen + appearances,
            )

    total_points = sum(points for points, _ in tally.values())
    total_seen = sum(seen for _, seen in tally.values())
    league = total_points / total_seen if total_seen else 0.0
    by_position = {}
    for position in Position:
        points = sum(p for (c, pos), (p, s) in tally.items() if pos == position.value)
        seen = sum(s for (c, pos), (p, s) in tally.items() if pos == position.value)
        by_position[position] = points / seen if seen else league

    def prior_for(player) -> float:
        """The group's return with this player taken out of it."""
        points, seen = tally.get((player.club, player.position.value), (0.0, 0))
        points -= old_scored[player.id] + new_scored[player.id]
        seen -= old_played[player.id] + new_played[player.id]
        if seen < MIN_POOL_APPEARANCES:
            # A promoted club, or a position nobody at the club has played in
            # the top flight. "We do not know" is the position average, not a
            # guess built from one man's fortnight.
            return by_position[player.position]
        return points / seen

    league_availability = statistics.mean(
        [
            (old_played[i] + new_played[i]) / total
            for i in market
            if (total := old_available[i] + new_available[i]) > 0
        ]
        or [0.5]
    )

    returns, playing, basis = {}, {}, {}
    for player in market.values():
        prior = prior_for(player)

        appearances = old_played[player.id] + new_played[player.id]
        total_points = old_scored[player.id] + new_scored[player.id]
        returns[player.id] = (total_points + prior * PRIOR_STRENGTH) / (
            appearances + PRIOR_STRENGTH
        )

        # Availability leans on this season: being in the side is news, and a
        # team sheet from two years ago at another club is not.
        seasons_seen = old_available[player.id]
        now_seen = new_available[player.id]
        old_rate = old_played[player.id] / seasons_seen if seasons_seen else None
        anchor = old_rate if old_rate is not None else league_availability
        playing[player.id] = (
            new_played[player.id] + anchor * APPEARANCE_PRIOR + league_availability * ROTATION_PRIOR
        ) / (now_seen + APPEARANCE_PRIOR + ROTATION_PRIOR)
        basis[player.id] = (appearances, seasons_seen + now_seen)

    def known_rate(player_id, blended):
        """What his record shows, rather than what the projection assumes.

        The projection shrinks hard toward the league on two rounds of a new
        season, which is right for forecasting and wrong for a label: it says
        every player might be a rotation risk, because in August every player
        might be. His own history answers the question the label asks.
        """
        seen = old_available.get(player_id, 0)
        if seen >= 20:
            return old_played[player_id] / seen
        return blended.get(player_id)

    rows = [
        {"id": p.id, "position": p.position, "value": p.value} for p in market.values()
    ]
    expected = {
        i: playing[i] * returns[i] + (1 - playing[i]) * float(UNUSED_PENALTY)
        for i in market
    }
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
        draws=args.draws,
    )

    snapshot = ManualSquadSource(SQUAD_PATH).load()
    # WHICH WINDOW THIS IS. The changes below used to be listed one a round,
    # best first, with a closing line citing §6.8 — which is the wrong rule in
    # two of the three windows, and the wrong ADVICE in the one that matters
    # most. Before matchday 5 §6.7 lets the whole twenty-three be rebuilt at
    # once, so a ladder is a list nobody has to climb; in February §6.9 gives
    # six for the window and switches §6.8 off entirely.
    matchday = args.jornada or snapshot.round_number
    window, article = transfers_allowed(matchday)
    rungs = args.moves if args.moves is not None else (window or SQUAD_SIZE)

    mine = [p.id for p in snapshot.squad.players]
    covered = [i for i in mine if i in market]
    yours = squad_value(covered, market, returns, playing, draws=args.draws)

    print(f"{len(market)} players on the market, {ROUNDS_LEFT} rounds to play")
    print()
    print("THE PROPOSED 23")
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    for player_id in sorted(
        proposed["players"], key=lambda i: (order[market[i].position], -returns[i])
    ):
        player = market[player_id]
        seen, of = basis[player_id]
        mark = "<- tens" if player_id in mine else ""
        said = describe_pick(
            appearances=seen,
            availability=known_rate(player_id, playing),
            volatility=spread.get(player_id),
            owned_percent=quoted[player_id].owned_percent,
        )
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
    known = [
        i
        for i in market
        if basis.get(i, (0, 0))[0] >= MIN_OWN_HISTORY or i in covered
    ]
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
        seen, _ = basis.get(coming, (0, 0))
        said = describe_pick(
            appearances=seen,
            availability=known_rate(coming, playing),
            volatility=spread.get(coming),
            owned_percent=quoted[coming].owned_percent,
        )
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
