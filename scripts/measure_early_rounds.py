"""Does the zerozero rebuild of 2026/27 score what Record paid?

Phase 2 lets rounds 1-5 of this season, rebuilt from zerozero, into the model
(`source.season`). Before it does, the rebuild is checked against every real
score there is. The bars were written in docs/REVISAO-2026-09-22-fase2.md
before any result was read:

    the emailed rounds from 6, the whole market
        played or not agrees on >= 95% of the rows whose club had a match;
        where both say he played, the mean error is within +-0.5 and the
        mean absolute error at most 1.0
    rounds 3-5, the squad's emails: at most 2 of the 66 rows disagree
        (the three §15.3 voided in round 3 are left out, as the emails do)
    round 2, appearances.json of 19/08: >= 95% of the 441 with a match agree
    >= 95% of the market linked to zerozero, the rest named

A man who has left the market since cannot be compared, and his rows are left
out and counted. One still on the market whom the rebuild does not cover is a
row that disagrees in the last two bars — never a smaller denominator — and in
the first he is the coverage bar's business: covered means in the rebuild,
linked and his page read, which is what the model reads.

The limits, said before reading: only Record's mark is estimated, 0.44 off the
fit, and the rebuild counts neither the player of the week, nor own goals, nor
penalties.

    python scripts/measure_early_rounds.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import FIRST_SCORING_MATCHDAY, Position  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, load_official_rounds  # noqa: E402
from liga_record_mcp.source.appearances import load_appearances  # noqa: E402
from liga_record_mcp.source.scores import email_score  # noqa: E402
from liga_record_mcp.source.season import MARKET_AT_START_ROUND, REBUILT_FILE  # noqa: E402
from liga_record_mcp.stats import NO_MATCH, PLAYED, UNUSED_PENALTY  # noqa: E402

DATA = ROOT / "data"

#: The bars, from the plan, fixed before any result was read.
AGREEMENT = 0.95
MEAN_ERROR = 0.5
MEAN_ABSOLUTE_ERROR = 1.0
SQUAD_DISAGREEMENTS = 2
LINKED = 0.95


def played_in(match: Mapping[str, Any] | None) -> bool:
    """Whether the rebuild has him on the pitch. A filled-in absence is not."""
    return bool(match) and not match.get("absent") and bool(match.get("used"))


def owner(key: str, market: Mapping[str, Any]) -> str | None:
    """The market id an email row belongs to, or None if he has left the market.

    By name and club, else by a name the MARKET holds once: a man who moved
    between Portuguese clubs keeps his old club in the older emails. Never by a
    name the email holds once — the emails of rounds 3-5 carry the squad alone,
    and there the only "Samu" is V. Guimarães's, which read the FC Porto Samu's
    rounds 3-5 off the wrong man.
    """
    name, club = key.split("|", 1)
    same = [i for i, p in market.items() if p.name == name]
    exact = [i for i in same if market[i].club == club]
    if exact:
        return exact[0]
    return same[0] if len(same) == 1 else None


def against_emails(
    rebuilt: Mapping[str, Mapping[str, Any]],
    market: Mapping[str, Any],
    rounds: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One row per email row that counts: his club had a match.

    Each row is read once, from the email's side. One whose man has left the
    market is kept, `on_market: False`, so it can be counted rather than lost;
    a man on the market the rebuild does not cover is `linked: False` and never
    agrees, so no bar can shrink around the players it could not compare.

    `agree` is the bar as written: -1 in the email is a man not used. `blind`
    marks the rows that reading cannot settle — the rebuild has him on the
    pitch for exactly -1, which is also what the email says (see
    `source.scores`: a man who played and netted -1 reads as unused).
    """
    rows = []
    for number, doc in sorted(rounds.items()):
        for key, score in doc["jogadores"].items():
            name, club = key.split("|", 1)
            if email_score(doc, name, club) is None:
                continue
            player_id = owner(key, market)
            entry = rebuilt.get(player_id) if player_id is not None else None
            his = {int(m["round"]): m for m in (entry or {}).get("matches") or ()}
            match = his.get(number)
            on_email, on_rebuild = score != UNUSED_PENALTY, played_in(match)
            compared = entry is not None
            rebuilt_points = float(match["points"]) if on_rebuild else None
            rows.append(
                {
                    "name": name,
                    "club": club,
                    "position": market[player_id].position.value if player_id else None,
                    "round": number,
                    "email": score,
                    "rebuilt": rebuilt_points,
                    "on_market": player_id is not None,
                    "linked": compared,
                    "agree": compared and on_email == on_rebuild,
                    "blind": compared and not on_email and rebuilt_points == UNUSED_PENALTY,
                    "error": rebuilt_points - score
                    if compared and on_email and on_rebuild
                    else None,
                }
            )
    return rows


