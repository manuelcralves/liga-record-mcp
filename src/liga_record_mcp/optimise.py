"""Picking the best legal team when the scores are already known.

Nothing here predicts anything. Everything here answers "given these numbers,
what was the most a legal team sheet could have scored" — which is only useful
once there is a season of numbers to ask it about, and until last season was
reconstructed there was not.

Two questions, both exactly answerable rather than approximated:

    best_eleven          the highest-scoring XI a 23-man squad could field
    best_squad_under_budget   the 23 to buy, under §6.6's quota and §6.4's money

They exist to build a ruler. A manager scoring five points a round has no idea
whether that is good until he knows what a perfect season was worth, what a
well-chosen squad blindly managed was worth, and what a careless one was worth.
Run against real scores those three numbers bracket the season, and his own
falls somewhere on it.

Used with hindsight these give a ceiling nobody could have reached. Used with
projections they give what a strategy would actually have returned. The
difference between the two is the value of knowing the future, and it is
usually much larger than people expect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import random
from math import gcd
from statistics import pstdev
from typing import Any

from .models import (
    BASE_BUDGET,
    BENCH_SIZE,
    SQUAD_QUOTA,
    STARTER_RANGE,
    XI_SIZE,
    Position,
    Selection,
    Squad,
)
from .rules import simulate_autosubs
from .stats import UNUSED_PENALTY


def _position_of(row: Mapping[str, Any]) -> Position:
    """Accepts either the enum or the string it serialises to."""
    found = row["position"]
    return found if isinstance(found, Position) else Position(str(found))


def legal_shapes() -> list[tuple[int, int, int]]:
    """Every (defenders, midfielders, forwards) satisfying §6.13.

    Derived from the per-position ranges and the size of an XI, the same way
    `rules.legal_formations` derives its labels — and checked against it, since
    two derivations of one rule drifting apart is exactly the sort of thing
    that would never announce itself.
    """
    lo_d, hi_d = STARTER_RANGE[Position.DEF]
    lo_m, hi_m = STARTER_RANGE[Position.MID]
    lo_f, hi_f = STARTER_RANGE[Position.FWD]
    keepers, _ = STARTER_RANGE[Position.GK]
    return [
        (d, m, f)
        for d in range(lo_d, hi_d + 1)
        for m in range(lo_m, hi_m + 1)
        for f in range(lo_f, hi_f + 1)
        if keepers + d + m + f == XI_SIZE
    ]


def best_eleven(
    squad: Sequence[Mapping[str, Any]],
    points: Mapping[str, float],
    *,
    default: float = float(UNUSED_PENALTY),
) -> dict[str, Any] | None:
    """The highest-scoring legal team sheet this squad could have put out.

    Exact, not searched: for a fixed shape the best XI is the top scorers at
    each position, and because §10.3(l) doubles the captain rather than
    replacing him, the same eleven that maximises the sum also contains the
    best captain. So seven shapes and four sorts settle it.

    `default` is what a player with no entry scores. §10.3(i)'s -1 is the right
    answer: a man who does not appear in a round's results did not play, and a
    manager who owned him was charged for it. Scoring him zero would quietly
    make absences free.

    The bench is one spare per position, best first, and then whoever is left.
    That is a strategy and not a rule — §6.13 asks for four substitutes and
    does not say which — but §11 substitutes like for like, so four names of
    the same position cover one position and abandon three.

    Taking the best four remaining instead looks obviously right and is not.
    Ranked by projection they came out as a keeper and three defenders, and a
    squad whose midfielders were the ones missing had no cover at all. A search
    over squads found it by preferring a WORSE defender purely because losing
    him pushed a midfielder onto the bench — which is the optimiser reporting a
    bug in its own scoring rather than a squad worth buying.

    Returns None when the squad cannot field a legal XI at all.
    """
    by_position: dict[Position, list[Mapping[str, Any]]] = {p: [] for p in Position}
    for row in squad:
        by_position[_position_of(row)].append(row)

    def score(row: Mapping[str, Any]) -> float:
        return float(points.get(str(row["id"]), default))

    ranked = {
        position: sorted(rows, key=lambda r: (-score(r), str(r["id"])))
        for position, rows in by_position.items()
    }

    best: dict[str, Any] | None = None
    for defenders, midfielders, forwards in legal_shapes():
        wanted = {
            Position.GK: STARTER_RANGE[Position.GK][0],
            Position.DEF: defenders,
            Position.MID: midfielders,
            Position.FWD: forwards,
        }
        if any(len(ranked[p]) < n for p, n in wanted.items()):
            continue

        starters = [row for p, n in wanted.items() for row in ranked[p][:n]]
        total = sum(score(row) for row in starters)
        captain = max(starters, key=lambda r: (score(r), str(r["id"])))
        total += score(captain)

        if best is None or total > best["points"]:
            chosen = {str(row["id"]) for row in starters}
            spare = {
                position: [r for r in ranked[position] if str(r["id"]) not in chosen]
                for position in Position
            }
            bench = [
                group[0]
                for group in (spare[p] for p in Position)
                if group
            ][:BENCH_SIZE]
            taken = {str(r["id"]) for r in bench}
            leftovers = sorted(
                (r for group in spare.values() for r in group if str(r["id"]) not in taken),
                key=lambda r: (-score(r), str(r["id"])),
            )
            bench = (bench + leftovers)[:BENCH_SIZE]

            best = {
                "starters": [str(row["id"]) for row in starters],
                "bench": [str(row["id"]) for row in bench],
                "captain": str(captain["id"]),
                "formation": f"{defenders}-{midfielders}-{forwards}",
                "points": total,
            }
    return best


def squad_value(
    squad_ids: Sequence[str],
    market: Mapping[str, Any],
    returns: Mapping[str, float],
    playing: Mapping[str, float],
    *,
    draws: int = 64,
    seed: int = 0,
    spread: bool = False,
) -> float | tuple[float, float]:
    """What a squad's team sheet is expected to return in a round.

    With `spread` it also returns how much the round-to-round result varies,
    which is what a caller needs to tell a real improvement from a sampling
    one.

    This is the objective `best_squad_under_budget` cannot express. That one
    maximises the sum of twenty-three season totals, which is not the game:
    eleven score. Left to itself it spends ten million of a forty million
    budget on two good goalkeepers, and one of them never plays a minute for
    you all season. That is not a hypothetical — it is what it did.

    So the whole round is played out instead. The eleven and the bench are
    named from what is expected of each man, exactly as a manager names them
    before kickoff; then who actually turns up is drawn from each player's own
    chance of playing; then §11 repairs what it can, like for like, in bench
    order, three at most. Averaging that over many draws prices depth properly:
    a fourth centre back is worth something because the third one gets injured,
    and a third goalkeeper is worth almost nothing because the first two do not
    both vanish.

    `returns` is what a player scores on the days he plays, and `playing` his
    chance of playing at all — the two halves the projection now keeps apart.
    """
    expected = {
        i: playing.get(i, 0.0) * returns.get(i, 0.0)
        + (1 - playing.get(i, 0.0)) * float(UNUSED_PENALTY)
        for i in squad_ids
    }
    sheet = best_eleven(
        [
            {"id": i, "position": market[i].position, "value": market[i].value}
            for i in squad_ids
        ],
        expected,
    )
    if sheet is None:
        return (float("-inf"), 0.0) if spread else float("-inf")

    squad = Squad(
        team_id=0, team_name="candidate", players=tuple(market[i] for i in squad_ids)
    )
    selection = Selection(
        starters=tuple(sheet["starters"]),
        bench=tuple(sheet["bench"]),
        captain=sheet["captain"],
        coach_id="candidate",
    )

    # Each player misses exactly his share of the draws, in an order of his
    # own. Two things had to be fixed here and only the first is obvious.
    #
    # The draws must belong to the PLAYER, not to his slot in the list. With
    # one shared stream, swapping a man shifts every later player's draw, so
    # two squads are compared against different luck and a search over squads
    # optimises the sampling instead of the football.
    #
    # And the share must be exact rather than sampled. Sampled, a man's own
    # rate lands anywhere near his probability — and whatever it lands on is
    # then fixed for every comparison he appears in, so an unlucky stream does
    # not average out, it becomes a permanent discount on him. That is how a
    # ninety-point defender came to be traded for one worth half a point, at a
    # reported gain.
    def stream(identifier: str) -> list[bool]:
        chance = min(1.0, max(0.0, playing.get(identifier, 0.0)))
        present = [n < round(chance * draws) for n in range(draws)]
        random.Random(f"{seed}:{identifier}").shuffle(present)
        return present

    turns_up = {i: stream(i) for i in squad_ids}
    rounds: list[float] = []
    for draw in range(draws):
        absent = {i for i in squad_ids if not turns_up[i][draw]}
        starters = list(selection.starters)
        captain = selection.captain
        if absent & set(starters):
            result = simulate_autosubs(squad, selection, absent)
            for swap in result.substitutions:
                starters[starters.index(swap.out_id)] = swap.in_id
            captain = result.captain_id or captain
        scored = {
            i: (float(UNUSED_PENALTY) if i in absent else returns.get(i, 0.0))
            for i in starters
        }
        rounds.append(sum(scored.values()) + scored.get(captain, 0.0))
    return sum(rounds) / draws if not spread else (sum(rounds) / draws, pstdev(rounds))


def improve_squad(
    squad_ids: Sequence[str],
    market: Mapping[str, Any],
    returns: Mapping[str, float],
    playing: Mapping[str, float],
    *,
    budget: int,
    candidates: Sequence[str] | None = None,
    shortlist: int = 8,
    passes: int = 3,
    draws: int = 400,
    seed: int = 0,
    max_swaps: int | None = None,
) -> dict[str, Any]:
    """Trade players for as long as the team sheet improves.

    Hill climbing rather than another dynamic program, because the objective it
    is climbing — what the eleven returns once absences and substitutions are
    played out — cannot be split into independent positions the way a sum of
    season totals can.

    IT MOVES TWO AT A TIME, and has to. A squad that has spent the budget can
    only swap a player for a cheaper one, so every single move that would free
    money to spend elsewhere looks like a loss on its own and is refused. The
    squad it was asked to repair was stuck exactly there: two goalkeepers worth
    ten million between them, and no single swap could pay for undoing it.
    Downgrading one man and upgrading another in the same move is what gets
    past that.

    It does not find the global optimum and does not claim to. It finds a squad
    that no legal move of one or two players improves.

    `max_swaps` caps how many players may change hands, which is what §6.9's
    February window needs: six for the whole month, not six a round, and the
    ordinary transfer of §6.8 switched off while it runs. Capped, the climb
    takes its best moves first and stops, so the six spent are the six worth
    most rather than the first six it happened to find.
    """
    squad = list(squad_ids)
    pool = list(candidates if candidates is not None else market)
    by_position: dict[Position, list[str]] = {}
    for identifier in pool:
        by_position.setdefault(market[identifier].position, []).append(identifier)

    def affordable(position: Position, held: set[str], headroom: int) -> list[str]:
        """The best few this squad could actually pay for, and the cheapest.

        Ranked candidates are filtered by price BEFORE the shortlist is cut,
        not after. Cutting first hands back an empty list whenever the best
        players at a position are dearer than the man being replaced, which is
        most of the time and was why an earlier version made no moves at all.
        """
        options = [
            i
            for i in by_position.get(position, ())
            if i not in held and market[i].value <= headroom
        ]
        options.sort(key=lambda i: -returns.get(i, 0.0))
        cheapest = min(options, key=lambda i: market[i].value, default=None)
        best = options[:shortlist]
        if cheapest is not None and cheapest not in best:
            best = best + [cheapest]
        return best

    def value_of(ids: Sequence[str]) -> float:
        return squad_value(ids, market, returns, playing, draws=draws, seed=seed)

    started = set(squad)

    def changed_so_far() -> int:
        return len(started - set(squad))

    def room(needed: int) -> bool:
        return max_swaps is None or changed_so_far() + needed <= max_swaps

    value, variation = squad_value(
        squad, market, returns, playing, draws=draws, seed=seed, spread=True
    )
    # Two squads sharing twenty-two players are usually a near tie, and a near
    # tie sampled a few hundred times comes out differently every seed. Without
    # a margin the climb churns forever on those, spending real transfers to
    # chase a difference that is not there. One standard error of the mean is
    # the smallest gap worth believing.
    tolerance = max(1e-9, variation / (draws ** 0.5))
    swaps: list[dict[str, Any]] = []

    for _ in range(passes):
        improved = False

        # One at a time first: cheaper, and it catches the easy gains.
        for out_id in list(squad):
            if not room(1 if out_id in started else 0):
                continue
            held = set(squad)
            headroom = budget - sum(market[i].value for i in squad) + market[out_id].value
            best_move, best_value = None, value
            for in_id in affordable(market[out_id].position, held, headroom):
                trial = [in_id if i == out_id else i for i in squad]
                score = value_of(trial)
                if score > best_value + tolerance:
                    best_move, best_value = in_id, score
            if best_move is not None:
                swaps.append(
                    {
                        "out": market[out_id].name,
                        "in": market[best_move].name,
                        "gain": round(best_value - value, 3),
                    }
                )
                squad[squad.index(out_id)] = best_move
                value, improved = best_value, True

        # Then in pairs: sell one down to free money, spend it on another.
        for down_id in list(squad):
            if not room(2):
                break
            held = set(squad)
            spent = sum(market[i].value for i in squad)
            cheap = affordable(
                market[down_id].position, held, budget - spent + market[down_id].value
            )
            cheap = sorted(cheap, key=lambda i: market[i].value)[:2]
            for filler in cheap:
                if market[filler].value >= market[down_id].value:
                    continue
                freed = market[down_id].value - market[filler].value
                halfway = [filler if i == down_id else i for i in squad]
                for up_id in halfway:
                    if up_id == filler:
                        continue
                    headroom = (
                        budget
                        - (spent - freed)
                        + market[up_id].value
                    )
                    for in_id in affordable(
                        market[up_id].position, set(halfway), headroom
                    ):
                        if market[in_id].value <= market[up_id].value:
                            continue
                        trial = [in_id if i == up_id else i for i in halfway]
                        score = value_of(trial)
                        if score > value + tolerance:
                            swaps.append(
                                {
                                    "out": f"{market[down_id].name} + {market[up_id].name}",
                                    "in": f"{market[filler].name} + {market[in_id].name}",
                                    "gain": round(score - value, 3),
                                }
                            )
                            squad, value, improved = trial, score, True
                            break
                    if improved:
                        break
                if improved:
                    break

        if not improved:
            break

    return {
        "players": squad,
        "expected_round": round(value, 2),
        "cost": sum(market[i].value for i in squad),
        "swaps": swaps,
    }


def best_squad_under_budget(
    players: Sequence[Mapping[str, Any]],
    points: Mapping[str, float],
    *,
    budget: int = BASE_BUDGET,
    quota: Mapping[Position, int] | None = None,
) -> dict[str, Any] | None:
    """The 23 to have bought, under §6.6's quota and §6.4's money.

    Exact, by dynamic programming, because the greedy answer is wrong in a way
    that matters: the best value at each position independently will overspend
    on one and leave another with nothing but the floor price. The budget is
    shared, so the split between positions has to be solved, not assumed.

    Maximises the sum of the 23 season totals. That is not quite the right
    objective — only eleven of them score in any round — but the difference is
    small and the alternative is not separable, so it is stated rather than
    hidden. Evaluate the answer by actually playing the season with it.

    Prices come in steps of a quarter of a million, so the money dimension is
    a few hundred buckets rather than forty million.
    """
    wanted = dict(quota or SQUAD_QUOTA)
    priced = [row for row in players if str(row["id"]) in points]
    if not priced:
        return None

    step = gcd(budget, *(int(row["value"]) for row in priced))
    if step <= 0:
        return None
    buckets = budget // step

    # Per position: the best `taken` players costing no more than `spend`.
    tables: dict[Position, list[list[tuple[float, tuple[str, ...]]]]] = {}
    for position, count in wanted.items():
        pool = [row for row in priced if _position_of(row) is position]
        table = [
            [(float("-inf"), ()) for _ in range(buckets + 1)] for _ in range(count + 1)
        ]
        for spend in range(buckets + 1):
            table[0][spend] = (0.0, ())
        for row in pool:
            cost = int(row["value"]) // step
            identifier = str(row["id"])
            gain = float(points[identifier])
            for taken in range(count, 0, -1):
                for spend in range(buckets, cost - 1, -1):
                    carried, squad = table[taken - 1][spend - cost]
                    if carried == float("-inf"):
                        continue
                    if carried + gain > table[taken][spend][0]:
                        table[taken][spend] = (carried + gain, squad + (identifier,))
        # Make each row monotonic, so "at most this much" really means at most.
        for taken in range(count + 1):
            for spend in range(1, buckets + 1):
                if table[taken][spend - 1][0] > table[taken][spend][0]:
                    table[taken][spend] = table[taken][spend - 1]
        tables[position] = table

    # Spend the shared budget across the four positions.
    combined = [(0.0, ()) for _ in range(buckets + 1)]
    for position, count in wanted.items():
        row = tables[position][count]
        merged: list[tuple[float, tuple[str, ...]]] = [
            (float("-inf"), ()) for _ in range(buckets + 1)
        ]
        for spend in range(buckets + 1):
            carried, squad = combined[spend]
            if carried == float("-inf"):
                continue
            for extra in range(buckets - spend + 1):
                gain, added = row[extra]
                if gain == float("-inf"):
                    continue
                total = carried + gain
                if total > merged[spend + extra][0]:
                    merged[spend + extra] = (total, squad + added)
        combined = merged

    best_points, chosen = max(combined, key=lambda entry: entry[0])
    if best_points == float("-inf") or len(chosen) != sum(wanted.values()):
        return None

    by_id = {str(row["id"]): row for row in priced}
    return {
        "players": list(chosen),
        "points": round(best_points, 1),
        "cost": sum(int(by_id[i]["value"]) for i in chosen),
        "budget": budget,
    }
