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
from itertools import combinations
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


def left_the_league(
    squad_ids: Sequence[str], market: Mapping[str, Any]
) -> list[str]:
    """Held players the market no longer lists — men who have left the league.

    A REAL AND RECURRING EVENT, not a data glitch. Diogo Calila was in the
    squad on 8 September 2026 and had signed for Maghreb Fes five days earlier;
    Record dropped him from the market and left him in the teams that held him,
    where he will score nothing for the rest of the season.

    It was found by accident. `build_dashboard` died on a KeyError deep inside
    `expected_round_points`, and `propose_squad` did something worse — it filtered the
    unknown id out silently, so it priced twenty-two against a rival
    twenty-three and reported the gap as if both were whole. A squad quietly
    shrinking is the failure that hides; the crash is the one that gets fixed.

    So callers ask this first and say the names out loud. Nothing here guesses
    a replacement: which defender to buy is a decision, and the point of
    naming him is that the manager gets to make it.
    """
    return [i for i in squad_ids if i not in market]


def insured(expected: float, chance: float, cover: float, default: float) -> float:
    """What a starter is worth once the bench can replace him.

    His own estimate already charges him §10.3(i)'s -1 for the weeks he does
    not play, and for a STARTER that is false: §11 sends on the substitute of
    his position and the manager collects that man's points instead. So the -1
    is given back and the cover put in its place, weighted by how often he is
    missing.

    `cover` is what the substitute behind him is expected to score — his own
    estimate, -1 and all, because a substitute who does not play either leaves
    the starter's -1 standing. With no one of his position on the bench the
    cover IS the -1 and this returns the estimate untouched.

    NEVER WORSE THAN THE -1, and that is not a rounding convenience. Callers
    rank a man the bulletin says is out a thousand points below everyone —
    `build_dashboard.selection_values` does, and so does the backtest's
    `knows_availability` — so that he is picked last without being deleted. Read
    as cover, that number would say the bench is catastrophic and would frighten
    the eleven away from every man behind whom the only substitute is injured.
    He simply will not come on, which is worth exactly the -1 the starter keeps.

    The chance is held to 0..1 here, where every caller can rely on it: a
    number above 1 would otherwise DOCK a man for being too likely to play.
    """
    held = min(1.0, max(0.0, chance))
    return expected + (1.0 - held) * (max(cover, default) - default)


def _best_at(
    ranked: Sequence[Mapping[str, Any]],
    wanted: int,
    score,
    playing: Mapping[str, float],
    default: float,
) -> tuple[float, list[Mapping[str, Any]], dict[str, float]] | None:
    """The starters of one position, with the one substitute behind them priced.

    ONE SUBSTITUTE COVERS THE POSITION ONCE, and that is the whole difficulty.
    The bench holds a single man of each position, so what the position is worth
    is what its starters return plus what he adds the weeks he is needed:

        sum of their estimates + P(at least one of them misses) x (cover + 1)

    Read instead as a promise to every starter separately — each one paid the
    cover for his own absent weeks — it counts the same man over and over.
    Measured on the two reconstructed seasons that way, it started whoever was
    least likely to play, tripled the substitutions and lost 255 points a
    season. The probability that ANYBODY misses is what a single substitute can
    answer for.

    Exact for the position: every set of `wanted` men is tried, the substitute
    being the best of those left over, which is the bench rule. At most seventy
    sets per position, cached by shape in the caller.

    Returns the position's total, its starters, and each man's own insured
    number — that last only so the armband can be placed, where one slot is
    filled and the sharing does not arise.
    """
    if len(ranked) < wanted:
        return None
    plain = {str(row["id"]): score(row) for row in ranked}
    if len(ranked) == wanted:
        # Nobody of his position is left to come on, so §11 cannot repair a
        # thing here and each man is worth his own estimate, -1 and all.
        return sum(plain.values()), list(ranked), plain

    best: tuple[float, list[Mapping[str, Any]], dict[str, float]] | None = None
    for picked in combinations(range(len(ranked)), wanted):
        chosen = [ranked[n] for n in picked]
        spare = ranked[next(n for n in range(len(ranked)) if n not in picked)]
        cover = score(spare)
        needed = 1.0
        for row in chosen:
            # Held to 0..1 as `insured` holds it, so the position's total and
            # each man's own number never read the same chance differently.
            needed *= min(1.0, max(0.0, playing.get(str(row["id"]), 1.0)))
        total = sum(score(row) for row in chosen) + (1.0 - needed) * (
            max(cover, default) - default
        )
        if best is None or total > best[0]:
            values = {
                str(row["id"]): insured(
                    score(row), playing.get(str(row["id"]), 1.0), cover, default
                )
                for row in chosen
            }
            best = (total, chosen, values)
    return best