def against_appearances(
    rebuilt: Mapping[str, Mapping[str, Any]],
    market: Mapping[str, Any],
    statuses: Mapping[str, str],
    number: int,
) -> list[dict[str, Any]]:
    """Round `number` of the appearance record, for the men still on the market
    whose club had a match; one never linked is a row that does not agree."""
    rows = []
    for player_id, status in sorted(statuses.items()):
        if status == NO_MATCH or player_id not in market:
            continue
        entry = rebuilt.get(player_id)
        his = {int(m["round"]): m for m in (entry or {}).get("matches") or ()}
        rows.append(
            {
                "id": player_id,
                "linked": entry is not None,
                "agree": entry is not None
                and (status == PLAYED) == played_in(his.get(number)),
            }
        )
    return rows


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [r["error"] for r in rows if r.get("error") is not None]
    agree = sum(1 for r in rows if r["agree"])
    return {
        "rows": len(rows),
        "linked": sum(1 for r in rows if r.get("linked", True)),
        "agree": agree,
        # Disagreements the email alone cannot settle: on the pitch for -1.
        "blind": sum(1 for r in rows if not r["agree"] and r.get("blind")),
        "share": agree / len(rows) if rows else 0.0,
        "scored": len(errors),
        "mean_error": mean(errors) if errors else 0.0,
        "mae": mean(abs(e) for e in errors) if errors else 0.0,
    }


def verdict(passed: bool) -> str:
    return "PASSA" if passed else "FALHA"


