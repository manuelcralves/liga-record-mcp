"""Play last season, and find out what the numbers on the scoreboard mean.

A manager scoring five points a round has no idea whether that is good. Liga
Record shows a rank and never a scale — no par, no ceiling, nothing to say
whether a bad week was bad luck or bad selection. This builds the scale.

Six seasons are played through the reconstruction, from the unreachable to the
careless, and any real score falls somewhere among them:

    ceiling                the best XI in the league, every round, knowing the
                           results in advance. Nobody could reach it.
    best squad, hindsight  the best 23 money can buy, perfectly selected
    best squad, blind      the same 23, selected each round from what was known
                           at the time, with §11 substitutions where it went wrong
    your squad, blind      your actual 23, had you owned them last season
    a random squad, blind  the luck of the draw, over many draws
    the priciest squad     spend it all on the dearest legal 23

The gap between "hindsight" and "blind" on the same squad is the price of not
knowing the future, and it is the number most worth looking at: it is the part
of a season no amount of skill recovers.

WHAT IS BEING ASSUMED. Last season's prices are gone — Liga Record publishes
none — so the budget is enforced at TODAY's prices. That changes the question
in a defensible direction: not "what could you have bought last August" but
"what are last season's performances worth at what they cost now", which is the
question actually facing anyone building a squad this week. It is conservative,
too, since a player who did well is dearer now for having done it.

Transfers are not simulated. Every squad here is bought once and never
changed, so these are floors for anyone who manages actively.

    python scripts/simulate_last_season.py
    python scripts/simulate_last_season.py --draws 400
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

from liga_record_mcp.models import (  # noqa: E402
    BASE_BUDGET,
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    RECORD_ROUNDS,
    SQUAD_QUOTA,
    Player,
    Position,
    Selection,
    Squad,
)
from liga_record_mcp.backtest import (  # noqa: E402
    appeared,
    pick_coach,
    replay,
    shrunk_projection,
)
from liga_record_mcp.optimise import best_eleven, best_squad_under_budget  # noqa: E402
from liga_record_mcp.rules import simulate_autosubs, validate_selection  # noqa: E402
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    ManualSquadSource,
    OpenFootballClient,
    SiteError,
)
from liga_record_mcp.stats import (  # noqa: E402
    MEAN_MARK_POINTS,
    PRIOR_STRENGTH,
    UNUSED_PENALTY,
    coach_points,
)

SEASON_PATH = ROOT / "data" / "last-season.json"

#: openfootball's name for the season being replayed. Written out rather than
#: derived from zerozero's numbering, which is a different site's bookkeeping.
ARCHIVE_TAG = "2025-26"
SQUAD_PATH = ROOT / "data" / "squad.yaml"

#: The matchdays that count. Settled by watching the site rather than by
#: reading the rules, which contradict themselves: the squad locks at matchday
#: 5 and so does everything else, so 5 is where scoring starts and 30 is the
#: season. §16.4's own range is a week shorter and is what the national prize
#: table uses — see models.py. Playing all thirty-four would inflate every
#: total here and quietly answer a third question.
ROUNDS = range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1)

#: Everything that happened, scoring or not. A manager standing at round one
#: has already watched five matchdays of football, and §19 says they were
#: scored exactly like the real thing — so they are evidence even though they
#: are not points.
ALL_MATCHDAYS = range(1, LAST_MATCHDAY + 1)


def coach_history(archive_tag: str) -> dict[str, dict[int, float]]:
    """What each club's coach scored each matchday, under §14.3.

    A coach's points depend only on his club's result, so a round can be scored
    without knowing his name — which is fortunate, because no archive records
    who was in charge on a given weekend.

    Two terms cannot be had. §14.1's editorial mark is unpublished, exactly as
    for players, and is credited to everyone at the market average: it shifts
    every club by the same amount and so changes no decision, only the totals.
    §14.3(d)'s goals off the bench, and §14.3(e)'s sendings-off, are not in a
    results archive at all. Both omissions make a coach's round smaller than it
    was, never larger.
    """
    fixtures = OpenFootballClient().season_fixtures(archive_tag)
    history: dict[str, dict[int, float]] = defaultdict(dict)
    for fixture in fixtures:
        halves = (fixture.home_half, fixture.away_half)
        for club, scored, conceded, ours, theirs in (
            (fixture.home, fixture.home_goals, fixture.away_goals, *halves),
            (fixture.away, fixture.away_goals, fixture.home_goals, *halves[::-1]),
        ):
            trailed = max(0, theirs - ours) if ours is not None else 0
            history[club][fixture.round_number] = float(
                coach_points(
                    scored=scored,
                    conceded=conceded,
                    trailed_by=trailed,
                    rating_points=round(MEAN_MARK_POINTS),
                )
            )
    return dict(history)


def load_season() -> tuple[dict[str, dict[int, float]], dict[str, str]]:
    """Per-round points for everyone, and the club each played for."""
    if not SEASON_PATH.exists():
        raise SystemExit(f"no {SEASON_PATH} — run scripts/build_last_season.py first")
    loaded = json.loads(SEASON_PATH.read_text(encoding="utf-8"))["players"]

    scores: dict[str, dict[int, float]] = {}
    clubs: dict[str, str] = {}
    for player_id, player in loaded.items():
        if not player["matches"]:
            continue
        scores[player_id] = {
            matchday: float(UNUSED_PENALTY) for matchday in ALL_MATCHDAYS
        }
        played: dict[str, int] = defaultdict(int)
        for match in player["matches"]:
            scores[player_id][int(match["round"])] = float(match["points"])
            # A round filled in for a week he was not in the squad has no club
            # on it, because there was no match to be at.
            if match.get("club"):
                played[match["club"]] += 1
        clubs[player_id] = max(played, key=played.get) if played else ""
    return scores, clubs


def appeared(player_id: str, round_number: int, season: dict) -> bool:
    """Whether a player took the field, which is what §11 substitutes on."""
    return season.get(player_id, {}).get(round_number, float(UNUSED_PENALTY)) != float(
        UNUSED_PENALTY
    )


def projections(
    scores: dict[str, dict[int, float]], clubs: dict[str, str], positions: dict[str, str]
) -> dict[int, dict[str, float]]:
    """What each player was worth before each round, knowing only earlier ones.

    A thin wrapper over the engine, so the shrinkage lives in one place. It was
    written twice once, and two copies of a replay quietly disagreeing is worse
    than either being wrong.
    """
    cells = {i: (clubs[i], positions.get(i, "")) for i in scores}
    return {
        matchday: shrunk_projection(scores, cells, upto=matchday) for matchday in ROUNDS
    }


def play(
    squad_ids: list[str],
    market: dict[str, Player],
    scores: dict[str, dict[int, float]],
    *,
    forecast: dict[int, dict[str, float]] | None = None,
    knows_availability: bool = False,
    coaches: dict[str, dict[int, float]] | None = None,
    coach_forecast: dict[int, dict[str, float]] | None = None,
) -> dict:
    """Play a fixed squad through the season. See `backtest.replay`."""
    return replay(
        squad_ids,
        market,
        scores,
        ROUNDS,
        forecasts=forecast,
        knows_availability=knows_availability,
        coaches=coaches,
        coach_forecasts=coach_forecast,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=200, help="random squads to try")
    parser.add_argument("--seed", type=int, default=20260820)
    args = parser.parse_args()

    scores, clubs = load_season()
    market_client = LigaRecordClient(timeout=60.0)
    market = {
        p.id: p.as_player()
        for position in Position
        for p in market_client.search(position)
    }
    pool = {i: market[i] for i in scores if i in market}
    positions = {i: pool[i].position.value for i in pool}
    scores = {i: s for i, s in scores.items() if i in pool}
    clubs = {i: c for i, c in clubs.items() if i in pool}
    rows = [{"id": p.id, "position": p.position, "value": p.value} for p in pool.values()]

    totals = {i: sum(scores[i].values()) for i in scores}
    print(f"{len(pool)} players reconstructed and still on the market")
    print(
        f"budget {BASE_BUDGET:,} at today's prices; no transfers; "
        f"{RECORD_ROUNDS} rounds, matchdays {FIRST_SCORING_MATCHDAY}-{LAST_MATCHDAY}"
    )

    forecast = projections(scores, clubs, positions)

    # §14.4 adds a coach to every round, for nothing. Leaving him out was the
    # largest thing missing from these totals.
    try:
        coaches = coach_history(ARCHIVE_TAG)
    except SiteError as exc:
        raise SystemExit(f"could not read the {ARCHIVE_TAG} calendar: {exc}")
    coach_cells = {club: (club, "coach") for club in coaches}
    coach_forecast = {
        matchday: shrunk_projection(coaches, coach_cells, upto=matchday)
        for matchday in ROUNDS
    }
    blind_coach = dict(coaches=coaches, coach_forecast=coach_forecast)
    sharp_coach = dict(coaches=coaches)

    # 1. The ceiling: the best XI in the league every round, knowing the results.
    ceiling = []
    for round_number in ROUNDS:
        actual = {i: scores[i][round_number] for i in scores}
        sheet = best_eleven(rows, actual)
        # The best coach of the round too, or this one line would be the only
        # benchmark on the table missing §14.4 — and it is the line everything
        # else is measured against.
        _, coached = pick_coach(coaches, round_number)
        ceiling.append(sheet["points"] + coached)

    # 2 and 3. The best squad money can buy, played both ways.
    bought = best_squad_under_budget(rows, totals)
    if bought is None:
        raise SystemExit("no legal squad fits the budget")
    best_hindsight = play(bought["players"], pool, scores, **sharp_coach)
    best_blind = play(
        bought["players"], pool, scores, forecast=forecast, knows_availability=True,
        **blind_coach,
    )
    best_unaware = play(bought["players"], pool, scores, forecast=forecast, **blind_coach)

    # 4. Manuel's own 23, had he owned them last season.
    mine_players = ManualSquadSource(SQUAD_PATH).load().squad.players
    mine = [p.id for p in mine_players]
    absent = [p for p in mine_players if p.id not in pool]
    for player in absent:
        # Promoted from the second division, or abroad. He would have been in
        # the squad and scored -1 every round of §10.3(i); leaving him out
        # would silently shrink the team to twenty-two.
        pool[player.id] = player
        scores[player.id] = {m: float(UNUSED_PENALTY) for m in ALL_MATCHDAYS}
        clubs[player.id] = player.club
        positions[player.id] = player.position.value
        for round_number in ROUNDS:
            forecast[round_number][player.id] = float(UNUSED_PENALTY)
    yours_hindsight = play(mine, pool, scores, **sharp_coach)
    yours_blind = play(
        mine, pool, scores, forecast=forecast, knows_availability=True, **blind_coach
    )

    # 5. The luck of the draw.
    rng = random.Random(args.seed)
    by_position: dict[Position, list[str]] = defaultdict(list)
    for player in pool.values():
        by_position[player.position].append(player.id)
    drawn: list[float] = []
    for _ in range(args.draws):
        picked: list[str] = []
        for position, count in SQUAD_QUOTA.items():
            picked.extend(rng.sample(by_position[position], count))
        if sum(pool[i].value for i in picked) > BASE_BUDGET:
            continue
        drawn.append(
            play(
                picked, pool, scores, forecast=forecast, knows_availability=True,
                **blind_coach,
            )["points"]
        )

    # 6. Spend it all on the dearest legal squad.
    dearest = best_squad_under_budget(rows, {i: float(pool[i].value) for i in pool})
    priciest = play(
        dearest["players"], pool, scores, forecast=forecast, knows_availability=True,
        **blind_coach,
    )

    print()
    print(f"  {'season':<34}{'points':>8}{'/round':>9}{'best':>7}{'worst':>7}")
    def show(label, result):
        print(
            f"  {label:<34}{result['points']:>8.0f}{result['per_round']:>9.1f}"
            f"{result['best']:>7.0f}{result['worst']:>7.0f}"
        )

    show(
        "the ceiling, knowing everything",
        {
            "points": sum(ceiling),
            "per_round": statistics.mean(ceiling),
            "best": max(ceiling),
            "worst": min(ceiling),
        },
    )
    show("best squad, perfectly selected", best_hindsight)
    show("best squad, selected blind", best_blind)
    show("best squad, blind to injuries too", best_unaware)
    show("YOUR squad, perfectly selected", yours_hindsight)
    show("YOUR squad, selected blind", yours_blind)
    show("the dearest legal squad, blind", priciest)
    if drawn:
        drawn.sort()
        show(
            "a squad drawn at random, blind",
            {
                "points": statistics.median(drawn),
                "per_round": statistics.median(drawn) / len(ROUNDS),
                "best": drawn[-1] / len(ROUNDS),
                "worst": drawn[0] / len(ROUNDS),
            },
        )
        print(
            f"  {'':<34}{'':>8}{'':>9}"
            f"  (median of {len(drawn)} draws; best and worst draw, per round)"
        )
    if absent:
        print()
        print(
            f"  {len(absent)} of your 23 were not in the top flight last "
            f"season and score -1 a round throughout: " + ", ".join(player.name for player in absent)
        )

    print()
    gap = best_hindsight["points"] - best_blind["points"]
    print(
        f"  Knowing the results in advance was worth {gap:.0f} points to the same "
        f"squad — {gap / len(ROUNDS):.1f} a round, {gap / best_hindsight['points']:.0%} "
        "of everything it scored."
    )
    print(
        f"  A manager who did not read the team news would have started an "
        f"injured player {best_unaware['substitutions']} times, and §11 rescued "
        f"him every time — worth "
        f"{best_blind['points'] - best_unaware['points']:.0f} points over the season."
    )

    if yours_blind:
        spread = sorted(drawn)
        beat = sum(1 for d in spread if d < yours_blind["points"])
        print(
            f"  Your 23, blindly managed, would have scored {yours_blind['points']:.0f}"
            f" — {best_blind['points'] - yours_blind['points']:.0f} behind the best "
            f"23 buyable, and above all {len(spread)} random squads, whose best "
            f"draw reached {spread[-1]:.0f}."
        )
        print(
            "  Note that your 23 were chosen this summer, by someone who watched "
            "last season. Scoring well on it is partly circular and should not be "
            "read as a forecast."
        )

    # The optimiser must produce sheets the real validator accepts. If these
    # two ever disagree, the numbers above are answers to a different game.
    sheet = best_eleven(rows, {i: scores[i][1] for i in scores})
    check = validate_selection(
        Squad(
            team_id=0,
            team_name="check",
            players=tuple(
                pool[i] for i in bought["players"]
            ),
        ),
        Selection(
            starters=tuple(best_eleven(
                [{"id": pool[i].id, "position": pool[i].position, "value": pool[i].value}
                 for i in bought["players"]],
                {i: scores[i][1] for i in bought["players"]},
            )["starters"]),
            bench=tuple(best_eleven(
                [{"id": pool[i].id, "position": pool[i].position, "value": pool[i].value}
                 for i in bought["players"]],
                {i: scores[i][1] for i in bought["players"]},
            )["bench"]),
            captain=None,
            coach_id="simulated",
        ),
    )
    print(f"\n  team sheets accepted by §6.13: {check.is_valid} ({sheet['formation']} on round 1)")


if __name__ == "__main__":
    main()
