"""What §14.3 does not explain about a coach's round — the mark, and the rest.

The model credits every coach one number on top of what §14.3 pays for the
result. Until 23/09/2026 it was 2.59 — the mark handed to PLAYERS where no mark
is known, a loan, and nothing said a coach's was the same number. It is now
`COACH_BEYOND_POINTS`, fitted here on matchdays 6 and 7.

SO THIS IS NOW A CHECK ON WHAT RUNS. It reads the constant the model pays and
asks whether the emails still agree with it; a round that moves the level out
of the interval will say so.

So this takes the real result of each coach's match, scores §14.3 on it with
`stats.coach_points`, and subtracts. What is left is exactly what the constant
stands in for: §14.1's mark, and the things a results model cannot see before
kickoff — coming from behind, goals off the bench, a coach sent off.

THE TRUTH ONLY EXISTS IN THE WEEKLY EMAILS. No public archive records what a
coach scored, so this cannot be replayed over the reconstructed seasons: it is
eighteen rows a round, from matchday 6 on.

TWO THINGS THIS CANNOT SEE, both of which would land in the leftover and be
read as a mark. A coach who never took the bench scores nothing at all under
§14.5, not zero-and-the-result, and the calendar does not say who was there;
that shows up as a large negative leftover, so the negatives are counted out
loud. And the two coaches of one match share a scoreline, so the rows are not
independent and the interval runs a little tight — see `interval`.

AND THE SHAPE DECIDES BEFORE THE LEVEL. If what is left grows with the result,
a constant is the wrong instrument and moving it would straighten the wrong
number; the split by result is printed for exactly that reason.

    python scripts/measure_coach_mark.py
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import FIRST_SCORING_MATCHDAY  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, load_official_rounds  # noqa: E402
from liga_record_mcp.stats import COACH_BEYOND_POINTS, coach_points  # noqa: E402

DATA = ROOT / "data"

#: The bar, from the plan, fixed before any result was read: the constant only
#: moves if the 90% interval of what is left excludes today's value.
CONFIDENCE = 1.645


def fold(text: str) -> set[str]:
    """A club label as words, without accents, for matching two spellings."""
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return {word for word in stripped.replace(".", " ").split() if word}


def same_club(one: str, other: str) -> bool:
    """Whether two labels name one club. The email writes "Académico" where the
    calendar writes "Académico Viseu"."""
    first, second = fold(one), fold(other)
    return bool(first) and bool(second) and (first <= second or second <= first)


def results_in(fixtures, round_number: int) -> dict[str, tuple[int, int]]:
    """Each club's scoreline that round: goals for, goals against."""
    found: dict[str, tuple[int, int]] = {}
    for fixture in fixtures:
        if fixture.round_number != round_number or not fixture.played:
            continue
        found[fixture.home] = (fixture.home_goals, fixture.away_goals)
        found[fixture.away] = (fixture.away_goals, fixture.home_goals)
    return found


def rows_for(rounds, fixtures) -> list[dict]:
    """One row per coach and emailed round whose match was played.

    `left` is the round's points minus what §14.3 pays for the result, with no
    mark and none of the events a results model cannot see.
    """
    rows = []
    for number, doc in sorted(rounds.items()):
        played = results_in(fixtures, number)
        for key, scored in (doc.get("treinadores") or {}).items():
            name, _, club = key.partition("|")
            match = next(
                (goals for label, goals in played.items() if same_club(club, label)),
                None,
            )
            if match is None:
                continue
            for_, against = match
            rules = coach_points(scored=for_, conceded=against, rating_points=0)
            rows.append(
                {
                    "round": number,
                    "name": name,
                    "club": club,
                    "points": scored,
                    "rules": rules,
                    "left": scored - rules,
                    "result": "vitoria"
                    if for_ > against
                    else "empate"
                    if for_ == against
                    else "derrota",
                }
            )
    return rows


def interval(values: list[float]) -> tuple[float, float, float]:
    """The mean and its 90% interval, by the standard error of the mean.

    It runs a little tight, because the rows are not independent: the two
    coaches of one match share a scoreline, so a chaotic match moves both
    leftovers together. It is not tight enough to matter to the bar it is read
    on — the gap to the model's constant is four standard errors, and the win
    and loss rows come from twelve distinct matches each — but a later run with
    more rounds should not be read as if the rows were independent.
    """
    if len(values) < 2:
        return (values[0] if values else 0.0), 0.0, 0.0
    average = mean(values)
    error = stdev(values) / (len(values) ** 0.5)
    return average, average - CONFIDENCE * error, average + CONFIDENCE * error


