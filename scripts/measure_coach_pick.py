"""Is the coach worth choosing every round, on that round's match?

Coaches are free and may be changed every round (§6.13–6.16), and what one
scores rides on his club's result. The page never chose one per round: it
ranked the eighteen once, on last season's average, and on 18/09/2026 the right
call for round 7 — Sporting at home to Arouca over FC Porto against Benfica —
was worked out by hand. The "86 points between the best and the worst" quoted
around the project is a ceiling, not a strategy, and no script reproduced it.

THE REPLAY. Each season is played from matchday 6 to 34 with openfootball's
results, the two seasons before it standing in for the archive, as the live
path sums two. Before each round the clubs' strengths are fitted only on
matches played before that round's first kickoff — dated, not numbered, since
a postponed match keeps its round's number and would otherwise leak — and four
rules pick a coach:

    pelo jogo   the best expected points on this round's match (the proposal)
    a epoca     the page's rule: last season's best average, fixed all season
    pela forma  the best average so far this season, no opponent
    teto        the best of the round, knowing the results

Each is scored with §14.3 on the real result. Everyone is credited the same
editorial mark, which no archive has; it moves the totals and none of the
differences. Goals off the bench and sendings-off are not in a results archive
either, and both are missing for every rule alike.

THE SECOND QUESTION, answered and not acted on: do the expected points predict
a coach's round better than `stats.project_coach`, which the ledger files?
Changing the ledger would change what it records and is a plan of its own.

    python scripts/measure_coach_pick.py
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.final_table import coach_values, strengths  # noqa: E402
from liga_record_mcp.models import (  # noqa: E402
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    ClubRecord,
)
from liga_record_mcp.source.history import OpenFootballClient  # noqa: E402
from liga_record_mcp.stats import (  # noqa: E402
    MEAN_MARK_POINTS,
    coach_points,
    project_coach,
)

#: The replayed season, the two before it that stand in for the archive, and
#: whether it is one of the two the adoption bar was set on before running.
SEASONS = (
    ("2024-25", ("2022-23", "2023-24"), True),
    ("2025-26", ("2023-24", "2024-25"), True),
    ("2023-24", ("2021-22", "2022-23"), False),
)

ROUNDS = range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1)
MARK = round(MEAN_MARK_POINTS)
RULES = ("pelo jogo", "a epoca", "pela forma", "teto")

DRAWS = 5000
SEED = 20260922


def coach_rounds(fixtures) -> dict[str, dict[int, int]]:
    """What each club's coach scored each matchday, §14.3 with the mark credited."""
    out: dict[str, dict[int, int]] = defaultdict(dict)
    for match in fixtures:
        halves = (match.home_half, match.away_half)
        for club, scored, conceded, ours, theirs in (
            (match.home, match.home_goals, match.away_goals, *halves),
            (match.away, match.away_goals, match.home_goals, *halves[::-1]),
        ):
            trailed = max(0, theirs - ours) if ours is not None and theirs is not None else 0
            out[club][match.round_number] = coach_points(
                scored=scored, conceded=conceded, trailed_by=trailed, rating_points=MARK
            )
    return dict(out)


def club_totals(fixtures) -> dict[str, dict[str, int]]:
    """Played, scored and conceded per club over these matches."""
    totals: dict[str, dict[str, int]] = {}
    for match in fixtures:
        for club, scored, conceded in (
            (match.home, match.home_goals, match.away_goals),
            (match.away, match.away_goals, match.home_goals),
        ):
            row = totals.setdefault(club, {"played": 0, "goals_for": 0, "goals_against": 0})
            row["played"] += 1
            row["goals_for"] += scored
            row["goals_against"] += conceded
    return totals


def as_records(totals) -> dict[str, ClubRecord]:
    return {
        club: ClubRecord(
            club=club,
            matches=row["played"],
            goals_for=row["goals_for"],
            goals_against=row["goals_against"],
        )
        for club, row in totals.items()
    }


def correlation(pairs: list[tuple[float, float]]) -> float:
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0