def best_eleven(
    squad: Sequence[Mapping[str, Any]],
    points: Mapping[str, float],
    *,
    default: float = float(UNUSED_PENALTY),
    playing: Mapping[str, float] | None = None,
) -> dict[str, Any] | None:
    """The highest-scoring legal team sheet this squad could have put out.

    Exact, not searched: for a fixed shape the best XI is the top scorers at
    each position, and because §10.3(l) doubles the captain rather than
    replacing him, the same eleven that maximises the sum also contains the
    best captain. So seven shapes and four sorts settle it.

    WITH `playing`, THE BENCH IS PART OF THE CHOICE. Each man's chance of
    playing turns his estimate into what `insured` prices: an uncertain starter
    with a good substitute of his position behind him is worth more than his
    own number, and a certain one with no cover is worth less. The eleven is
    then chosen on that, one position at a time, and §11.5 carries the armband
    to whoever comes on, so the captain's doubled slot is priced the same way.
    Without it, nothing changes.

    Three things the priced version leaves out, and the measurement in
    docs/REVISAO-2026-09-23-onze-com-seguro.md was read knowing them.

    §11 allows three substitutions in all, and nothing here counts them: a
    sheet risky in all four positions can need a fourth.

    And each position is settled on its own total before the armband is
    placed. In the plain version that is exact, because doubling a man adds a
    fixed amount whatever else is chosen. Here his second slot is worth what
    the cover behind him makes it worth, so a position could in principle give
    up a hundredth of a point to free a better captain. Near ties only, and
    never yet seen to change a sheet.

    `points` reported on the sheet stays the plain sum, so every caller that
    compares two sheets keeps comparing the same quantity; `insured` carries
    the number the choice was made on.

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

    # Seven shapes ask for the same few (position, count) pairs, and each one
    # is a search over sets. Worked out once each.
    settled: dict[tuple[Position, int], Any] = {}

    def at_position(position: Position, count: int):
        if (position, count) not in settled:
            settled[(position, count)] = _best_at(
                ranked[position], count, score, playing or {}, default
            )
        return settled[(position, count)]

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

        if playing is None:
            starters = [row for p, n in wanted.items() for row in ranked[p][:n]]
            worth = {str(row["id"]): score(row) for row in starters}
            chosen_on = sum(worth.values())
        else:
            picked = [at_position(position, n) for position, n in wanted.items()]
            if any(found is None for found in picked):
                continue
            starters = [row for found in picked for row in found[1]]
            worth = {i: v for found in picked for i, v in found[2].items()}
            chosen_on = sum(found[0] for found in picked)

        captain = max(starters, key=lambda r: (worth[str(r["id"])], str(r["id"])))
        chosen_on += worth[str(captain["id"])]
        total = sum(score(row) for row in starters) + score(captain)

        if best is None or chosen_on > best["insured"]:
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
                "insured": chosen_on,
            }
    return best


def expected_round_points(
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

    IT WAS CALLED `squad_value` until 24/09/2026, and the project had three
    things under that name: this projection, the MCP tool that ranks the squad
    by points per MATCH with each man's price, and a field carrying the squad's
    cost in EUROS. Points a round, points a match and money. The tool is the
    one with a contract, so it kept the name and these two gave theirs up.

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

    # ABSENCE IS DRAWN PER PLAYER, NOT PER CLUB, and that is a simplification
    # rather than the truth — a postponed fixture removes a whole club at once,
    # and §11 allows only three substitutions, so five starters from one club
    # going out together is a different event from five going out separately.
    #
    # Measured before deciding it did not matter. Postponements run at 1.1% of
    # club-rounds — seventeen fixtures in the five seasons openfootball holds —
    # and at that rate, on the current squad:
    #
    #     drawn per club (what happens)    44.795
    #     drawn per player (what this does) 44.865
    #
    # Seven hundredths of a point a round, about two over a season, against a
    # squad choice worth ninety. Not worth the complication.
    #
    # The reason it is so small is worth keeping, because it will not always
    # hold: his concentration sits on the BENCH — five of Gil Vicente in the
    # twenty-three but one in the eleven — and a substitute contributes almost
    # nothing to a round. A squad with five of one club STARTING would pay
    # considerably more, and this measurement would not describe it.
    #
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
    horizon: Sequence[Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    """Trade players for as long as the team sheet improves.

    Hill climbing rather than another dynamic program, because the objective it
    is climbing — what the eleven returns once absences and substitutions are
    played out — cannot be split into independent positions the way a sum of
    season totals can.

    IT TAKES THE BEST MOVE AND NOT THE FIRST ONE IT FINDS, which is not a
    refinement. Walking the candidates and accepting whatever helps makes the
    answer a function of the order they arrive in, and every order is equally
    defensible, so choosing between them is a coin toss the model has no
    business making. On 2025/26 that coin was worth 136 points: six shuffles of
    the same candidates returned seasons from 1360 to 1496, while the six
    squads they produced differed by 0.26 points a round on the objective this
    is actually maximising. The search could barely tell them apart. The spread
    was luck downstream of an arbitrary tie-break, and it was wide enough to
    bury any real improvement measured against it — which is what it did.

    So every legal move is scored against the same squad, the best is taken,
    and the sweep starts again. Ties fall to whichever id sorts first — as
    text, since that is what an id is here, so "1000" comes before "999". That
    is arbitrary, and deliberately so: the point is not which one wins but that
    the same one always does. The answer is now a
    function of the SET of candidates rather than of their order, which is why
    the loops walk `sorted(...)` and why `affordable` ranks on a total key.

    IT MOVES TWO AT A TIME, and has to. A squad that has spent the budget can
    only swap a player for a cheaper one, so every single move that would free
    money to spend elsewhere looks like a loss on its own and is refused. The
    squad it was asked to repair was stuck exactly there: two goalkeepers worth
    ten million between them, and no single swap could pay for undoing it.
    Downgrading one man and upgrading another in the same move is what gets
    past that. Pairs are searched the same way as singles and for the same
    reason — taking the first pair that helped was the larger half of the 136,
    and it is how one shuffle happened to buy the season's third top scorer.

    It does not find the global optimum and does not claim to. It finds a squad
    that no legal move of one or two players improves.

    `max_swaps` caps how many players may change hands, which is what §6.9's
    February window needs: six for the whole month, not six a round, and the
    ordinary transfer of §6.8 switched off while it runs. Capped, the climb
    takes its best moves first and stops, so the six spent are the six worth
    most rather than the first six it happened to find — which is what this
    docstring already promised, and only became true when the search stopped
    taking the first thing that helped.

    `passes` is how many times the two searches alternate, not how many players
    change hands: each runs itself out before handing over.

    `horizon` LOOKS PAST SATURDAY. Without it a squad is priced on `returns`
    alone, the season values, as though every week left were an average one.
    Given one `returns` map per round ahead, each moved by that round's
    opponent, a squad is priced in every round and the prices averaged, and the
    shortlist is ranked on the same average so the candidates and the price
    agree. `playing` stays as it is: an opponent moves what a man returns when
    he plays, not whether he does.

    The margin becomes the mean of each round's standard error. The rounds
    share their draws, so they move together and the exact error of the average
    can only be smaller — the margin is a shade wider than it needs to be, and
    never narrower. Without a horizon, or with one round equal to `returns`,
    every number here is the one it always was; a test holds that.

    Measured in `scripts/backtest_transfers.py` before it was wired, against the
    season values this search had always used alone.
    """
    squad = list(squad_ids)
    pool = list(candidates if candidates is not None else market)
    by_position: dict[Position, list[str]] = {}
    for identifier in pool:
        by_position.setdefault(market[identifier].position, []).append(identifier)

    # The rounds a squad is priced over. One round of season values is the
    # search as it always was: the arithmetic below divides by one.
    rounds: list[Mapping[str, float]] = list(horizon) if horizon else [returns]
    ranking: Mapping[str, float] = (
        {
            i: sum(view.get(i, 0.0) for view in rounds) / len(rounds)
            for i in dict.fromkeys(p for view in rounds for p in view)
        }
        if horizon
        else returns
    )

    def affordable(position: Position, held: set[str], headroom: int) -> list[str]:
        """The best few this squad could actually pay for, and the cheapest.

        Ranked candidates are filtered by price BEFORE the shortlist is cut,
        not after. Cutting first hands back an empty list whenever the best
        players at a position are dearer than the man being replaced, which is
        most of the time and was why an earlier version made no moves at all.

        Both keys are total. Ranked on the projection alone, players sharing a
        number kept whatever order the pool arrived in, and that order decided
        which of them survived the cut at `shortlist` — an arbitrary choice
        made quietly, on the way in, before any football had been evaluated.
        """
        options = [
            i
            for i in by_position.get(position, ())
            if i not in held and market[i].value <= headroom
        ]
        options.sort(key=lambda i: (-ranking.get(i, 0.0), i))
        cheapest = min(options, key=lambda i: (market[i].value, i), default=None)
        best = options[:shortlist]
        if cheapest is not None and cheapest not in best:
            best = best + [cheapest]
        return best

    #: Squads already priced in this climb. Scoring every move against a fixed
    #: baseline means the sweeps overlap heavily — 28% of the calls here were
    #: exact repeats on the uncapped climb, and 45% on the one transfer the
    #: dashboard asks for every morning.
    #:
    #: Keyed on the SET, which is sound for the same reason the search is: a
    #: squad is worth what it is worth whatever order its names arrive in.
    #: `stream()` seeds each absence on the player's own id, `best_eleven`
    #: sorts on a total key, and §11 indexes the bench by id — so the same
    #: twenty-three price bit-for-bit identically however they are shuffled.
    #: Nothing else the price depends on — the market, the two projections,
    #: the horizon, `draws`, `seed` — can move inside one call.
    priced: dict[frozenset[str], float] = {}

    def value_of(ids: Sequence[str]) -> float:
        key = frozenset(ids)
        if key not in priced:
            priced[key] = sum(
                expected_round_points(ids, market, view, playing, draws=draws, seed=seed)
                for view in rounds
            ) / len(rounds)
        return priced[key]

    started = set(squad)

    def within_cap(trial: Sequence[str]) -> bool:
        """§6.9's allowance, counted on the squad the move would leave behind.

        Counted rather than predicted. A move that sells a man bought earlier
        in this same climb spends nothing new, and one that buys an original
        back hands an allowance in; reasoning about that before the move got it
        wrong in both directions, and the squad is right there to be counted.
        """
        return max_swaps is None or len(started.difference(trial)) <= max_swaps

    starting = [
        expected_round_points(squad, market, view, playing, draws=draws, seed=seed, spread=True)
        for view in rounds
    ]
    value = sum(price for price, _ in starting) / len(starting)
    variation = sum(spread for _, spread in starting) / len(starting)
    # Two squads sharing twenty-two players are usually a near tie, and a near
    # tie sampled a few hundred times comes out differently every seed. Without
    # a margin the climb churns forever on those, spending real transfers to
    # chase a difference that is not there. One standard error of the mean is
    # the smallest gap worth believing.
    #
    # Held against the squad the sweep STARTED from, never against the best
    # candidate seen so far. Chained, the margin smuggles the ordering back in:
    # of two candidates a tenth apart, whichever is read first is kept, and the
    # second never clears the bar its own rival just raised.
    tolerance = max(1e-9, variation / (draws ** 0.5))
    swaps: list[dict[str, Any]] = []

    def best_single() -> tuple[float, str, str, list[str]] | None:
        """The best legal one-for-one, every one scored against the same squad."""
        spent = sum(market[i].value for i in squad)
        held = set(squad)
        best: tuple[float, str, str, list[str]] | None = None
        for out_id in sorted(squad):
            headroom = budget - spent + market[out_id].value
            for in_id in affordable(market[out_id].position, held, headroom):
                trial = [in_id if i == out_id else i for i in squad]
                if not within_cap(trial):
                    continue
                score = value_of(trial)
                if best is None or score > best[0]:
                    best = (score, out_id, in_id, trial)
        return best

    def best_pair() -> tuple[float, str, str, str, str, list[str]] | None:
        """The best legal sell-one-down-to-buy-another, on the same terms."""
        spent = sum(market[i].value for i in squad)
        held = set(squad)
        best: tuple[float, str, str, str, str, list[str]] | None = None
        for down_id in sorted(squad):
            cheap = affordable(
                market[down_id].position, held, budget - spent + market[down_id].value
            )
            cheap = sorted(cheap, key=lambda i: (market[i].value, i))[:2]
            for filler in cheap:
                if market[filler].value >= market[down_id].value:
                    continue
                freed = market[down_id].value - market[filler].value
                halfway = [filler if i == down_id else i for i in squad]
                for up_id in sorted(halfway):
                    if up_id == filler:
                        continue
                    headroom = budget - (spent - freed) + market[up_id].value
                    for in_id in affordable(
                        market[up_id].position, set(halfway), headroom
                    ):
                        if market[in_id].value <= market[up_id].value:
                            continue
                        trial = [in_id if i == up_id else i for i in halfway]
                        if not within_cap(trial):
                            continue
                        score = value_of(trial)
                        if best is None or score > best[0]:
                            best = (score, down_id, filler, up_id, in_id, trial)
        return best

    # Each search runs itself out before the other takes over. Taking the best
    # move means a sweep commits once, so bounding the climb at `passes` sweeps
    # would cap it at three transfers; the bound belongs on the alternation.
    # The count here is only a backstop — every step has to clear the tolerance
    # against a squad that just got better, so the climb ends on its own.
    ceiling = 2 * len(squad) + 1

    for _ in range(passes):
        improved = False

        # One at a time first: cheaper, and it catches the easy gains.
        for _ in range(ceiling):
            move = best_single()
            if move is None or move[0] <= value + tolerance:
                break
            score, out_id, in_id, trial = move
            swaps.append(
                {
                    "out": market[out_id].name,
                    "in": market[in_id].name,
                    "gain": round(score - value, 3),
                }
            )
            squad, value, improved = trial, score, True

        # Then in pairs: sell one down to free money, spend it on another.
        for _ in range(ceiling):
            move = best_pair()
            if move is None or move[0] <= value + tolerance:
                break
            score, down_id, filler, up_id, in_id, trial = move
            swaps.append(
                {
                    "out": f"{market[down_id].name} + {market[up_id].name}",
                    "in": f"{market[filler].name} + {market[in_id].name}",
                    "gain": round(score - value, 3),
                }
            )
            squad, value, improved = trial, score, True

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