def verdict(rows: list[dict]) -> dict:
    """Both bars from the plan, in the order it fixed them.

    `level` is the first: the 90% interval of the leftover excludes what the
    model pays today. `flat` is the second, and it is the one that governs —
    if the leftover walks with the result, the constant is the wrong SHAPE and
    stretching it would straighten the average while leaving every coach wrong
    by the same amount, the winner short and the loser long.

    So the constant moves only if both hold. Fewer than two rows is never a
    pass: an empty measurement has no interval, and must not read as one.
    """
    left = [row["left"] for row in rows]
    average, low, high = interval(left)
    wins = [row["left"] for row in rows if row["result"] == "vitoria"]
    losses = [row["left"] for row in rows if row["result"] == "derrota"]

    flat = True
    if len(wins) > 1 and len(losses) > 1:
        _, win_low, _ = interval(wins)
        _, _, loss_high = interval(losses)
        flat = win_low <= loss_high

    level = len(left) > 1 and (low > COACH_BEYOND_POINTS or high < COACH_BEYOND_POINTS)
    return {
        "rows": len(rows),
        "average": average,
        "low": low,
        "high": high,
        "level": level,
        "flat": flat,
        "change": level and flat,
    }


def main() -> int:
    rounds = load_official_rounds(DATA / "pontuacoes", first_round=FIRST_SCORING_MATCHDAY)
    emailed = {n: doc for n, doc in rounds.items() if doc.get("treinadores")}
    if not emailed:
        raise SystemExit("nenhum email com treinadores em data/pontuacoes")
    fixtures = LigaRecordClient(timeout=60.0).fixtures()

    rows = rows_for(emailed, fixtures)
    matched = {(row["round"], row["club"], row["name"]) for row in rows}
    missing = [
        f"J{number} {key.partition('|')[2]}"
        for number, doc in sorted(emailed.items())
        for key in (doc.get("treinadores") or {})
        if (number, key.partition("|")[2], key.partition("|")[0]) not in matched
    ]
    if len(rows) < 2:
        raise SystemExit(
            f"{len(rows)} treinador-jornadas com jogo lido no calendario, de "
            f"{len(rows) + len(missing)} emailadas: nao ha o que medir"
        )

    result = verdict(rows)
    print(f"jornadas {sorted(emailed)}: {len(rows)} treinador-jornadas")
    if missing:
        print(
            f"  FORA DA CONTA, sem jogo jogado no calendario: {len(missing)} "
            f"({', '.join(missing)})"
        )
    print(
        f"  o que sobra ao §14.3: {result['average']:+.2f} "
        f"(90%: {result['low']:+.2f} a {result['high']:+.2f}), "
        f"contra os {COACH_BEYOND_POINTS:.2f} que o modelo paga"
    )
    print(f"  o que o §14.3 explica: {mean(r['rules'] for r in rows):+.2f} por jornada")
    print(f"  o que os treinadores fizeram: {mean(r['points'] for r in rows):+.2f}")

    negative = [row for row in rows if row["left"] < 0]
    if negative:
        print(
            f"  {len(negative)} com sobra negativa, que uma nota nao explica: o §14.5 "
            "paga zero a quem nao esteve no banco, e o calendario nao diz quem esteve"
        )

    print("\n  por resultado, que é a forma antes do nível:")
    for result_name in ("vitoria", "empate", "derrota"):
        theirs = [row["left"] for row in rows if row["result"] == result_name]
        if theirs:
            each, under, over = interval(theirs)
            print(
                f"    {result_name:<8} {len(theirs):>3} linhas, sobra {each:+.2f} "
                f"(90%: {under:+.2f} a {over:+.2f})"
            )

    print("\n  as maiores sobras, que é onde estao os eventos que nao se veem:")
    for row in sorted(rows, key=lambda r: -abs(r["left"]))[:8]:
        print(
            f"    J{row['round']} {row['name'][:22]:<22} {row['club'][:16]:<16} "
            f"fez {row['points']:>3}, §14.3 dá {row['rules']:>3}, sobra {row['left']:+d}"
        )

    print(
        f"\nnivel: {'PASSA' if result['level'] else 'NAO PASSA'} — o intervalo "
        f"{'exclui' if result['level'] else 'inclui'} os {COACH_BEYOND_POINTS:.2f} do modelo"
    )
    print(
        "forma: "
        + (
            "o que sobra nao anda com o resultado"
            if result["flat"]
            else "o que sobra ANDA com o resultado, e a forma decide antes do nivel"
        )
    )
    print(
        f"\n{'TROCAR' if result['change'] else 'NAO TROCAR'} a constante: "
        + (
            "as duas barras passam"
            if result["change"]
            else "o problema é a forma, nao o nivel — dobrar a nota ao resultado é outro plano"
            if result["level"]
            else "o nivel medido nao exclui o que o modelo ja paga"
        )
    )
    return 0 if result["change"] else 1


if __name__ == "__main__":
    sys.exit(main())