def replay(client: OpenFootballClient, season: str, priors: tuple[str, ...]) -> dict:
    """One season: each rule's points by round, and each predictor's pairs."""
    fixtures = client.season_fixtures(season)
    before = [match for tag in priors for match in client.season_fixtures(tag)]
    actual = coach_rounds(fixtures)
    archive = as_records(club_totals(before))
    league_against = statistics.fmean(r.goals_against_per_match for r in archive.values())
    league_for = statistics.fmean(r.goals_for_per_match for r in archive.values())

    # The page's rule: the eighteen ranked on last season's average, once. A
    # club promoted this season has no last season and cannot be the pick.
    last = coach_rounds(client.season_fixtures(priors[-1]))
    present = {m.home for m in fixtures} | {m.away for m in fixtures}
    fixed = max(
        sorted(club for club in present if club in last),
        key=lambda club: statistics.fmean(last[club].values()),
    )

    scored: dict[str, dict[int, float]] = {rule: {} for rule in RULES}
    average: dict[int, float] = {}
    pairs: dict[str, list[tuple[float, float]]] = {
        "pelo jogo": [], "project_coach": [], "pela forma": []
    }
    differ = 0
    for number in ROUNDS:
        this_round = [m for m in fixtures if m.round_number == number]
        if not this_round:
            continue
        kickoff = min(m.date for m in this_round)
        played = [m for m in fixtures if m.date < kickoff]
        table = [
            {"club": club, **row} for club, row in sorted(club_totals(played).items())
        ]
        strength = strengths(archive, table)
        values = coach_values([(m.home, m.away) for m in this_round], strength)
        playing = sorted(values)

        # Form: this season's coach points so far, round by round as played.
        so_far: dict[str, list[int]] = defaultdict(list)
        for match in played:
            for club in (match.home, match.away):
                so_far[club].append(actual[club][match.round_number])
        form = {
            club: statistics.fmean(so_far[club]) if so_far[club] else float(MARK)
            for club in playing
        }

        picks = {
            "pelo jogo": max(playing, key=values.get),
            "a epoca": fixed,
            "pela forma": max(playing, key=form.get),
        }
        truth = {club: actual[club][number] for club in playing}
        for rule, club in picks.items():
            scored[rule][number] = truth.get(club, 0)
        scored["teto"][number] = max(truth.values())
        average[number] = statistics.fmean(truth.values())
        differ += picks["pelo jogo"] != picks["a epoca"]

        # The second question: each predictor against every coach's round.
        rate_so_far = [p for points in so_far.values() for p in points]
        baseline = statistics.fmean(rate_so_far) if rate_so_far else float(MARK)
        for club in playing:
            pairs["pelo jogo"].append((values[club] + MARK, truth[club]))
            pairs["pela forma"].append((form[club], truth[club]))
            projected = project_coach(
                sum(so_far[club]),
                len(so_far[club]),
                archive.get(club),
                baseline,
                league_against,
                league_for,
            )["projected_rate"]
            pairs["project_coach"].append((projected, truth[club]))

    return {
        "scored": scored,
        "average": average,
        "fixed": fixed,
        "differ": differ,
        "pairs": pairs,
    }


def interval(differences: list[float], draws: int, seed: int) -> tuple[float, float]:
    """90% interval of the season total of a per-round difference, resampling rounds."""
    rng = random.Random(seed)
    n = len(differences)
    totals = sorted(
        sum(differences[rng.randrange(n)] for _ in range(n)) for _ in range(draws)
    )
    return totals[int(0.05 * draws)], totals[int(0.95 * draws) - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draws", type=int, default=DRAWS)
    args = parser.parse_args()

    client = OpenFootballClient(timeout=60.0)
    verdicts = []
    for season, priors, in_bar in SEASONS:
        found = replay(client, season, priors)
        scored = found["scored"]
        rounds = sorted(scored["pelo jogo"])
        print()
        print(
            f"{season} ({'na barra' if in_bar else 'a mais, fora da barra'}), "
            f"jornadas {rounds[0]}-{rounds[-1]}, memoria {' + '.join(priors)}; "
            f"a epoca escolhe {found['fixed']}"
        )
        for rule in RULES:
            total = sum(scored[rule].values())
            print(f"    {rule:<12}{total:>7.0f}   {total / len(rounds):>5.2f} por jornada")
        print(
            f"    {'media dos 18':<12}{sum(found['average'].values()):>7.0f}   "
            f"(o que vale escolher ao calhas)"
        )
        for other in ("a epoca", "pela forma"):
            diffs = [scored["pelo jogo"][r] - scored[other][r] for r in rounds]
            low, high = interval(diffs, args.draws, SEED)
            better = sum(1 for d in diffs if d > 0)
            worse = sum(1 for d in diffs if d < 0)
            print(
                f"    pelo jogo - {other:<11}{sum(diffs):>+6.0f}   90%: {low:+.0f} a "
                f"{high:+.0f}; melhor em {better}, pior em {worse} jornadas"
            )
        print(f"    pelo jogo escolhe outro treinador que a epoca em {found['differ']} jornadas")
        print("    quem preve melhor a jornada de um treinador (todos os 18, todas as jornadas):")
        for name, pairs in found["pairs"].items():
            error = statistics.fmean(abs(p - t) for p, t in pairs)
            print(
                f"      {name:<14} correlacao {correlation(pairs):.3f}   "
                f"erro medio {error:.2f}   n={len(pairs)}"
            )
        if in_bar:
            verdicts.append(sum(scored["pelo jogo"][r] - scored["a epoca"][r] for r in rounds))

    print()
    passed = all(v > 0 for v in verdicts)
    print(
        "BARRA (pelo jogo acima de a epoca nas duas epocas): "
        + ", ".join(f"{v:+.0f}" for v in verdicts)
        + (" — PASSA" if passed else " — FALHA")
    )


if __name__ == "__main__":
    main()
