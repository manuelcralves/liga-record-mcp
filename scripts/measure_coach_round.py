"""Does a leftover that walks with the result predict a coach's round better?

The model paid every coach 2.59 flat on top of §14.3 — the PLAYERS' average
mark, borrowed. `measure_coach_mark.py` showed the leftover is 3.72 and walks
with the result: +5.00 on a win, +3.67 on a draw, +2.50 on a loss, the win and
loss intervals not touching. This asks the question that decides whether the
model changes: fitted on one round and measured on another it has never seen,
is the coach's round LESS WRONG?

IT IS NOT A FREE CHANGE, which is why it needs a bar. The model already knows
the chance of each result, so a favourite is credited more of the leftover than
an underdog — about 1.2 points between them, the size of the §14.3 differences
that decide today's pick. It reorders the eighteen.

THE BAR, fixed before running: the leftover by result must beat the flat
constant on mean absolute error IN BOTH FOLDS and pooled, and the coach it
names must not have scored less. One fold is twelve matches; winning one and
losing the other is noise.

WHAT THIS CANNOT DO. There are two emailed rounds, so there are two folds and
two picks — the pick is a guard, not evidence. And `measure_coach_pick.py`
cannot stand in for it: that harness credits every coach the same mark, "which
moves the totals and none of the differences", and a mark that walks with the
result is exactly the case where that stops being true.

    python scripts/measure_coach_round.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.coaches import coach_points_by_club  # noqa: E402
from liga_record_mcp.final_table import coach_values, strengths  # noqa: E402
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, load_official_rounds  # noqa: E402
from liga_record_mcp.source.history import OpenFootballClient  # noqa: E402
from liga_record_mcp.stats import (  # noqa: E402
    COACH_BEYOND_POINTS,
    COACH_BEYOND_RESULT,
    MEAN_MARK_POINTS,
    coach_points,
    league_table,
)

DATA = ROOT / "data"

#: The model as it stood until 23/09/2026: the PLAYERS' average mark, paid to
#: every coach whatever the result. Kept as the arm the other two answer to.
FLAT = {"win": MEAN_MARK_POINTS, "draw": MEAN_MARK_POINTS, "loss": MEAN_MARK_POINTS}

RESULTS = ("win", "draw", "loss")

#: The three models compared, and the middle one is the control: without it,
#: a shape that only fixed the level would look like a shape that works.
ARMS = (
    ("flat", "2.59, ate 23/09"),
    ("level", "so o nivel, hoje"),
    ("beyond", "por resultado"),
)


def kickoff(fixtures, round_number: int):
    """When a round started, by the calendar. None if it says nothing."""
    starts = [
        f.starts_at for f in fixtures if f.round_number == round_number and f.starts_at
    ]
    return min(starts) if starts else None


def known_before(fixtures, when) -> list:
    """The matches already played when a round kicked off.

    Dated, not numbered: a match postponed out of round 3 keeps the number 3,
    and counting it as known would hand the fit a result nobody had yet.
    """
    return [f for f in fixtures if f.played and f.starts_at and f.starts_at < when]


def week(fixtures, round_number: int) -> list[tuple[str, str]]:
    return [(f.home, f.away) for f in fixtures if f.round_number == round_number]


def outcome(scored: int, conceded: int) -> str:
    return "win" if scored > conceded else "draw" if scored == conceded else "loss"


def leftovers(doc, fixtures, round_number: int) -> list[dict]:
    """What each coach scored beyond §14.3 that round, and after what result."""
    rows = []
    for fixture in fixtures:
        if fixture.round_number != round_number or not fixture.played:
            continue
        sides = (
            (fixture.home, fixture.home_goals, fixture.away_goals),
            (fixture.away, fixture.away_goals, fixture.home_goals),
        )
        for club, scored, conceded in sides:
            points = coach_points_by_club(doc, club)
            if points is None:
                continue
            rows.append(
                {
                    "club": club,
                    "points": points,
                    "left": points - coach_points(scored=scored, conceded=conceded),
                    "result": outcome(scored, conceded),
                }
            )
    return rows


def fit(rows: list[dict]) -> dict[str, float]:
    """The leftover's mean for each result.

    A result nobody had that round keeps the flat mark, which is the honest
    stand-in: it is what the model would have paid without this measurement.
    """
    fitted = {}
    for result in RESULTS:
        theirs = [row["left"] for row in rows if row["result"] == result]
        fitted[result] = mean(theirs) if theirs else COACH_BEYOND_POINTS
    return fitted


def level(rows: list[dict]) -> dict[str, float]:
    """The leftover's mean, one number for every result — the control.

    Without it the comparison is rigged, and in the same way the transfer
    lookahead was once rigged: most of what a fitted shape wins is really the
    level, because 2.59 is 1.1 points short of what a coach averages. This is
    the level put right and NOTHING else, so whatever the shape beats it by is
    the shape's own.
    """
    average = mean(row["left"] for row in rows) if rows else COACH_BEYOND_POINTS
    return {result: average for result in RESULTS}


def expected(fixtures, records, round_number: int, beyond) -> dict[str, float]:
    """Each club's coach priced as the page would have priced him that week.

    The clubs' strengths are fitted only on matches that had kicked off, so the
    round being measured is not inside them. It matters twice here: a better
    idea of who wins is exactly what the leftover is weighted by, so a leak
    would flatter the shape this is testing.
    """
    when = kickoff(fixtures, round_number)
    table = league_table(known_before(fixtures, when)) if when else []
    return coach_values(
        week(fixtures, round_number), strengths(records, table), beyond=beyond
    )


def errors(doc, prices: dict[str, float]) -> list[float]:
    """One signed error per coach: what he was priced at, minus what he did."""
    out = []
    for club in sorted(prices):
        points = coach_points_by_club(doc, club)
        if points is not None:
            out.append(prices[club] - points)
    return out


def pick(prices: dict[str, float]) -> str | None:
    """The club this model would name: the best price, the name breaking ties."""
    if not prices:
        return None
    return sorted(prices, key=lambda club: (-prices[club], club))[0]


def main() -> int:
    rounds = load_official_rounds(
        DATA / "pontuacoes", first_round=FIRST_SCORING_MATCHDAY
    )
    emailed = {n: doc for n, doc in rounds.items() if doc.get("treinadores")}
    if len(emailed) < 2:
        raise SystemExit(
            f"{len(emailed)} jornada(s) emailada(s) com treinadores: "
            "sem duas nao ha dobra nenhuma"
        )
    fixtures = LigaRecordClient(timeout=60.0).fixtures()
    records = OpenFootballClient(timeout=60.0).club_records()

    every = {n: leftovers(doc, fixtures, n) for n, doc in sorted(emailed.items())}
    for number, rows in sorted(every.items()):
        if not rows:
            raise SystemExit(f"jornada {number}: nenhum treinador casou com o calendario")

    lines = sum(len(rows) for rows in every.values())
    print(f"jornadas {sorted(emailed)}, {lines} treinador-jornadas")
    whole = fit([row for _, rows in sorted(every.items()) for row in rows])
    print(
        "  o que sobra ao §14.3, tudo junto: "
        + ", ".join(f"{name} {whole[name]:+.2f}" for name in RESULTS)
        + f" (o modelo paga {COACH_BEYOND_POINTS:.2f} a todos, desde 23/09)"
    )

    pooled: dict[str, list[float]] = {"flat": [], "level": [], "beyond": []}
    folds = []
    for number in sorted(emailed):
        rest = [n for n in sorted(emailed) if n != number]
        others = [row for other in rest for row in every[other]]
        fitted, flattened = fit(others), level(others)
        fold = {"round": number, "fitted": fitted, "level": flattened}
        for name, beyond in (("flat", FLAT), ("level", flattened), ("beyond", fitted)):
            prices = expected(fixtures, records, number, beyond)
            signed = errors(emailed[number], prices)
            pooled[name].extend(signed)
            named = pick(prices)
            fold[name] = {
                "mae": mean(abs(e) for e in signed),
                "bias": mean(signed),
                "pick": named,
                "realised": coach_points_by_club(emailed[number], named or ""),
                "rows": len(signed),
            }
        folds.append(fold)

        said = ", ".join(str(n) for n in rest)
        print(f"\njornada {number}, com o ajuste da jornada {said}:")
        print(
            "  ajuste usado: "
            + ", ".join(f"{name} {fitted[name]:+.2f}" for name in RESULTS)
            + f"; so o nivel, {flattened['win']:+.2f}"
        )
        for name, label in ARMS:
            row = fold[name]
            print(
                f"    {label:<14} erro medio {row['mae']:.2f}, enviesamento "
                f"{row['bias']:+.2f}, sobre {row['rows']} treinadores"
            )
            print(f"      escolheria {row['pick']}, que fez {row['realised']}")

    print("\nas duas jornadas juntas:")
    totals = {}
    for name, label in ARMS:
        totals[name] = {
            "mae": mean(abs(e) for e in pooled[name]),
            "bias": mean(pooled[name]),
        }
        print(
            f"  {label:<14} erro medio {totals[name]['mae']:.2f}, "
            f"enviesamento {totals[name]['bias']:+.2f}"
        )

    both = all(fold["beyond"]["mae"] < fold["flat"]["mae"] for fold in folds)
    together = totals["beyond"]["mae"] < totals["flat"]["mae"]
    over_level = all(fold["beyond"]["mae"] < fold["level"]["mae"] for fold in folds)
    level_wins = all(fold["level"]["mae"] < fold["flat"]["mae"] for fold in folds)
    changed = [f["round"] for f in folds if f["beyond"]["pick"] != f["flat"]["pick"]]
    kept = all(
        (fold["beyond"]["realised"] or 0) >= (fold["flat"]["realised"] or 0)
        for fold in folds
    )

    print(
        f"\nescolha: muda em {len(changed)} de {len(folds)} jornadas"
        + (f" ({', '.join(str(n) for n in changed)})" if changed else "")
    )
    passes = both and together and kept
    print(
        f"\n{'PASSA' if passes else 'NAO PASSA'} a barra: "
        f"erro menor nas duas dobras {'sim' if both else 'NAO'}, "
        f"no conjunto {'sim' if together else 'NAO'}, "
        f"escolha nao pior {'sim' if kept else 'NAO'}"
    )
    print(
        "\ne a forma contra o nivel, que e a pergunta por baixo da barra: "
        f"a forma ganha ao nivel nas duas dobras {'sim' if over_level else 'NAO'}, "
        f"e so o nivel ganha ao 2.59 nas duas {'sim' if level_wins else 'NAO'}"
    )
    print(f"  ajuste das duas juntas, que e o que iria para o codigo: {whole}")
    print(f"  o que esta no codigo agora: {COACH_BEYOND_RESULT}")
    return 0 if passes else 1


if __name__ == "__main__":
    sys.exit(main())