def main() -> int:
    path = DATA / REBUILT_FILE
    if not path.exists():
        raise SystemExit(
            f"no data/{REBUILT_FILE} — run build_last_season.py --season 156 "
            f"--out data/{REBUILT_FILE}"
        )
    rebuilt = json.loads(path.read_text(encoding="utf-8"))["players"]
    client = LigaRecordClient(timeout=60.0)
    market = {p.id: p.as_player() for pos in Position for p in client.search(pos)}
    emails = load_official_rounds(DATA / "pontuacoes", first_round=1)
    links = json.loads((DATA / "players.json").read_text(encoding="utf-8"))["players"]
    statuses = (
        load_appearances(DATA / "appearances.json")["rounds"].get(MARKET_AT_START_ROUND)
        or {}
    ).get("players") or {}
    results = []

    whole = {n: d for n, d in emails.items() if n >= FIRST_SCORING_MATCHDAY}
    every = against_emails(rebuilt, market, whole)
    # The rebuild's accuracy is judged on the men it covers; the ones it does
    # not are the coverage bar's business, below, and are counted there.
    late = [r for r in every if r["linked"]]
    s = summary(late)
    passed = (
        s["share"] >= AGREEMENT
        and abs(s["mean_error"]) <= MEAN_ERROR
        and s["mae"] <= MEAN_ABSOLUTE_ERROR
    )
    results.append(passed)
    print(f"jornadas {sorted(whole)}, mercado inteiro, contra os emails: {verdict(passed)}")
    print(
        f"  jogou ou não coincide em {s['agree']} de {s['rows']} ({s['share']:.1%}); "
        f"onde jogou ({s['scored']}): erro médio {s['mean_error']:+.2f}, "
        f"absoluto médio {s['mae']:.2f}"
    )
    print(
        f"  {s['blind']} das discordâncias são em campo por -1, que o email lê "
        "como não utilizado"
    )
    departed = sum(1 for r in every if not r["on_market"])
    print(
        f"  {len(every)} linhas nos emails: {departed} de quem já saiu do mercado, "
        f"{len(every) - departed - len(late)} de jogadores fora da reconstrução"
    )
    for position in Position:
        errors = [
            r["error"]
            for r in late
            if r["error"] is not None and r["position"] == position.value
        ]
        if errors:
            print(
                f"    {position.value}: {len(errors)} linhas, erro médio "
                f"{mean(errors):+.2f}, absoluto médio {mean(abs(e) for e in errors):.2f}"
            )
    missed = [r for r in late if not r["agree"] and r["email"] != UNUSED_PENALTY]
    invented = [r for r in late if not r["agree"] and r["email"] == UNUSED_PENALTY]
    print(f"  jogou no email e não na reconstrução: {len(missed)}")
    for r in missed[:12]:
        print(f"    J{r['round']} {r['name']} ({r['club']}) email {r['email']}")
    print(f"  jogou na reconstrução e -1 no email: {len(invented)}")
    for r in invented[:12]:
        print(f"    J{r['round']} {r['name']} ({r['club']}) reconstrução {r['rebuilt']}")
    worst = sorted((r for r in late if r["error"] is not None), key=lambda r: -abs(r["error"]))
    print("  os maiores erros:")
    for r in worst[:10]:
        print(
            f"    J{r['round']} {r['name']} ({r['club']}) email {r['email']} "
            f"reconstrução {r['rebuilt']:.1f}"
        )

    early = {n: d for n, d in emails.items() if n < FIRST_SCORING_MATCHDAY}
    emailed = against_emails(rebuilt, market, early)
    # Every row of a man still on the market counts, compared or not: one the
    # rebuild does not cover is a disagreement, not a smaller denominator.
    on_market = [r for r in emailed if r["on_market"]]
    squad = summary(on_market)
    passed = squad["rows"] - squad["agree"] <= SQUAD_DISAGREEMENTS
    results.append(passed)
    print(f"\njornadas {sorted(early)}, plantel, contra os emails: {verdict(passed)}")
    print(
        f"  coincide em {squad['agree']} de {squad['rows']} "
        f"({squad['rows'] - squad['linked']} fora da reconstrução, contadas como "
        f"falha); onde jogou ({squad['scored']}): erro médio "
        f"{squad['mean_error']:+.2f}, absoluto médio {squad['mae']:.2f}"
    )
    print(
        f"  {squad['blind']} das discordâncias são em campo por -1, que o email lê "
        "como não utilizado"
    )
    print(
        f"  {len(emailed)} linhas nos emails; {len(emailed) - len(on_market)} de "
        "quem já saiu do mercado ficam de fora"
    )
    for r in on_market:
        if not r["agree"]:
            print(
                f"    J{r['round']} {r['name']} ({r['club']}) email {r['email']} "
                f"reconstrução {r['rebuilt']}"
            )

    start = summary(
        against_appearances(rebuilt, market, statuses, int(MARKET_AT_START_ROUND))
    )
    with_a_match = sum(1 for status in statuses.values() if status != NO_MATCH)
    passed = start["share"] >= AGREEMENT
    results.append(passed)
    print(f"\njornada {MARKET_AT_START_ROUND}, appearances.json de 19/08: {verdict(passed)}")
    print(
        f"  coincide em {start['agree']} de {start['rows']} ({start['share']:.1%}); "
        f"{with_a_match} tinham jogo, {with_a_match - start['rows']} já saíram do "
        f"mercado, {start['rows'] - start['linked']} fora da reconstrução "
        "(contados como falha)"
    )

    # Covered means in the rebuild, which is what the model reads: linked to
    # zerozero, and his page read.
    missing = [p for i, p in market.items() if i not in rebuilt]
    share = 1 - len(missing) / len(market)
    passed = share >= LINKED
    results.append(passed)
    print(
        f"\nna reconstrução: {len(market) - len(missing)} de {len(market)} "
        f"({share:.1%}): {verdict(passed)}"
    )
    for p in sorted(missing, key=lambda p: (p.club, p.name)):
        why = "página por ler" if p.id in links else "sem ligação ao zerozero"
        print(f"  {p.name} ({p.club}) — {why}")

    print(f"\n{'TUDO PASSA' if all(results) else 'NÃO PASSA'} — {sum(results)} de {len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
