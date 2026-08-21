"""One season, played out blind, with every term the regulation scores.

The squad backtests measured whether a strategy was worth points. This shows
what it would actually have had you do: the eleven named each round, the
armband, the coach, the one transfer §6.8 allows, and the bet on the season's
top scorer that §10.3(m) lets you make once and never again.

Nothing here sees the future. Before each round the projection is rebuilt from
matchdays strictly earlier, the eleven and the four are named from it, and only
then is the round scored. The opening squad is chosen from the four matchdays
of §19's trial and nothing after them, and the top-scorer bet from the same.

EVERY TERM, so the total means something:

    the eleven          §6.13, and only they score
    the captain         §10.3(l), doubled, and inherited on a substitution
    §11 substitutions   like for like, in bench order, three at most
    the unused          §10.3(i), -1 each, for anyone named who did not play
    the coach           §14.4, added to the team every round, free
    the top scorer      §10.3(m), +20 / +10 / +5, once, in the final standings

    python scripts/season_report.py
    python scripts/season_report.py --from 6      # under §16.4's reading
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from typing import Any
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.backtest import (  # noqa: E402
    ABSENT,
    best_transfer,
    pick_coach,
    play_round,
    shrunk_projection,
    two_part_projection,
)
from liga_record_mcp.models import (  # noqa: E402
    BASE_BUDGET,
    BRONZE_BOOT_BONUS,
    GOLDEN_BOOT_BONUS,
    LAST_MATCHDAY,
    REOPENED_FIRST_ROUND,
    REOPENED_LAST_MATCHDAY,
    REOPENED_MAX_SWAPS,
    SILVER_BOOT_BONUS,
    Position,
)
from liga_record_mcp.optimise import best_squad_under_budget, improve_squad  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, OpenFootballClient  # noqa: E402
from liga_record_mcp.stats import MEAN_MARK_POINTS, coach_season  # noqa: E402

SEASON_PATH = ROOT / "data" / "last-season.json"
ARCHIVE_PATH = ROOT / "data" / "season-2024-25.json"
ARCHIVE_TAG = "2025-26"
ALL_MATCHDAYS = list(range(1, LAST_MATCHDAY + 1))

#: The season before is laid out on NEGATIVE matchdays: its round 1 becomes
#: -33, its round 34 becomes 0. Nothing else in this file has to know.
#:
#: The alternative was to shift this season forward by 34 and translate every
#: number back for display, which is the same arithmetic done in more places
#: and forgotten in one of them. Negatives keep `upto=5` meaning exactly what
#: it says — everything before matchday 5 of the season being replayed — while
#: the archive falls before it for free. The recency window keeps working too:
#: at matchday 5 it looks at 3 and 4, and never reaches back into last year.
ARCHIVE_OFFSET = -LAST_MATCHDAY

#: Manuel's own account of the site: the squad locks at matchday 5 and the four
#: before it do not count. §16.4 says the standings start at 6. One round apart,
#: and it is shown rather than chosen — the first round is marked.
DEFAULT_FIRST = 5


def _matches_of(player) -> list:
    return player.get("matches") or []


def load(market, *, use_archive: bool = True, order_seed: int | None = None):
    """Points, minutes and goals per matchday, and the club to pool each with.

    Two seasons, not one. The squad is bought at matchday 5 and four rounds is
    almost nothing to buy on — this report used to do exactly that, and the
    number it produced was a measurement of the model with its memory removed.
    The pages Manuel actually reads have always used both reconstructions; only
    the backtest was starved, because it was written before the second one
    existed and nobody went back.

    A player is given last season's rounds ONLY IF HE PLAYED IN IT. Padding a
    newcomer with thirty-four absences would not say "we have no record of
    him", it would say "he was left out thirty-four times", and the model would
    price every arrival as a permanent substitute.
    """
    if not SEASON_PATH.exists():
        raise SystemExit(f"no {SEASON_PATH} — run scripts/build_last_season.py first")
    loaded = json.loads(SEASON_PATH.read_text(encoding="utf-8"))["players"]

    archive: dict[str, Any] = {}
    if use_archive and ARCHIVE_PATH.exists():
        archive = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))["players"]

    points, minutes, goals, cells = {}, {}, {}, {}
    # In file order, NEVER by iterating a set. Python randomises string hashing
    # per process, so a set of player ids comes out in a different order every
    # run — and that order becomes the order of every dict built here, which
    # the hill climb walks when it looks for a swap. Two runs of identical code
    # returned seasons 35 points apart before this line was written down.
    ordered = list(loaded) + [i for i in archive if i not in loaded]
    # A DELIBERATE reordering, for measuring what an arbitrary one is worth.
    # The hill climb walks the candidates in whatever order they arrive and
    # stops at the first swap that helps, so the order decides which local
    # optimum it lands in. That is not a knob on the model — every order is
    # equally defensible — which makes the spread across orders the floor
    # below which no improvement here can be called demonstrated.
    if order_seed is not None:
        random.Random(order_seed).shuffle(ordered)
    for player_id in ordered:
        if player_id not in market:
            continue
        this_season = _matches_of(loaded.get(player_id, {}))
        last_season = _matches_of(archive.get(player_id, {}))
        if not this_season and not last_season:
            continue

        # This season, always the full calendar: a round he was left out of is
        # a real -1 and has to be on file as one.
        points[player_id] = {m: ABSENT for m in ALL_MATCHDAYS}
        minutes[player_id] = {m: 0 for m in ALL_MATCHDAYS}
        goals[player_id] = {m: 0 for m in ALL_MATCHDAYS}

        # Last season, only for those who were there — and then in full, for
        # the same reason.
        if last_season:
            for m in ALL_MATCHDAYS:
                points[player_id][m + ARCHIVE_OFFSET] = ABSENT
                minutes[player_id][m + ARCHIVE_OFFSET] = 0
                goals[player_id][m + ARCHIVE_OFFSET] = 0

        for match in last_season:
            m = int(match["round"]) + ARCHIVE_OFFSET
            points[player_id][m] = float(match["points"])
            minutes[player_id][m] = int(match.get("minutes") or 0)
            goals[player_id][m] = int(match.get("goals") or 0)

        clubs = defaultdict(int)
        for match in this_season:
            m = int(match["round"])
            points[player_id][m] = float(match["points"])
            minutes[player_id][m] = int(match.get("minutes") or 0)
            goals[player_id][m] = int(match.get("goals") or 0)
            if match.get("club"):
                clubs[match["club"]] += 1

        # Pooled with THIS season's club. A man who moved in the summer is
        # judged against the team he plays for now, not the one he left, and
        # falling back on last season's is only for someone yet to appear.
        if not clubs:
            for match in last_season:
                if match.get("club"):
                    clubs[match["club"]] += 1
        cells[player_id] = (
            max(clubs, key=clubs.get) if clubs else "",
            market[player_id].position.value,
        )
    return points, minutes, goals, cells


def pick_top_scorer(market, goals, minutes, upto):
    """§10.3(m) — the one-off bet, made from the trial rounds and never again.

    Goals per appearance times the appearances a season is likely to bring,
    shrunk hard, because four matchdays of finishing is mostly luck and the
    league's leading scorer is usually its best forward at its best club rather
    than whoever has three in August.
    """
    league = statistics.mean(
        [
            sum(g for m, g in by.items() if m < upto)
            / max(1, sum(1 for m, x in minutes[i].items() if m < upto and x > 0))
            for i, by in goals.items()
            if market[i].position is Position.FWD
        ]
        or [0.0]
    )
    best, ranked = None, []
    for player_id, by_matchday in goals.items():
        if market[player_id].position is not Position.FWD:
            continue
        played = sum(
            1 for m, x in minutes[player_id].items() if m < upto and x > 0
        )
        scored = sum(g for m, g in by_matchday.items() if m < upto)
        rate = (scored + league * 6) / (played + 6)
        ranked.append((rate * market[player_id].value, player_id))
    ranked.sort(reverse=True)
    return [player_id for _, player_id in ranked[:3]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="first", type=int, default=DEFAULT_FIRST)
    parser.add_argument("--draws", type=int, default=400)
    parser.add_argument(
        "--order-seed",
        type=int,
        default=None,
        help="shuffle the candidate order reproducibly, to measure the noise floor",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help=(
            "the Monte Carlo stream the squad search draws from. Changing it "
            "changes nothing about the model and only breaks near-ties a "
            "different way, so the spread across seeds is the noise floor: an "
            "improvement smaller than it has not been demonstrated"
        ),
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help=(
            "buy the squad on this season's opening matchdays alone, with no "
            "memory of the season before — what this report did until the "
            "archive was wired in, and the control for measuring what it is worth"
        ),
    )
    parser.add_argument(
        "--window",
        choices=("ration", "all"),
        default="ration",
        help=(
            "spread §6.9's six across the month, or spend them at the open. "
            "Rationing measured 1427 against 1398 on 2025/26, and lifted the "
            "worst round from 27 to 31: emptying the allowance on the first "
            "day leaves four rounds with no answer to an injury"
        ),
    )
    args = parser.parse_args()

    matchdays = list(range(args.first, LAST_MATCHDAY + 1))
    client = LigaRecordClient(timeout=60.0)
    market = {
        p.id: p.as_player() for position in Position for p in client.search(position)
    }
    points, minutes, goals, cells = load(
        market,
        use_archive=not args.no_archive,
        order_seed=args.order_seed,
    )
    market = {i: market[i] for i in points}
    rows = [
        {"id": p.id, "position": p.position, "value": p.value} for p in market.values()
    ]

    fixtures = OpenFootballClient().season_fixtures(ARCHIVE_TAG)
    coaches = coach_season(
        (f.model_dump() | {"round": f.round_number} for f in fixtures),
        rating_points=round(MEAN_MARK_POINTS),
    )
    coach_cells = {club: (club, "coach") for club in coaches}

    # The squad, chosen from the trial and nothing after it.
    opening_view = two_part_projection(points, minutes, cells, upto=args.first)
    returns, playing = {}, {}
    for player_id in market:
        # Over every round on file before the squad locks — last season's
        # included, for the players who have one. Four matchdays cannot tell a
        # starter from a man who happened to be fit in August.
        his = minutes.get(player_id, {})
        earlier = [m for m in his if m < args.first]
        played = [m for m in earlier if his[m] > 0]
        playing[player_id] = len(played) / max(1, len(earlier))
        returns[player_id] = (
            statistics.mean([points[player_id][m] for m in played]) if played else 2.5
        )
    bought = best_squad_under_budget(
        rows, {i: v * len(matchdays) for i, v in opening_view.items()}
    )
    if bought is None:
        raise SystemExit("no legal squad fits the budget")
    squad = improve_squad(
        bought["players"], market, returns, playing, budget=BASE_BUDGET, draws=args.draws, seed=args.seed
    )["players"]

    bet = pick_top_scorer(market, goals, minutes, args.first)

    print(f"THE SQUAD, chosen knowing matchdays 1-{args.first - 1}")
    order = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}
    for player_id in sorted(squad, key=lambda i: (order[market[i].position], -market[i].value)):
        player = market[player_id]
        print(
            f"  {player.position.value:<4}{player.name[:22]:<24}"
            f"{player.club[:14]:<15}{player.value / 1e6:>5.2f}M"
        )
    print(f"  {sum(market[i].value for i in squad):,} of {BASE_BUDGET:,}")
    print(f"\n  §10.3(m) bet on the top scorer: {market[bet[0]].name}")

    print()
    print(f"  {'md':>3} {'formation':<10}{'captain':<18}{'coach':<15}"
          f"{'pts':>5}{'coach':>7}   transfer")

    # §6.9's window, in matchdays. Round 16 of the competition, whichever
    # matchday that is under the reading being used, through matchday 24.
    window_opens = args.first + REOPENED_FIRST_ROUND - 1
    print()
    print(f"  §6.9 window: matchdays {window_opens}-{REOPENED_LAST_MATCHDAY}, "
          f"{REOPENED_MAX_SWAPS} swaps, and no weekly transfer inside it")


    held = list(squad)
    sold: set[str] = set()
    window_used = 0
    per_round, coach_total, transfers = [], 0.0, []
    for index, matchday in enumerate(matchdays):
        view = two_part_projection(points, minutes, cells, upto=matchday)
        coach_view = shrunk_projection(coaches, coach_cells, upto=matchday)

        note = ""
        in_window = window_opens <= matchday <= REOPENED_LAST_MATCHDAY
        if in_window and args.window == "ration":
            # Spread across the window rather than spent on the first day.
            # §6.9 gives six for the month and §6.8 gives nothing else, so
            # emptying the allowance at the open leaves four rounds with no
            # answer to an injury. Rationed, each round may take its share of
            # what is left.
            left = REOPENED_MAX_SWAPS - window_used
            rounds_left = REOPENED_LAST_MATCHDAY - matchday + 1
            allowance = -(-left // rounds_left) if rounds_left else left
            taken = 0
            while taken < allowance:
                swap = best_transfer(
                    held, market, view,
                    budget=BASE_BUDGET,
                    rounds_left=len(matchdays) - index,
                    sold=sold,
                )
                if swap is None:
                    break
                out_id, in_id, _ = swap
                held[held.index(out_id)] = in_id
                sold.add(out_id)
                transfers.append((matchday, out_id, in_id))
                taken += 1
            window_used += taken
            note = f"window: {taken} of {left} left" if taken else "window: held"
        elif matchday == window_opens:
            # §6.9 — the market reopens and up to six players change hands at
            # once. Six for the whole window, not six a round, and §6.8's
            # ordinary transfer is switched off for its duration. Taken
            # together rather than one a week, which is the point: six moves
            # can pay for each other, and one a week cannot.
            playing_now, returns_now = two_part_projection(
                points, minutes, cells, upto=matchday, parts=True
            )
            rebuilt = improve_squad(
                held,
                market,
                returns_now,
                playing_now,
                budget=BASE_BUDGET,
                draws=args.draws, seed=args.seed,
                max_swaps=REOPENED_MAX_SWAPS,
                candidates=[i for i in market if i not in sold],
            )["players"]
            # Each departure paired with a DIFFERENT arrival. The generator
            # used to be rebuilt inside the loop, so it restarted at the top
            # every time and six defenders leaving were all recorded as
            # replaced by the same man. Only the count is read today, which is
            # why it went unnoticed; the rows were wrong all the same, and §6.9
            # can move six at once.
            arriving = [i for i in rebuilt if i not in held]
            for out_id in [i for i in held if i not in rebuilt]:
                in_id = next(
                    i
                    for i in arriving
                    if market[i].position is market[out_id].position
                )
                arriving.remove(in_id)
                sold.add(out_id)
                transfers.append((matchday, out_id, in_id))
            note = f"WINDOW: {len(set(held) - set(rebuilt))} swaps"
            held = list(rebuilt)
        elif not in_window:
            swap = best_transfer(
                held,
                market,
                view,
                budget=BASE_BUDGET,
                rounds_left=len(matchdays) - index,
                sold=sold,
            )
            if swap is not None:
                out_id, in_id, _ = swap
                held[held.index(out_id)] = in_id
                sold.add(out_id)
                transfers.append((matchday, out_id, in_id))
                note = f"{market[out_id].name[:16]} -> {market[in_id].name[:16]}"
        else:
            note = "(February — no transfer, §6.8)"

        played = play_round(
            held, market, points, matchday, forecast=view, knows_availability=True
        )
        club, earned = pick_coach(coaches, matchday, forecast=coach_view)
        total = played["points"] + earned
        per_round.append(total)
        coach_total += earned

        print(
            f"  {matchday:>3} {played['formation']:<10}"
            f"{market[played['captain']].name[:17]:<18}{(club or '-')[:14]:<15}"
            f"{played['points']:>5.0f}{earned:>7.0f}   {note}"
        )
        print(
            "      "
            + "  ".join(market[i].name.split()[-1][:11] for i in played["starters"])
        )

    # THIS season's goals, and only this season's. The archive sits on matchdays
    # at or below zero, and summing the lot settles the golden boot across two
    # years: it crowned Pavlidis with 41, paid out a bet that had actually lost
    # to Luis Suárez, and quietly added twenty points that were never earned.
    scored = {i: sum(g for m, g in by.items() if m > 0) for i, by in goals.items()}
    podium = sorted(scored, key=lambda i: -scored[i])[:3]
    bonus = 0
    for prize, place in zip((GOLDEN_BOOT_BONUS, SILVER_BOOT_BONUS, BRONZE_BOOT_BONUS), podium):
        if bet[0] == place:
            bonus = prize
    print()
    print(f"  top scorers: " + ", ".join(
        f"{market[i].name} ({scored[i]})" for i in podium
    ))
    print(f"  the bet was {market[bet[0]].name} — worth {bonus:+d}")

    print()
    print(f"  players            {sum(per_round) - coach_total:>7.0f}")
    print(f"  coach              {coach_total:>7.0f}")
    print(f"  top-scorer bet     {bonus:>7d}")
    print(f"  SEASON             {sum(per_round) + bonus:>7.0f}"
          f"   ({statistics.mean(per_round):.1f} a round over {len(matchdays)})")
    print(f"  {len(transfers)} transfers, best round {max(per_round):.0f}, "
          f"worst {min(per_round):.0f}")


if __name__ == "__main__":
    main()
