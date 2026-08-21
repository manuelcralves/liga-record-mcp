"""Render the season dashboard from live standings and the projection log.

The point of this page is the gap between two columns: what was projected
before a round, and what actually happened. Everything else on it is context
for reading that gap.

So the projection ledger never shows a blank cell for a result that has not
arrived. A blank reads as zero, and a zero here would be a lie — it is exactly
the confusion between "pending" and "scored" that cost this project a day.
Unsettled rows carry an explicit pending state instead.

The page is two designs with a hinge between them. The top is broadcast
graphics — heavy condensed type, huge figures, solid colour blocks — and it is
a fixed dark panel that ignores the reader's theme, the way a front page's
photograph is the photograph whatever paper it is printed on. Below a heavy
rule it becomes a results page: no boxes, no shadows, hierarchy carried
entirely by rules and type weight, because thirty league rows and a
twenty-three-row fixture grid need a density the broadcast idiom cannot give.

    python scripts/build_dashboard.py              # private, full league table
    python scripts/build_dashboard.py --public     # docs/index.html, no other people
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from statistics import pstdev

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp import server as mcp  # noqa: E402
from liga_record_mcp.advice import MIN_OWN_HISTORY, valuation  # noqa: E402
from liga_record_mcp.backtest import best_transfer  # noqa: E402
from liga_record_mcp.optimise import best_eleven  # noqa: E402
from liga_record_mcp.source import ManualSquadSource  # noqa: E402
from liga_record_mcp.source.last_season import archive_records  # noqa: E402
from liga_record_mcp.models import LAST_MATCHDAY, Position  # noqa: E402
from liga_record_mcp.source import OpenFootballClient  # noqa: E402
from liga_record_mcp.stats import (  # noqa: E402
    MEAN_MARK_POINTS,
    adjust_for_fixture,
    coach_season,
    describe_pick,
    fixture_multipliers,
    matches_played,
    per_match,
    upcoming_opponents,
)

LOG_PATH = ROOT / "data" / "projections.json"
SQUAD_PATH = ROOT / "data" / "squad.yaml"
HISTORY_PATH = ROOT / "data" / "history.json"
MY_TEAM_ID = 156412
ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
POS_PT = {"GK": "GR", "DEF": "DEF", "MID": "MED", "FWD": "AVA"}

# The sheet as entered on the site. Kept here rather than read back, because
# reading a team sheet needs a login this project deliberately does not hold.
XI = "38800 41584 41670 43052 42725 21459 43500 33728 42896 42920 42142".split()
BENCH = "42398 42937 42893 43430".split()
CAPTAIN = "42896"
COACH = "Farioli", "FC Porto"
GRID_ROUNDS = 5


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def group(number: int) -> str:
    """Thousands separated by spaces, the way the site writes them."""
    return f"{number:,}".replace(",", " ")


def gather(round_number: int) -> dict:
    log = json.loads(LOG_PATH.read_text(encoding="utf-8"))["rounds"]
    key = str(round_number)
    if key not in log:
        raise SystemExit(f"round {round_number} has no recorded projection")
    stored = log[key]

    guid = os.environ.get("LIGA_RECORD_LEAGUE")
    league = mcp.standings(league_guid=guid, page_size=50) if guid else None
    national = mcp.standings(team="Melro", national=True)

    me = None
    if league and league.get("teams"):
        me = next((t for t in league["teams"] if t["team"] == "Melro"), None)
    if me is None:
        me = next(
            (t for t in national.get("teams", []) if t.get("user") == "manuelcralves"),
            None,
        )
    # A transient site error returns {"detail": ...} with no teams, and the
    # page would then render with a section quietly missing. A dashboard that
    # silently drops a section is worse than one that refuses to build.
    if guid and not (league or {}).get("teams"):
        raise SystemExit(
            "the private league came back empty: "
            f"{(league or {}).get('detail', 'no teams and no reason given')}"
        )
    if me is None:
        raise SystemExit("could not find the team Melro in either ranking")

    # A position without the size of the field says very little, and the hero
    # prints it as a headline figure. A transient failure here used to render an
    # em-dash and say nothing — the same silent degradation the league check
    # above exists to prevent.
    sized = mcp.standings(page_size=20, national=True)
    field = sized.get("field_size_estimate")
    if not field:
        raise SystemExit(
            "the national ranking gave no field size: "
            f"{sized.get('detail', 'no page count and no reason given')}"
        )

    history = []
    if HISTORY_PATH.exists():
        raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["rounds"]
        for round_key, entry in sorted(raw.items(), key=lambda kv: int(kv[0])):
            row = entry["teams"].get(str(MY_TEAM_ID))
            if row:
                history.append({"round": int(round_key), **row})

    return {
        "round": round_number,
        "stored": stored,
        "league": (league or {}).get("teams") or [],
        "me": me,
        "field": field,
        "history": history,
        "differentials": mcp.find_differentials(limit=10, min_matches=1),
        "exposure": mcp.squad_exposure(round_number=round_number),
        "grid": fixture_grid(stored, round_number),
        "table": mcp.primeira_liga(),
        "market": market_leaders(stored),
        "ratings": mcp.editorial_ratings(),
        "model": model_sheet(stored, round_number),
        "seasons": two_seasons(stored),
        "coaches": coach_data(),
        "race": scorer_race(),
        "holidays": holiday_rows(round_number),
        "as_of": national.get("as_of", ""),
    }


#: What a pick's label is called on the page. stats.py speaks English like the
#: rest of the code; the translation lives here so the model and the page never
#: have to agree on a language.
LABEL_PT = {
    "safe": "seguro",
    "volatile": "volátil",
    "uncertain": "incerto",
    "risky": "arriscado",
    "bet": "aposta",
    "fair": "razoável",
}
FIELD_PT = {"template": "toda a gente tem", "differential": "diferencial"}


def holiday_rows(round_number: int) -> list[dict]:
    """What §6.17 would have paid in each round already scored.

    One live read per round, which is cheap and is the only way to know: the
    round winner's score is not derivable from anything held locally.
    """
    rows = []
    for played in range(1, round_number + 1):
        found = mcp.holiday_round(played)
        if not found.get("round_winner"):
            # Not scored yet. A zero here would read as "the holiday was worth
            # nothing that week" instead of "that week has not happened".
            continue
        rows.append(
            {
                "round": played,
                "winner": found["round_winner"],
                "pays": found["holiday_pays"],
                "ours": found.get("our_score"),
            }
        )
    return rows


def scorer_race() -> list[dict]:
    """Who is leading the race §10.3(m) pays for.

    The bet is made once, when the team is first submitted, and is worth +20 /
    +10 / +5 in the final standings. It costs nothing, which is exactly why it
    goes unmade: nothing that costs nothing feels like a decision.

    Ranked on goals per appearance across the archives, because four matchdays
    of finishing is mostly luck — but this season's minutes are shown beside
    it, and they are what changes a mind. A striker on a goal a game who is
    playing twenty-seven minutes a week is not leading anything.
    """
    goals, apps, this_year = {}, {}, {}
    for path, current in (
        (ROOT / "data" / "last-season.json", False),
        (ROOT / "data" / "season-2024-25.json", False),
    ):
        if not path.exists():
            continue
        for player_id, player in json.loads(
            path.read_text(encoding="utf-8")
        )["players"].items():
            for match in player["matches"]:
                if match.get("used"):
                    apps[player_id] = apps.get(player_id, 0) + 1
                    goals[player_id] = goals.get(player_id, 0) + int(match.get("goals") or 0)
    if not goals:
        return []

    forwards = mcp.search_market(position="FWD", limit=500).get("players", [])
    rows = []
    for player in forwards:
        seen = apps.get(player["id"], 0)
        if seen < 15:
            continue
        rows.append(
            {
                "name": player["name"],
                "club": player["club"],
                "value": player["value"],
                "goals": goals.get(player["id"], 0),
                "apps": seen,
                "rate": goals.get(player["id"], 0) / seen,
                "owned": player.get("owned_percent"),
                "points": player.get("points_total", 0),
            }
        )
    rows.sort(key=lambda r: -r["rate"])
    return rows[:8]


def scorer_section(data: dict) -> str:
    rows = data.get("race") or []
    if not rows:
        return '      <p class="lede">Sem épocas reconstruídas.</p>'
    body = chr(10).join(
        '            <tr>' + chr(10)
        + '              <td class="name">' + esc(r["name"])
        + '<span class="sub">' + esc(r["club"]) + '</span></td>' + chr(10)
        + '              <td class="fig strong">' + f"{r['rate']:.2f}" + '</td>' + chr(10)
        + '              <td class="fig">' + str(r["goals"]) + '</td>' + chr(10)
        + '              <td class="fig">' + str(r["apps"]) + '</td>' + chr(10)
        + '              <td class="fig">'
        + ("—" if r["owned"] is None else f"{r['owned']:.0f}%") + '</td>' + chr(10)
        + '            </tr>'
        for r in rows
    )
    return (
        '      <p class="lede">O §10.3(m) paga <strong>+20</strong> por acertares '
        "no bota-de-ouro, +10 no prata, +5 no bronze. É gratuito, é opcional, e "
        "faz-se <strong>uma única vez</strong>, ao constituir a equipa — que é "
        "precisamente porque fica por fazer: o que não custa nada não parece uma "
        "decisão. A tabela ordena por golos por jogo nas duas épocas "
        "reconstruídas, e não por golos: quatro jornadas de eficácia são sorte. "
        "Mas confirma sempre os minutos desta época antes de decidir — um "
        "avançado a golo por jogo que anda a jogar vinte e sete minutos não está "
        "a liderar coisa nenhuma.</p>"
        + chr(10) + '      <table class="data">' + chr(10)
        + "        <thead><tr><th>avançado</th><th>golos/jogo</th><th>golos</th>"
        + "<th>jogos</th><th>posse</th></tr></thead>" + chr(10)
        + "        <tbody>" + chr(10) + body + chr(10)
        + "        </tbody>" + chr(10) + "      </table>"
    )


def coach_data() -> dict:
    """What each club's coach returns, this season and across the archives.

    §14.4 adds a coach to the team every round and §6.4 charges nothing for
    him, so this is the only pick in the game with no constraint on it at all —
    and one most people set once in August and never look at again.

    The spread reported is over THIS season's eighteen clubs, with §14.1's
    unpublished mark credited to everyone alike so that it cancels. It is not
    the same as the 86 points measured across last season's table, which had
    different clubs in it and left the mark out entirely. Two honest numbers
    answering two questions.
    """
    coaches = mcp.list_coaches().get("coaches") or []
    fixtures = OpenFootballClient().season_fixtures("2025-26")
    history = coach_season(
        (f.model_dump() | {"round": f.round_number} for f in fixtures),
        rating_points=round(MEAN_MARK_POINTS),
    )
    rates = {
        club: (sum(rounds.values()) / len(rounds) if rounds else 0.0)
        for club, rounds in history.items()
    }

    def matched(name: str):
        """openfootball writes clubs out in full where Liga Record abbreviates.

        Compared word by word rather than as substrings, for the reason the
        zerozero matcher learned the hard way: "Nacional" is inside
        "Internacional", and a club that quietly matches the wrong one puts
        another team's season against this coach's name.
        """
        ours = {w for w in name.lower().replace(".", " ").split() if len(w) > 2}
        for club, rate in rates.items():
            theirs = {w for w in club.lower().split() if len(w) > 2}
            if ours and (ours <= theirs or theirs <= ours):
                return rate
        return None

    rows = [
        {
            "name": c["name"],
            "club": c["club"],
            "now": c.get("points_total", 0),
            "round": c.get("points_round", 0),
            "history": matched(c["club"]),
        }
        for c in coaches
    ]
    rows.sort(key=lambda r: -(r["history"] if r["history"] is not None else -9))
    known = [r["history"] for r in rows if r["history"] is not None]
    return {"rows": rows, "spread": (max(known) - min(known)) * 29 if known else None}


def coach_section(data: dict) -> str:
    found = data.get("coaches") or {}
    rows = found.get("rows") or []
    if not rows:
        return '      <p class="lede">Sem dados de treinadores.</p>'
    body = chr(10).join(
        '            <tr>' + chr(10)
        + '              <td class="name">' + esc(r["name"])
        + '<span class="sub">' + esc(r["club"]) + '</span></td>' + chr(10)
        + '              <td class="fig">'
        + ("—" if r["history"] is None else f"{r['history']:+.2f}") + '</td>' + chr(10)
        + '              <td class="fig strong">' + str(r["now"]) + '</td>' + chr(10)
        + '              <td class="fig">' + f"{r['round']:+d}" + '</td>' + chr(10)
        + '            </tr>'
        for r in rows
    )
    spread = found.get("spread")
    gap = (
        f"Na época passada, escolher sempre o melhor em vez do pior valia "
        f"<strong>{spread:.0f} pontos</strong>. "
        if spread
        else ""
    )
    return (
        '      <p class="lede">Escolhe-se um todas as jornadas, não custa '
        "orçamento (§6.4), e sem ele a equipa faz <strong>zero</strong> "
        "(§6.17). Os pontos dele somam-se aos da equipa (§14.4). " + gap
        + "A cláusula que decide é o §14.3(b): empatar depois de estar dois "
        "abaixo vale +2, <em>ganhar</em> vale +4, porque duplica. E o §14.5 é "
        "o aviso — um treinador castigado ou despedido <strong>não pontua de "
        "todo</strong>: a equipa dele pode ganhar 5-0 e tu não levas nada.</p>"
        + chr(10) + '      <table class="data">' + chr(10)
        + "        <thead><tr><th>treinador</th><th>época passada</th>"
        + "<th>total</th><th>jornada</th></tr></thead>" + chr(10)
        + "        <tbody>" + chr(10) + body + chr(10)
        + "        </tbody>" + chr(10) + "      </table>"
    )


def holiday_section(data: dict) -> str:
    rows = data.get("holidays") or []
    if not rows:
        return '      <p class="lede">Ainda sem jornadas pontuadas.</p>'
    body = chr(10).join(
        '            <tr>' + chr(10)
        + '              <td class="name">Jornada ' + str(r["round"]) + '</td>' + chr(10)
        + '              <td class="fig">' + str(r["winner"]) + '</td>' + chr(10)
        + '              <td class="fig strong">' + str(r["pays"]) + '</td>' + chr(10)
        + '              <td class="fig">'
        + ("—" if r["ours"] is None else str(r["ours"])) + '</td>' + chr(10)
        + '              <td class="fig '
        + ("up" if r["pays"] > (r["ours"] or 0) else "down") + '">'
        + ("—" if r["ours"] is None else f"{r['pays'] - r['ours']:+d}")
        + '</td>' + chr(10)
        + '            </tr>'
        for r in rows
    )
    return (
        '      <p class="lede">O §6.17 dá <strong>três rondas por época</strong>'
        " — seguidas ou não, nunca nas três últimas — em que recebes metade da "
        "pontuação do vencedor da ronda, arredondada para cima, faças o que "
        "fizeres. E o vencedor é <strong>nacional</strong>: o §16.1 define-o "
        "como uma das trinta equipas que mais pontuam no país. "
        "Isto não é um bónus, é um chão — o pagamento é metade de um máximo "
        "sobre 127 mil equipas, alto e estável, enquanto a tua pontuação é um "
        "sorteio só. Gasta-as nas rondas em que o <em>teu</em> plantel está "
        "desfalcado e a liga não está: seleções, castigos, semanas de três "
        "jogos. O pagamento não depende de nada teu.</p>"
        + chr(10) + '      <table class="data">' + chr(10)
        + "        <thead><tr><th></th><th>vencedor</th><th>Férias pagam</th>"
        + "<th>fizeste</th><th>diferença</th></tr></thead>" + chr(10)
        + "        <tbody>" + chr(10) + body + chr(10)
        + "        </tbody>" + chr(10) + "      </table>"
    )


def model_sheet(stored: dict, round_number: int) -> dict:
    """What the model would field next round, beside what was actually filed.

    Everything else on this page reports. This one recommends, and the two are
    kept visibly apart: `sheet_section` shows the eleven that was submitted and
    this shows the eleven the model would submit, so the difference between
    them is the advice rather than something the reader has to reconstruct.

    The valuation comes from `advice.valuation`, which is also what the squad
    proposal uses. A page and a script disagreeing about a player would be
    worse than either being wrong, because neither would be the model's
    opinion.
    """
    quoted = {
        p["id"]: p
        for position in ("GK", "DEF", "MID", "FWD")
        for p in mcp.search_market(position=position, limit=500).get("players", [])
    }
    squad = ManualSquadSource(SQUAD_PATH).load().squad
    market = {p.id: p for p in squad.players}

    archive = archive_records(ROOT / "data")
    view = valuation(
        market,
        archive,
        {},
        owned={i: q.get("owned_percent") for i, q in quoted.items()},
    )
    if not any(v["appearances"] for v in view.values()):
        return {}

    rows = [
        {"id": p.id, "position": p.position, "value": p.value} for p in squad.players
    ]
    expected = {i: v["expected"] for i, v in view.items()}
    sheet = best_eleven(rows, expected)
    if sheet is None:
        return {}

    filed = set(XI)
    picked = set(sheet["starters"])

    def described(player_id: str) -> dict:
        entry = view[player_id]
        person = market[player_id]
        return {
            "name": person.name,
            "position": person.position.value,
            "club": person.club,
            "expected": entry["expected"],
            "playing": entry["playing"],
            "returns": entry["returns"],
            "label": LABEL_PT.get(entry["label"], entry["label"]),
            "appearances": entry["appearances"],
        }

    # The transfer §6.8 allows, judged on what it does to the ELEVEN rather
    # than to the player: only eleven score, so a better substitute is worth
    # nothing, and comparing the two men's own rates cannot see that.
    whole = {p.id: p.as_player() for p in mcp_players()}
    wide = valuation(
        whole,
        archive,
        {},
        owned={i: q.get("owned_percent") for i, q in quoted.items()},
    )
    # Only players with a real record are candidates. A man with no top-flight
    # matches is valued at his club's pool, which is a fair estimate and a poor
    # recommendation: the first version of this proposed a Sporting midfielder
    # with ZERO matches behind him and priced the move at thirty points, which
    # is a confident claim about somebody nobody has measured.
    #
    # He may well be excellent. A transfer is one a round and does not
    # accumulate, so it is the wrong place to find out.
    known = [
        i
        for i, entry in wide.items()
        if entry["appearances"] >= MIN_OWN_HISTORY or i in market
    ]
    swap = best_transfer(
        [p.id for p in squad.players],
        whole,
        {i: v["expected"] for i, v in wide.items()},
        budget=squad.budget,
        rounds_left=max(1, LAST_MATCHDAY - round_number + 1),
        candidates=known,
    )
    move = None
    if swap is not None:
        out_id, in_id, gain = swap
        move = {
            "out": described(out_id) if out_id in market else None,
            "in": {
                "name": whole[in_id].name,
                "club": whole[in_id].club,
                "position": whole[in_id].position.value,
                "value": whole[in_id].value,
                "expected": wide[in_id]["expected"],
                "playing": wide[in_id]["playing"],
                "appearances": wide[in_id]["appearances"],
                "label": LABEL_PT.get(wide[in_id]["label"], wide[in_id]["label"]),
                "field": FIELD_PT.get(wide[in_id]["field_label"], ""),
            },
            "gain": gain,
        }

    return {
        "starters": [described(i) for i in sheet["starters"]],
        "bench": [described(i) for i in sheet["bench"]],
        "captain": described(sheet["captain"]),
        "formation": sheet["formation"],
        "expected": sheet["points"],
        "in_not_filed": [described(i) for i in sorted(picked - filed)],
        "out_of_filed": [described(i) for i in sorted(filed - picked)],
        "transfer": move,
    }


def mcp_players():
    """Every player on the market, as squad-shaped objects."""
    return [
        p
        for position in Position
        for p in mcp._market.search(position)
    ]


def model_section(data: dict) -> str:
    found = data.get("model") or {}
    if not found:
        return """      <p class="lede">Sem épocas reconstruídas, o modelo não tem
      opinião. Corre <code>scripts/build_last_season.py</code>.</p>"""

    def line(entry: dict, mark: str = "") -> str:
        return (
            '            <tr>' + chr(10)
            + '              <td class="name">' + esc(entry["name"])
            + '<span class="sub">' + esc(POS_PT[entry["position"]]) + " · "
            + esc(entry["club"]) + "</span></td>" + chr(10)
            + '              <td class="fig strong">' + f"{entry['expected']:.2f}"
            + "</td>" + chr(10)
            + '              <td class="fig">' + f"{entry['playing']:.0%}" + "</td>" + chr(10)
            + '              <td class="verdict">' + esc(entry["label"])
            + (f'<span class="sub">{esc(mark)}</span>' if mark else "")
            + "</td>" + chr(10)
            + "            </tr>"
        )

    eleven = chr(10).join(
        line(entry, "capitão" if entry["name"] == found["captain"]["name"] else "")
        for entry in found["starters"]
    )
    bench = chr(10).join(line(entry) for entry in found["bench"])

    changes = ""
    if found["in_not_filed"] or found["out_of_filed"]:
        into = ", ".join(esc(e["name"]) for e in found["in_not_filed"]) or "ninguém"
        outof = ", ".join(esc(e["name"]) for e in found["out_of_filed"]) or "ninguém"
        changes = (
            f'      <p class="lede">Contra o onze que entregaste: entrariam '
            f"<strong>{into}</strong>, sairiam <strong>{outof}</strong>.</p>"
        )
    else:
        changes = (
            '      <p class="lede">O onze que entregaste é o mesmo que o modelo '
            "escolheria.</p>"
        )

    move = found.get("transfer")
    if move and move["out"]:
        arrival = move["in"]
        transfer = (
            '      <p class="lede"><strong>A transferência (§6.8).</strong> '
            f"Sai <strong>{esc(move['out']['name'])}</strong> "
            f"({move['out']['expected']:.2f} esperados), entra "
            f"<strong>{esc(arrival['name'])}</strong> do {esc(arrival['club'])} "
            f"a {arrival['value'] / 1e6:.2f}M ({arrival['expected']:.2f}, joga "
            f"{arrival['playing']:.0%} das jornadas, {arrival['appearances']} jogos "
            f"de evidência) — <strong>{esc(arrival['label'])}</strong>"
            + (f", {esc(arrival['field'])}" if arrival["field"] else "")
            + f". Vale cerca de <strong>{move['gain']:.0f} pontos</strong> até ao "
            "fim da época. Só entram na busca jogadores com registo a sério — "
            "quem não tem jogos é valorizado pelo clube dele, o que dá uma "
            "estimativa justa e uma recomendação má. E é uma por ronda: não "
            "acumula, e saltar uma não guarda nada.</p>"
        )
    else:
        transfer = (
            '      <p class="lede"><strong>A transferência (§6.8).</strong> '
            "Nenhuma — o modelo não encontra troca que melhore o onze o "
            "suficiente para valer a pena. Não transferir é uma decisão, e "
            "aqui é a recomendada.</p>"
        )

    return f"""      <p class="lede">Isto <strong>não</strong> é a tua folha — é a
      que o modelo entregaria, com o que sabe de {found['starters'][0]['appearances']}+
      jornadas por jogador. <strong>Esperado</strong> é quanto rende a jornada
      contando com a hipótese de não jogar; <strong>joga</strong> é essa
      hipótese. Formação <strong>{esc(found['formation'])}</strong>, capitão
      <strong>{esc(found['captain']['name'])}</strong>, e o onze todo vale
      <strong>{found['expected']:.0f}</strong> pontos esperados (§10.3(l) conta
      o capitão duas vezes).</p>
{changes}
{transfer}
      <table class="data">
        <thead><tr><th>o onze</th><th>esperado</th><th>joga</th><th>o que é</th></tr></thead>
        <tbody>
{eleven}
        </tbody>
      </table>
      <table class="data">
        <thead><tr><th>o banco</th><th>esperado</th><th>joga</th><th>o que é</th></tr></thead>
        <tbody>
{bench}
        </tbody>
      </table>"""


def two_seasons(stored: dict) -> dict:
    """What the reconstructed seasons know about each player in the squad.

    Everything on this page before it was drawn from the current season, which
    at this point is two rounds. These are sixty-eight matchdays of the same
    players, rebuilt from §10.3 and zerozero — the difference between "he
    scored well on Sunday" and "this is what he is".

    Absent until the sweeps have been run, and the section simply does not
    appear rather than rendering empty rows.
    """
    seasons = [ROOT / "data" / "last-season.json", ROOT / "data" / "season-2024-25.json"]
    played, points, available, each = {}, {}, {}, {}
    for path in seasons:
        if not path.exists():
            continue
        for player_id, player in json.loads(
            path.read_text(encoding="utf-8")
        )["players"].items():
            for match in player["matches"]:
                available[player_id] = available.get(player_id, 0) + 1
                if match.get("used"):
                    played[player_id] = played.get(player_id, 0) + 1
                    points[player_id] = points.get(player_id, 0.0) + float(match["points"])
                    each.setdefault(player_id, []).append(float(match["points"]))
    if not played:
        return {}

    owned = {
        p["id"]: p
        for position in ("GK", "DEF", "MID", "FWD")
        for p in mcp.search_market(position=position, limit=500).get("players", [])
    }

    rows = []
    for player_id, player in stored["players"].items():
        appearances = played.get(player_id, 0)
        of = available.get(player_id, 0)
        spread = pstdev(each[player_id]) if len(each.get(player_id, [])) >= 15 else None
        rate = points[player_id] / appearances if appearances else None
        said = describe_pick(
            appearances=appearances,
            availability=(appearances / of) if of >= 20 else None,
            volatility=spread,
            owned_percent=(owned.get(player_id) or {}).get("owned_percent"),
        )
        rows.append(
            {
                "name": player["name"],
                "position": player["position"],
                "club": player["club"],
                "appearances": appearances,
                "of": of,
                "rate": rate,
                "availability": (appearances / of) if of else None,
                "spread": spread,
                "owned": (owned.get(player_id) or {}).get("owned_percent"),
                "label": LABEL_PT.get(said["label"], said["label"]),
                "field": FIELD_PT.get(said["field_label"], ""),
                "thin": said["evidence"] == "thin",
            }
        )
    order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    rows.sort(key=lambda r: (order[r["position"]], -(r["rate"] or -9)))
    return {
        "rows": rows,
        "thin": sum(1 for r in rows if r["thin"]),
        "matchdays": max(available.values()) if available else 0,
    }


def market_leaders(stored: dict) -> dict:
    """Who is scoring most per match, and who the country has picked.

    Both answers come from the same market read the differentials already need,
    so this costs nothing extra. The ownership half is the counterweight to the
    differentials table: it says how far the squad already sits inside the
    consensus, which is what a differential is a move away from.
    """
    market = [p for pos in Position for p in mcp._market.search(pos)]
    counts = matches_played(mcp._market.fixtures())
    mine = set(stored["players"])

    best = {}
    for pos in Position:
        ranked = []
        for player in market:
            if player.position is not pos:
                continue
            played = counts.get(player.club, 0)
            rate = per_match(player.points_total, played)
            # Requiring two matches would drop Sp. Braga and Gil Vicente
            # entirely — hiding the clubs the postponement disadvantaged, which
            # is the denominator mistake in another hat. They rank with
            # everyone else and carry their sample size.
            if played >= 1 and rate is not None:
                ranked.append(
                    {
                        "name": player.name,
                        "club": player.club,
                        "rate": round(rate, 1),
                        "matches": played,
                        "value": player.value,
                        "owned": round(player.owned_percent, 1),
                        "mine": player.id in mine,
                    }
                )
        ranked.sort(key=lambda r: (-r["rate"], -r["matches"], -r["owned"]))
        best[pos.value] = ranked[:3]

    owned = sorted(market, key=lambda p: -p.owned_percent)[:5]
    return {
        "best": best,
        "most_owned": [
            {
                "name": p.name,
                "club": p.club,
                "owned": round(p.owned_percent, 1),
                "mine": p.id in mine,
            }
            for p in owned
        ],
        "mine_in_top_owned": sum(1 for p in owned if p.id in mine),
        "podium_slots_mine": sum(
            1 for rows in best.values() for r in rows if r["mine"]
        ),
        "podium_slots": sum(len(rows) for rows in best.values()),
        "never_played": sum(
            1
            for p in market
            if counts.get(p.club, 0) > 0 and p.points_total == -counts.get(p.club, 0)
        ),
        "market_size": len(market),
    }


def fixture_grid(stored: dict, from_round: int) -> list[dict]:
    """Each squad player's next few opponents, scored by how hard they look.

    The multiplier is the same one the round projection uses, so a cell and the
    team sheet cannot disagree. Rounds are walked in order rather than by date:
    a club with a postponed fixture has its rounds out of chronological
    sequence, and sorting by kickoff would show the wrong opponent for the
    wrong week.
    """
    fixtures = mcp._market.fixtures()
    records = OpenFootballClient(timeout=60.0).club_records()
    known = [r for r in records.values() if r.has_history]
    league_ga = sum(r.goals_against_per_match for r in known) / len(known)
    league_gf = sum(r.goals_for_per_match for r in known) / len(known)

    def rates(club):
        record = records.get(club)
        if record is None or not record.has_history:
            return league_ga, league_gf
        return record.goals_against_per_match, record.goals_for_per_match

    rows = []
    for player_id, row in stored["players"].items():
        position = Position(row["position"])
        cells = []
        for rnd, opponent, at_home in upcoming_opponents(
            fixtures, row["club"], from_round, GRID_ROUNDS
        ):
            own_ga, own_gf = rates(row["club"])
            opp_ga, opp_gf = rates(opponent)
            defensive, attacking = fixture_multipliers(
                own_ga, own_gf, opp_ga, opp_gf, league_ga, league_gf, at_home=at_home
            )
            projected = adjust_for_fixture(
                row["season_rate"], position, defensive, attacking
            )
            cells.append(
                {
                    "round": rnd,
                    "opponent": opponent,
                    "at_home": at_home,
                    "projected": round(projected, 1),
                    "edge": round(projected - row["season_rate"], 2),
                }
            )
        rows.append({"id": player_id, **row, "cells": cells})
    rows.sort(key=lambda r: (ORDER[r["position"]], -r["season_rate"]))
    return rows


# --------------------------------------------------------------------------
# The broadcast panel
# --------------------------------------------------------------------------


def hero(data: dict, public: bool = False) -> str:
    """Masthead, the figures that matter, and the season's form so far.

    Deliberately loud and deliberately sparse: five numbers and nothing to
    read. Everything that needs density lives below the hinge.
    """
    me, league, field = data["me"], data["league"], data["field"]
    rounds = data["history"]

    lead = ""
    if league:
        leader = league[0]
        gap = leader["points_total"] - me["points_total"]
        # The gap is Manuel's own number; the name attached to it is not his to
        # publish, so the public build states the total without the team.
        note = (
            f"o líder tem {leader['points_total']}"
            if public
            else f"{esc(leader['team'])} tem {leader['points_total']}"
        )
        lead = f"""      <div class="fig-block">
        <span class="fig-label">Para o 1.º</span>
        <span class="fig-value hot">−{gap}</span>
        <span class="fig-note">{note}</span>
      </div>"""

    bars = ""
    if rounds:
        top = max(r["points_round"] for r in rounds) or 1
        slots = []
        for i, row in enumerate(rounds):
            # Emitted even when empty: a column with one child fewer than its
            # neighbour sits on a different baseline, which reads as a data
            # difference rather than a missing label.
            move = '<span class="move"></span>'
            if i:
                delta = row["position"] - rounds[i - 1]["position"]
                if delta:
                    # Position grew means the team fell down the table.
                    arrow, cls = ("▼", "down") if delta > 0 else ("▲", "up")
                    move = f'<span class="move {cls}">{arrow} {group(abs(delta))}</span>'
            slots.append(
                f"""          <div class="bar-slot">
            <div class="bar{' lead' if row['points_round'] == top else ''}" style="height:{max(9, row['points_round'] / top * 100):.1f}%"><span class="bar-value">{row['points_round']}</span></div>
            <span class="bar-round">J{row['round']} · {group(row['position'])}.º</span>
            {move}
          </div>"""
            )
        first, last = rounds[0], rounds[-1]
        swing = last["position"] - first["position"]
        story = (
            f"lugares {'perdidos' if swing > 0 else 'ganhos'} entre a jornada "
            f"{first['round']} e a {last['round']}"
            if swing
            else "sem movimento na tabela nacional"
        )
        bars = f"""    <div class="form">
      <div class="form-bars">
{chr(10).join(slots)}
      </div>
      <div class="form-story">
        <span class="form-figure">{'▼' if swing > 0 else '▲'} {group(abs(swing))}</span>
        <span class="form-note">{story}</span>
      </div>
    </div>"""

    place = me.get("position_league")
    return f"""  <section class="bcast">
    <div class="mast">
      <div class="mast-name"><span>Melro</span></div>
      <div class="mast-meta">
        <span class="mast-tag">Liga Record</span>
        <span class="mast-round">Jornada {data['round']}</span>
      </div>
    </div>

    <div class="hero">
      <span class="hero-figure">{place if place else '—'}</span>
      <div class="hero-side">
        <span class="hero-of">de {len(league) if league else '—'}</span>
        <span class="hero-label">na liga privada</span>
      </div>
    </div>

    <div class="figs">
      <div class="fig-block">
        <span class="fig-label">No país</span>
        <span class="fig-value">{group(me['position'])}.º</span>
        <span class="fig-note">de cerca de {group(field) if field else '—'}</span>
      </div>
      <div class="fig-block">
        <span class="fig-label">Pontos</span>
        <span class="fig-value">{me['points_total']}</span>
        <span class="fig-note">em duas jornadas</span>
      </div>
{lead}
    </div>
{bars}
  </section>"""


# --------------------------------------------------------------------------
# The results page
# --------------------------------------------------------------------------


def sheet_section(data: dict) -> str:
    rows = data["stored"]["players"]
    coach = data["stored"].get("coach")
    groups = []
    for pos in ("GK", "DEF", "MID", "FWD"):
        members = sorted(
            ((p, rows[p]) for p in XI if rows[p]["position"] == pos),
            key=lambda kv: -kv[1]["projected"],
        )
        if not members:
            continue
        entries = []
        for pid, row in members:
            marks = []
            if pid == CAPTAIN:
                marks.append('<span class="cap">C</span>')
            if "SET" in (row["kickoff"] or ""):
                marks.append(f'<span class="late">{esc(row["kickoff"])}</span>')
            where = "casa" if row["at_home"] else "fora"
            entries.append(
                f"""            <tr>
              <td class="name">{esc(row['name'])} {''.join(marks)}<span class="sub">{esc(row['club'])} · {where} v {esc(row['opponent'])}</span></td>
              <td class="fig strong">{row['projected']:.1f}</td>
            </tr>"""
            )
        groups.append(
            f"""        <tbody>
          <tr><th colspan="2" class="pos-row">{POS_PT[pos]}</th></tr>
{chr(10).join(entries)}
        </tbody>"""
        )

    bench = []
    for n, pid in enumerate(BENCH, 1):
        row = rows[pid]
        bench.append(
            f"""            <tr>
              <td class="ord">{n}</td>
              <td class="name">{esc(row['name'])}<span class="sub">{POS_PT[row['position']]}</span></td>
              <td class="fig">{row['projected']:.1f}</td>
            </tr>"""
        )

    total = sum(rows[p]["projected"] for p in XI) + rows[CAPTAIN]["projected"]
    coach_row = f"{esc(COACH[0])} <span class=\"muted\">{esc(COACH[1])}</span>"
    if coach:
        total += coach["projected_rate"]
        coach_row = (
            f"{esc(coach['name'])} <span class=\"muted\">{esc(coach['club'])} · "
            f"{coach['projected_rate']:.1f}</span>"
        )

    return f"""      <p class="lede">Escalada antes do fecho. Os dois jogadores
      marcados têm o jogo desta jornada em setembro — o Benfica a 9, o Sp. Braga
      a 10 — portanto os pontos deles chegam semanas depois dos outros.</p>
      <div class="cols">
        <div>
          <table class="sheet">
{chr(10).join(groups)}
          </table>
        </div>
        <div class="rail">
          <h3>Suplentes</h3>
          <p class="rail-note">Por ordem de entrada.</p>
          <table class="sheet">
            <tbody>
{chr(10).join(bench)}
            </tbody>
          </table>
          <dl class="facts">
            <dt>Formação</dt><dd>3-4-3</dd>
            <dt>Treinador</dt><dd>{coach_row}</dd>
            <dt>Total</dt><dd><span class="big">{total:.1f}</span> <span class="muted">com capitão e treinador</span></dd>
          </dl>
        </div>
      </div>"""


def exposure_section(data: dict) -> str:
    matches = (data["exposure"] or {}).get("matches") or []
    if not matches:
        return '      <p class="lede">Sem exposição calculada.</p>'
    top = matches[0]
    hedged = data["exposure"].get("hedged_against_itself") or []
    rows = []
    for m in matches:
        names = [p["name"] for p in m["home_players"]] + [
            p["name"] for p in m["away_players"]
        ]
        flag = '<span class="late">dos dois lados</span>' if m["both_sides"] else ""
        rows.append(
            f"""            <tr>
              <td class="lead-fig">{m['count']}</td>
              <td class="name">{esc(m['home'])} <span class="muted">v</span> {esc(m['away'])} {flag}<span class="sub">{esc(', '.join(names))}</span></td>
            </tr>"""
        )
    warning = (
        f"Tens jogadores dos dois lados em {len(hedged)} "
        f"{'jogo' if len(hedged) == 1 else 'jogos'}: a manutenção de baliza é "
        "mutuamente exclusiva, portanto isso amortece um mau resultado tanto "
        "quanto trava um bom."
        if hedged
        else "Não tens jogadores a defrontarem-se."
    )
    return f"""      <p class="lede">O maior risco não é um jogador, é um jogo:
      <strong>{top['count']} dos 23</strong> jogam o {esc(top['home'])}–{esc(top['away'])}.
      Foi assim que a segunda jornada se perdeu — dez jogadores num só jogo, que
      foi adiado. {warning}</p>
      <div class="scroll">
        <table class="data expo">
          <tbody>
{chr(10).join(rows)}
          </tbody>
        </table>
      </div>"""


def grid_section(data: dict) -> str:
    rows = data["grid"]
    if not rows:
        return '      <p class="lede">Sem calendário para as jornadas seguintes.</p>'
    rounds = sorted({c["round"] for r in rows for c in r["cells"]})
    head = "".join(f'<th class="fig">J{r}</th>' for r in rounds)
    body = []
    for row in rows:
        by_round = {c["round"]: c for c in row["cells"]}
        cells = []
        for rnd in rounds:
            cell = by_round.get(rnd)
            if cell is None:
                cells.append('<td class="cell"><span class="muted">—</span></td>')
                continue
            edge = cell["edge"]
            tone = "easy" if edge > 0.25 else "hard" if edge < -0.25 else ""
            where = "vs" if cell["at_home"] else "@"
            cells.append(
                f'<td class="cell {tone}"><span class="cell-opp">{where} '
                f'{esc(cell["opponent"][:11])}</span>'
                f'<span class="cell-fig">{cell["projected"]:.1f}</span></td>'
            )
        body.append(
            f"""            <tr>
              <td class="name">{esc(row['name'])}<span class="sub">{esc(row['club'])}</span></td>
              <td class="sub">{POS_PT[row['position']]}</td>
              {''.join(cells)}
            </tr>"""
        )
    return f"""      <p class="lede">As próximas {len(rounds)} jornadas de cada
      jogador, com o mesmo ajuste ao adversário que a folha usa. Verde é melhor
      que a média dele, vermelho é pior; <strong>@</strong> é fora de casa.</p>
      <div class="scroll">
        <table class="data grid">
          <thead><tr><th>Jogador</th><th>Pos</th>{head}</tr></thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>"""


def differentials_section(data: dict) -> str:
    result = data["differentials"]
    rows = result.get("players") or []
    if not rows:
        return (
            '      <p class="lede">Sem candidatos — '
            f'{esc(result.get("detail", "nada devolvido"))}.</p>'
        )
    body = []
    for row in rows:
        thin = row["matches_played"] < 2
        body.append(
            f"""            <tr>
              <td class="name">{esc(row['name'])}<span class="sub">{esc(row['club'])}</span></td>
              <td class="sub">{POS_PT[row['position']]}</td>
              <td class="fig">{row['owned_percent']:.1f}%</td>
              <td class="fig">{row['observed_rate']:.1f}</td>
              <td class="fig muted">{row['expected_rate']:.1f}</td>
              <td class="fig up strong">+{row['residual']:.2f}</td>
              <td class="fig">{group(row['value'])}</td>
              <td class="sub">{'1 jogo' if thin else row['matches_played']}</td>
            </tr>"""
        )
    slopes = " · ".join(
        f"{pos} {vals['slope']:.2f}"
        for pos, vals in sorted(result.get("fit", {}).items())
    )
    market = data["market"]
    consensus = ""
    if market["mine_in_top_owned"]:
        names = ", ".join(
            esc(p["name"]) for p in market["most_owned"] if p["mine"]
        )
        consensus = (
            f"Os <strong>{market['mine_in_top_owned']} jogadores mais escolhidos "
            f"do país</strong> são meus — {names}. O plantel é o consenso "
            "nacional, e é por isso que empata com quem está à frente em vez de "
            "recuperar: não se ganha terreno a jogar as mesmas cartas. "
        )
    return f"""      <p class="lede">{consensus}A posse prevê a pontuação melhor do que qualquer
      outra coisa no mercado — de <strong>−0,15</strong> pontos por jogo abaixo de
      1% de posse até <strong>5,33</strong> acima de 30%. Por isso comprar quem
      ninguém tem é mau negócio. O que interessa é o resíduo: quem rende acima da
      linha que a sua posse e a sua posição preveem. Declive por posição: {esc(slopes)}.</p>
      <div class="scroll">
        <table class="data">
          <thead><tr>
            <th>Jogador</th><th>Pos</th><th class="fig">Posse</th>
            <th class="fig">Rende</th><th class="fig">Esperado</th>
            <th class="fig">Resíduo</th><th class="fig">Preço</th><th>Jogos</th>
          </tr></thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>
      <p class="footnote">{esc(result.get('caveat', ''))}</p>"""


def ledger_section(data: dict) -> str:
    rows = data["stored"]["players"]
    starters, subs = set(XI), set(BENCH)
    ordered = sorted(
        rows.items(), key=lambda kv: (ORDER[kv[1]["position"]], -kv[1]["projected"])
    )
    body = []
    for pid, row in ordered:
        role = "Titular" if pid in starters else "Suplente" if pid in subs else "Fora"
        if row["actual"] is None:
            result = '<span class="pending">por jogar</span>'
            error = '<span class="muted">—</span>'
        else:
            diff = row.get("error", 0)
            result = f'<span class="strong">{row["actual"]}</span>'
            error = f'<span class="{"up" if diff >= 0 else "down"}">{diff:+.1f}</span>'
        fixture = f"{'casa' if row['at_home'] else 'fora'} v {esc(row['opponent'])}"
        if "SET" in (row["kickoff"] or ""):
            fixture += f' <span class="late">{esc(row["kickoff"])}</span>'
        body.append(
            f"""            <tr>
              <td class="name">{esc(row['name'])}<span class="sub">{esc(row['club'])}</span></td>
              <td class="sub">{POS_PT[row['position']]}</td>
              <td class="role{' is-xi' if role == 'Titular' else ''}">{role}</td>
              <td class="sub">{fixture}</td>
              <td class="fig muted">{row['season_rate']:.1f}</td>
              <td class="fig strong">{row['projected']:.1f}</td>
              <td class="fig">{result}</td>
              <td class="fig">{error}</td>
            </tr>"""
        )
    settled = sum(1 for r in rows.values() if r["actual"] is not None)
    note = (
        f"{settled} de {len(rows)} liquidados"
        if settled
        else "Nenhum jogo desta jornada foi disputado ainda"
    )
    return f"""      <p class="lede">{note}. A coluna <strong>real</strong> só é
      preenchida depois de o clube de cada jogador ter jogado — um clube com jogo
      adiado fica pendente, nunca a zero.</p>
      <div class="scroll">
        <table class="data">
          <thead><tr>
            <th>Jogador</th><th>Pos</th><th>Papel</th><th>Jogo</th>
            <th class="fig">Época</th><th class="fig">Projetado</th>
            <th class="fig">Real</th><th class="fig">Erro</th>
          </tr></thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>"""


def liga_section(data: dict) -> str:
    """The real league table, which is the ground everything else stands on."""
    result = data["table"]
    rows = result.get("table") or []
    if not rows:
        return f'      <p class="lede">{esc(result.get("detail", "sem tabela"))}.</p>'
    mine = {row["club"] for row in data["stored"]["players"].values()}
    body = []
    for r in rows:
        held = sum(1 for p in data["stored"]["players"].values() if p["club"] == r["club"])
        body.append(
            f"""            <tr class="{'is-me' if r['club'] in mine else ''}">
              <td class="fig rank">{r['position']}</td>
              <td class="name">{esc(r['club'])}{f'<span class="sub">{held} teus</span>' if held else ''}</td>
              <td class="fig">{r['played']}</td>
              <td class="fig">{r['won']}</td>
              <td class="fig">{r['drawn']}</td>
              <td class="fig">{r['lost']}</td>
              <td class="fig muted">{r['goals_for']}–{r['goals_against']}</td>
              <td class="fig {'up' if r['goal_difference'] > 0 else 'down' if r['goal_difference'] < 0 else ''}">{r['goal_difference']:+}</td>
              <td class="fig strong">{r['points']}</td>
            </tr>"""
        )
    uneven = (
        " Repara na coluna <strong>J</strong> antes da ordem: o Sp. Braga e o "
        "Gil Vicente têm um jogo a menos, e uma tabela que escondesse isso "
        "cometia o mesmo erro que um total de pontos sem o seu denominador."
        if result.get("uneven_matches")
        else ""
    )
    return f"""      <p class="lede">Calculada do calendário, não pedida ao site —
      {result['matches_scored']} jogos disputados.{uneven}</p>
      <div class="scroll">
        <table class="data">
          <thead><tr>
            <th class="fig">#</th><th>Clube</th><th class="fig">J</th>
            <th class="fig">V</th><th class="fig">E</th><th class="fig">D</th>
            <th class="fig">Golos</th><th class="fig">DG</th><th class="fig">P</th>
          </tr></thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>"""


def best_section(data: dict) -> str:
    """The market's top scorers per position, and how much of it is already his."""
    market = data["market"]
    best = market["best"]
    cols = []
    for pos in ("GK", "DEF", "MID", "FWD"):
        rows = best.get(pos) or []
        entries = chr(10).join(
            f"""            <tr>
              <td class="name">{esc(r['name'])}{'<span class="cap">teu</span>' if r['mine'] else ''}<span class="sub">{esc(r['club'])} · {r['owned']:.0f}% posse{' · 1 jogo só' if r['matches'] < 2 else ''}</span></td>
              <td class="fig strong">{r['rate']:.1f}</td>
            </tr>"""
            for r in rows
        )
        cols.append(
            f"""        <div>
          <h3>{POS_PT[pos]}</h3>
          <table class="data">
            <tbody>
{entries}
            </tbody>
          </table>
        </div>"""
        )
    share = market["never_played"] / market["market_size"]
    return f"""      <p class="lede">Pontos por jogo. Quem tem um jogo só — o Sp. Braga e o
      Gil Vicente, por causa do adiamento — vai marcado, mas não é excluído:
      deixá-los de fora esconderia justamente os clubes que o adiamento
      prejudicou. Tens <strong>{market['podium_slots_mine']} dos
      {market['podium_slots']}</strong> lugares do pódio — o plantel não é o
      problema. Vale a pena lembrar o pano de fundo: <strong>{market['never_played']}
      dos {market['market_size']}</strong> jogadores do mercado ({share:.0%}) ainda não
      pisaram o relvado esta época.</p>
      <div class="quad">
{chr(10).join(cols)}
      </div>"""


def in_portuguese(candidate: str) -> str:
    """Turn a candidate reading into Portuguese for the page.

    stats.py speaks English like the rest of the code, so the translation
    belongs here — the model and the page never have to agree on a language.

    Built from match objects rather than regex backreferences: an earlier
    version wrote \1 into the file as a control byte and silently dropped every
    number, leaving rows reading 'nota  e  golo(s)'.
    """
    import re

    rules = (
        (r"rating (\S+) plus (\d+) goals?", "nota {0} e {1} golo(s)"),
        (r"rating (\S+) and a straight red", "nota {0} e vermelho direto"),
        (r"rating (\S+) and a second yellow", "nota {0} e segundo amarelo"),
        (r"rating (\S+) and an own goal", "nota {0} e autogolo"),
        (r"rating (\S+), blank after 75 minutes",
         "nota {0}, em branco após 75 minutos"),
        (r"^rating (\S+)$", "nota {0}"),
    )
    for pattern, template in rules:
        found = re.search(pattern, candidate)
        if found:
            return template.format(*found.groups())

    plain = {
        "nothing in §10.3 explains this": "nada na §10.3 explica isto",
        "not used (§10.3(i))": "não utilizado (§10.3(i))",
        "or used, with rating and events netting to -1":
            "ou utilizado, com nota e eventos a somar −1",
    }
    return plain.get(candidate, candidate)


def ratings_section(data: dict) -> str:
    """The Record's own mark, recovered by subtracting what the calendar owes.

    The rating was treated as unreachable here for weeks. It is not: §10.1 and
    §10.3 are published, so the objective half is arithmetic and what remains
    is the writers' mark. This is the only place on the page showing a number
    Record produced rather than one this project reasoned its way to.
    """
    result = data["ratings"]
    rows = result.get("players") or []
    if not rows:
        return f'      <p class="lede">{esc(result.get("detail", "sem jornada pontuada"))}.</p>'

    squad = set(data["stored"]["players"])
    mine = [r for r in rows if r["id"] in squad]
    if not mine:
        return '      <p class="lede">Nenhum jogador do plantel jogou esta jornada.</p>'

    body = []
    for row in sorted(mine, key=lambda r: -r["points_round"]):
        if not row["used"]:
            rating, reading = '<span class="muted">—</span>', "não utilizado"
        elif row["certain"]:
            rating = f'<span class="strong">{row["rating"]}</span>'
            reading = "nota lida"
        else:
            rating = '<span class="muted">—</span>'
            reading = esc(in_portuguese(row["candidates"][0]))
        objective = (
            f'{row["objective"]:+}' if row["used"] else '<span class="muted">—</span>'
        )
        body.append(
            f"""            <tr>
              <td class="name">{esc(row['name'])}<span class="sub">{esc(row['club'])}</span></td>
              <td class="sub">{POS_PT[row['position']]}</td>
              <td class="fig strong">{row['points_round']}</td>
              <td class="fig muted">{objective}</td>
              <td class="fig">{rating}</td>
              <td class="sub">{reading}</td>
            </tr>"""
        )

    spread = ""
    counts = {}
    for row in rows:
        if row["rating"] is not None:
            counts[row["rating"]] = counts.get(row["rating"], 0) + 1
    if counts:
        top = max(counts.values())
        bars = "".join(
            f'<span class="tick"><span class="tick-bar" style="height:{counts.get(v, 0) / top * 100:.0f}%"></span>'
            f'<span class="tick-n">{counts.get(v, 0)}</span>'
            f'<span class="tick-v">{v}</span></span>'
            for v in (0, 1, 2, 3, 4, 7)
        )
        spread = f"""      <div class="spread">
        <p class="spread-label">As {result['read']} notas lidas em todo o mercado, média {result['mean_rating']}</p>
        <div class="ticks">{bars}</div>
      </div>"""

    return f"""      <p class="lede">A Record avalia cada jogador de <strong>0 a 5</strong>,
      publica só o total, e o 5 vale 7 pontos (§10.1). Subtraindo o que o
      calendário deve — vitória, baliza a zero, golos sofridos, Jogador da
      Semana — o que sobra é a nota. Golos e cartões escondem-se lá dentro,
      portanto só se afirma uma nota quando mais nada a explica. Jogador da
      Semana desta jornada: <strong>{esc(result['player_of_the_week'] or '—')}</strong>.</p>
      <div class="scroll">
        <table class="data">
          <thead><tr>
            <th>Jogador</th><th>Pos</th><th class="fig">Pontos</th>
            <th class="fig">Calendário</th><th class="fig">Nota</th><th>Leitura</th>
          </tr></thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>
{spread}"""


def seasons_section(data: dict) -> str:
    """What sixty-eight matchdays say about each of the twenty-three."""
    found = data.get("seasons") or {}
    rows = found.get("rows") or []
    if not rows:
        return """      <p class="lede">Ainda sem épocas reconstruídas. Corre
      <code>scripts/build_last_season.py</code> para as duas épocas.</p>"""

    body = chr(10).join(
        f"""            <tr>
              <td class="name">{esc(r['name'])}<span class="sub">{esc(POS_PT[r['position']])} · {esc(r['club'])}</span></td>
              <td class="fig">{'—' if r['rate'] is None else f"{r['rate']:.1f}"}</td>
              <td class="fig">{'—' if r['availability'] is None else f"{r['availability']:.0%}"}</td>
              <td class="fig">{'—' if r['spread'] is None else f"{r['spread']:.1f}"}</td>
              <td class="fig">{'—' if r['owned'] is None else f"{r['owned']:.0f}%"}</td>
              <td class="fig">{r['appearances']}<span class="sub">de {r['of']}</span></td>
              <td class="verdict">{esc(r['label'])}{f'<span class="sub">{esc(r["field"])}</span>' if r['field'] else ''}</td>
            </tr>"""
        for r in rows
    )
    thin = found["thin"]
    return f"""      <p class="lede">O resto desta página olha para esta época, que
      são duas jornadas. Isto são <strong>{found['matchdays']} jornadas</strong> de
      2024/25 e 2025/26, reconstruídas a partir do §10.3 e do registo de cada
      jogo — a diferença entre "fez uma boa tarde no domingo" e "é isto que ele
      é". <strong>Rende</strong> é quanto faz nos dias em que joga;
      <strong>joga</strong> é em que fração das jornadas entrou em campo, que é
      o maior facto sobre um jogador de fantasy e o que uma média esconde.
      {f'<strong>{thin}</strong> dos teus vêm da II Liga ou do estrangeiro e têm evidência a menos para dizerem alguma coisa: a projeção deles é a média do clube com o nome deles.' if thin else ''}</p>
      <table class="data wide">
        <thead>
          <tr><th>jogador</th><th>rende</th><th>joga</th><th>oscila</th>
          <th>posse</th><th>jogos</th><th>o que é</th></tr>
        </thead>
        <tbody>
{body}
        </tbody>
      </table>"""


def league_distribution(data: dict) -> str:
    """The league's shape without its members.

    Anonymising the table row by row would leave thirty meaningless rows and
    still hint at who is who through the ordering. A distribution answers the
    question a stranger actually has — how competitive is this, and where does
    he sit — and carries no identity at all.
    """
    league, me = data["league"], data["me"]
    if not league or me is None:
        return ""
    totals = sorted(t["points_total"] for t in league)
    top, mine = max(totals), me["points_total"]
    live = [t for t in totals if t > 0]
    span = top or 1
    marks = "\n".join(
        f'          <span class="mark{" is-me" if v == mine else ""}" '
        f'style="left:{v / span * 100:.2f}%"></span>'
        for v in totals
    )
    return f"""      <p class="lede">Trinta equipas, {len(live)} com pontos. Cada
      marca é uma equipa; a vermelha é a minha. Os nomes ficam de fora — são de
      outras pessoas.</p>
      <div class="dist">
        <div class="dist-track">
{marks}
        </div>
        <div class="dist-axis">
          <span class="dist-end">0</span>
          <span class="dist-me" style="left:{mine / span * 100:.2f}%">{mine} · eu</span>
          <span class="dist-end right">{top}</span>
        </div>
      </div>"""


def league_section(data: dict, public: bool = False) -> str:
    league = data["league"]
    if public:
        return league_distribution(data)
    if not league:
        return """      <p class="lede">A liga privada não foi lida — define
      <code>LIGA_RECORD_LEAGUE</code> no ambiente com o guid da liga.</p>"""
    body = []
    for t in league:
        mine = t["team"] == "Melro"
        dead = t["points_total"] == 0
        classes = " ".join(
            c for c in ("is-me" if mine else "", "is-dead" if dead else "") if c
        )
        body.append(
            f"""            <tr class="{classes}">
              <td class="fig rank">{t['position_league']}</td>
              <td class="name">{esc(t['team'])}<span class="sub">{esc(t['user'])}</span></td>
              <td class="fig strong">{t['points_total']}</td>
              <td class="fig muted">{group(t['position'])}</td>
            </tr>"""
        )
    return f"""      <div class="scroll">
        <table class="data">
          <thead><tr><th class="fig">#</th><th>Equipa</th><th class="fig">Pontos</th><th class="fig">No país</th></tr></thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>"""


OPEN_QUESTIONS = [
    (
        "Um jogo adiado devolve os pontos?",
        "Por verificar",
        "Quando um jogo não se realiza, os 57 jogadores dos dois clubes ficam a 0 "
        "— enquanto 48,5% dos jogadores dos clubes que jogaram estão a −1. O −1 é "
        "o código para «não jogou», portanto o 0 parece ser «ainda não atribuído». "
        "É leitura, não é prova.",
    ),
    (
        "Sp. Braga–Gil Vicente da jornada 2",
        "Sem data",
        "Adiado a 16 de agosto por um surto gastrointestinal no plantel do Braga. "
        "A Liga Portugal ainda não marcou nova data. Dez dos 23 jogadores do "
        "plantel dessa jornada eram destes dois clubes.",
    ),
    (
        "Pontuação do treinador",
        "Não calculável",
        "Ajustada contra vitórias, empates, manutenções de baliza e margens chega "
        "a R² 0,85, com desvios até 3,8 e sem estrutura inteira. Uma pontuação "
        "calculada dos resultados encaixaria exatamente. O resíduo comporta-se "
        "como a nota editorial que domina a pontuação dos jogadores.",
    ),
    (
        "Modelo de preços",
        "Sem dados",
        "Ao fim de duas jornadas, 0 de 498 jogadores mudaram de preço. As bandas "
        "de ±50 000 € que o servidor projeta continuam sem nada contra que ser "
        "testadas.",
    ),
]


def questions_section() -> str:
    return "\n".join(
        f"""        <div class="q">
          <h3>{esc(title)}</h3>
          <p class="q-state">{esc(state)}</p>
          <p>{body}</p>
        </div>"""
        for title, state, body in OPEN_QUESTIONS
    )


head_body = '<title>{title}</title>\n<link rel="preconnect" href="https://fonts.googleapis.com">\n<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anton&family=IBM+Plex+Sans+Condensed:wght@400;500;600;700&family=Instrument+Serif&display=swap">\n<style>\n/* Paper, below the hinge. The reader\'s theme moves these and nothing else. */\n/* Declared so the browser knows this page handles its own themes. Without it\n   Chrome\'s automatic dark mode forces a scheme on top of ours, and it reaches\n   inside tables: on a light ground the paper stayed light while every table\n   inherited dark ink, which renders the player names invisible. */\n:root {{\n  color-scheme: light;\n  --paper: #eceae5;\n  --ink:   #16150f;\n  --dim:   #6a6860;\n  --rule:  #c9c6bd;\n  --hair:  #dcd9d1;\n  --wash:  #e3e0d9;\n  --red:   #a8112a;\n  --green: #1f6b4a;\n}}\n@media (prefers-color-scheme: dark) {{\n  :root:not([data-theme="light"]) {{\n    color-scheme: dark;\n    --paper: #14140f;\n    --ink:   #efece4;\n    --dim:   #918e85;\n    --rule:  #3a3931;\n    --hair:  #2a2923;\n    --wash:  #1e1e17;\n    --red:   #e0374f;\n    --green: #4fa87c;\n  }}\n}}\n:root[data-theme="dark"] {{\n  color-scheme: dark;\n  --paper: #14140f;\n  --ink:   #efece4;\n  --dim:   #918e85;\n  --rule:  #3a3931;\n  --hair:  #2a2923;\n  --wash:  #1e1e17;\n  --red:   #e0374f;\n  --green: #4fa87c;\n}}\n\n* {{ box-sizing: border-box; }}\nbody {{\n  margin: 0;\n  background: var(--paper);\n  color: var(--ink);\n  font-family: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;\n  font-size: 16.5px;\n  line-height: 1.5;\n  -webkit-font-smoothing: antialiased;\n}}\n.page {{ max-width: 1020px; margin: 0 auto; }}\n.body {{ padding: 0 32px 64px; }}\n\n/* ------------------------------------------------------------------ *\n * The broadcast panel. A fixed graphic: it does not follow the        *\n * reader\'s theme, the way a front page\'s photograph is the photograph *\n * whatever paper it is printed on.                                    *\n * ------------------------------------------------------------------ */\n.bcast {{\n  --bg:   #0a0d14;\n  --sunk: #141a27;\n  --bink: #ffffff;\n  --bdim: #8b95a8;\n  --hot:  #ff2d46;\n  --mint: #00d9a3;\n  background: var(--bg); color: var(--bink); padding-bottom: 32px;\n}}\n.mast {{ display: flex; align-items: stretch; height: 82px; }}\n.mast-name {{\n  background: var(--hot); display: flex; align-items: center;\n  padding: 0 40px 0 32px;\n  clip-path: polygon(0 0, 100% 0, calc(100% - 22px) 100%, 0 100%);\n}}\n.mast-name span {{ font-family: Anton, Impact, "Arial Narrow", sans-serif; font-size: 44px; line-height: 1; }}\n.mast-meta {{ flex: 1; display: flex; align-items: center; justify-content: flex-end; gap: 20px; padding: 0 32px; }}\n.mast-tag {{ font-size: 12.5px; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; color: var(--bdim); }}\n.mast-round {{ font-family: Anton, Impact, sans-serif; font-size: 25px; letter-spacing: .04em; }}\n\n.hero {{ display: flex; align-items: flex-end; gap: 26px; padding: 30px 32px 0; }}\n.hero-figure {{ font-family: Anton, Impact, sans-serif; font-size: 178px; line-height: .78; letter-spacing: -.03em; color: var(--hot); }}\n.hero-side {{ display: flex; flex-direction: column; gap: 3px; padding-bottom: 15px; }}\n.hero-of {{ font-family: Anton, Impact, sans-serif; font-size: 38px; line-height: 1; }}\n.hero-label {{ font-size: 16px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; color: var(--bdim); }}\n\n.figs {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 3px; padding: 28px 32px 0; }}\n.fig-block {{ background: var(--sunk); padding: 15px 19px 13px; }}\n.fig-label {{ display: block; font-size: 11.5px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; color: var(--bdim); }}\n.fig-value {{ display: block; font-family: Anton, Impact, sans-serif; font-size: 40px; line-height: 1.15; font-variant-numeric: tabular-nums; }}\n.fig-value.hot {{ color: var(--hot); }}\n.fig-note {{ display: block; font-size: 14.5px; color: var(--bdim); }}\n\n.form {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 3px; padding: 28px 32px 0; }}\n.form-bars {{ display: grid; grid-auto-flow: column; grid-auto-columns: 126px; grid-template-rows: 140px auto auto; gap: 0 3px; }}\n.bar-slot {{ display: grid; grid-row: 1 / -1; grid-template-rows: subgrid; justify-items: center; align-items: end; }}\n.bar {{ width: 100%; background: var(--sunk); display: flex; align-items: flex-start; justify-content: center; padding-top: 9px; align-self: end; }}\n.bar.lead {{ background: var(--hot); }}\n.bar-value {{ font-family: Anton, Impact, sans-serif; font-size: 44px; line-height: 1; }}\n.bar-round {{ font-size: 12.5px; font-weight: 600; letter-spacing: .08em; color: var(--bdim); padding-top: 7px; }}\n.move {{ display: block; font-size: 12px; font-weight: 600; line-height: 1.4; min-height: 1.4em; }}\n.move.down {{ color: var(--hot); }}\n.move.up {{ color: var(--mint); }}\n.form-story {{\n  background: var(--sunk); display: flex; flex-direction: column;\n  justify-content: center; gap: 1px; padding: 0 28px;\n  clip-path: polygon(0 0, 100% 0, 100% 100%, 26px 100%);\n}}\n.form-figure {{ font-family: Anton, Impact, sans-serif; font-size: 46px; line-height: 1; color: var(--hot); }}\n.form-note {{ font-size: 15.5px; color: var(--bdim); }}\n\n/* The hinge: graphics above, type below. */\n.hinge {{ height: 5px; background: var(--ink); }}\n\n/* ------------------------------------------------------------------ *\n * The results page. No boxes, no shadows: rules and weight only.      *\n * ------------------------------------------------------------------ */\nsection {{ padding-top: 34px; }}\nh2 {{\n  font-family: "Instrument Serif", Georgia, serif; font-weight: 400;\n  font-size: 31px; line-height: 1.1; margin: 0 0 3px; letter-spacing: -.01em;\n  text-wrap: balance;\n}}\n.rule {{ height: 2px; background: var(--ink); margin-bottom: 14px; }}\n.lede {{ margin: 0 0 16px; max-width: 68ch; color: var(--dim); }}\n.lede strong {{ color: var(--ink); font-weight: 600; }}\n.footnote {{ margin: 12px 0 0; font-size: 14.5px; color: var(--dim); max-width: 68ch; }}\ncode {{ font-family: ui-monospace, monospace; font-size: 14px; }}\n\n.cols {{ display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 0 34px; }}\n@media (max-width: 780px) {{ .cols {{ grid-template-columns: 1fr; gap: 28px; }} .rail {{ border-left: none; padding-left: 0; }} }}\n.rail {{ border-left: 1px solid var(--rule); padding-left: 26px; }}\n.rail h3 {{ font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 23px; margin: 0; }}\n.rail-note {{ margin: 0 0 4px; font-size: 14px; color: var(--dim); }}\n\n.scroll {{ overflow-x: auto; }}\ntable {{ width: 100%; border-collapse: collapse; }}\nth {{\n  text-align: left; font-size: 11.5px; font-weight: 600; letter-spacing: .14em;\n  text-transform: uppercase; color: var(--dim); padding: 0 12px 6px 0;\n  border-bottom: 1px solid var(--rule); white-space: nowrap;\n}}\ntd {{ padding: 8px 12px 8px 0; border-bottom: 1px solid var(--hair); vertical-align: baseline; }}\ntbody tr:last-child td {{ border-bottom: none; }}\n.fig {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}\nth.fig {{ text-align: right; }}\n.name {{ font-weight: 600; }}\n.sub {{ font-size: 14px; color: var(--dim); font-weight: 400; }}\ntd.name .sub {{ display: block; }}\n.muted {{ color: var(--dim); font-weight: 400; }}\n.strong {{ font-weight: 600; }}\n.up {{ color: var(--green); }}\n.down {{ color: var(--red); }}\n.pending {{ font-size: 14px; color: var(--dim); font-style: italic; }}\n\n.pos-row {{\n  font-family: "Instrument Serif", Georgia, serif; font-size: 17px;\n  font-weight: 400; letter-spacing: .03em; text-transform: none;\n  color: var(--ink); border-bottom: 1px solid var(--rule); padding-top: 18px;\n}}\n.ord {{ color: var(--dim); font-size: 13px; width: 16px; padding-right: 8px; }}\n.cap, .late {{ font-size: 11px; font-weight: 600; letter-spacing: .04em; padding: 1px 5px; white-space: nowrap; }}\n.cap {{ background: var(--red); color: #fff; }}\n.late {{ color: var(--red); border: 1px solid currentColor; }}\n.role.is-xi {{ font-weight: 600; }}\n.role.is-xi::before {{ content: ""; display: inline-block; width: 3px; height: 11px; background: var(--red); margin-right: 7px; vertical-align: -1px; }}\n\n.lead-fig {{ font-family: "Instrument Serif", Georgia, serif; font-size: 30px; width: 46px; text-align: right; padding-right: 16px; }}\n\n.facts {{ margin: 20px 0 0; padding-top: 12px; border-top: 2px solid var(--ink); display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 7px 14px; font-size: 15px; }}\n.facts dt {{ font-size: 11.5px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--dim); }}\n.facts dd {{ margin: 0; font-weight: 600; min-width: 0; }}\n.facts .big {{ font-family: "Instrument Serif", Georgia, serif; font-size: 29px; font-weight: 400; }}\n\n.grid td.cell {{ text-align: center; min-width: 92px; padding: 6px 8px; }}\n.cell-opp {{ display: block; font-size: 12px; color: var(--dim); white-space: nowrap; }}\n.cell-fig {{ display: block; font-weight: 600; font-variant-numeric: tabular-nums; }}\n.cell.easy {{ background: color-mix(in srgb, var(--green) 13%, transparent); }}\n.cell.easy .cell-fig {{ color: var(--green); }}\n.cell.hard {{ background: color-mix(in srgb, var(--red) 13%, transparent); }}\n.cell.hard .cell-fig {{ color: var(--red); }}\n\ntr.is-me td {{ background: var(--wash); }}\ntr.is-me .name {{ font-weight: 700; }}\ntr.is-me td:first-child {{ box-shadow: inset 3px 0 0 var(--red); }}\ntr.is-dead td, tr.is-dead .name {{ color: var(--dim); font-weight: 400; }}\n.rank {{ width: 38px; color: var(--dim); }}\n\n.dist {{ padding: 6px 0 0; }}\n.dist-track {{ position: relative; height: 34px; border-bottom: 1px solid var(--rule); }}\n.mark {{ position: absolute; bottom: 0; width: 2px; height: 20px; background: var(--rule); transform: translateX(-1px); }}\n.mark.is-me {{ background: var(--red); height: 34px; width: 3px; }}\n.dist-axis {{ position: relative; height: 22px; margin-top: 6px; font-size: 12.5px; color: var(--dim); font-variant-numeric: tabular-nums; }}\n.dist-end {{ position: absolute; left: 0; }}\n.dist-end.right {{ left: auto; right: 0; }}\n.dist-me {{ position: absolute; transform: translateX(-50%); color: var(--red); font-weight: 600; white-space: nowrap; }}\n\n.quad {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 0 30px; }}\n.quad h3 {{ font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 21px; margin: 0 0 2px; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }}\n.quad .cap {{ margin-left: 6px; }}\n.spread {{ margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--rule); }}\n.spread-label {{ margin: 0 0 10px; font-size: 13px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--dim); }}\n.ticks {{ display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); grid-template-rows: 76px auto auto; gap: 0 14px; max-width: 460px; }}\n.tick {{ display: grid; grid-row: 1 / -1; grid-template-rows: subgrid; justify-items: center; align-items: end; }}\n.tick-bar {{ width: 100%; background: var(--red); align-self: end; min-height: 2px; }}\n.tick-n {{ font-size: 12px; font-variant-numeric: tabular-nums; color: var(--dim); padding-top: 4px; }}\n.tick-v {{ font-family: "Instrument Serif", Georgia, serif; font-size: 19px; padding-top: 2px; }}\n.questions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 4px 30px; }}\n.q h3 {{ font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 21px; margin: 0; text-wrap: balance; }}\n.q-state {{ margin: 1px 0 7px; font-size: 11.5px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--red); }}\n.q p {{ margin: 0; font-size: 14.5px; color: var(--dim); }}\n\nfooter {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--rule); font-size: 13.5px; color: var(--dim); display: flex; flex-wrap: wrap; gap: 4px 20px; }}\na {{ color: var(--red); }}\n:focus-visible {{ outline: 2px solid var(--red); outline-offset: 2px; }}\n@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}\n\n/* The page nav. Five links, current one marked, and nothing clever: it has to\n   work from a file:// URL with no server behind it. */\n.nav {{\n  display: flex; flex-wrap: wrap; gap: 0;\n  border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);\n  margin: 0 0 2.4rem 0; background: var(--wash);\n}}\n.nav a {{\n  padding: 0.7rem 1.15rem; text-decoration: none; color: var(--dim);\n  font: 600 0.78rem/1 "IBM Plex Sans Condensed", system-ui, sans-serif;\n  letter-spacing: 0.09em; text-transform: uppercase;\n  border-right: 1px solid var(--hair);\n}}\n.nav a:hover {{ color: var(--ink); background: var(--paper); }}\n.nav a[aria-current] {{ color: var(--paper); background: var(--ink); }}\n.slim {{\n  display: flex; align-items: baseline; justify-content: space-between;\n  gap: 1rem; padding: 1.6rem 0 1.1rem 0;\n}}\n.slim .mark {{\n  font: 400 2.1rem/1 Anton, Impact, sans-serif; letter-spacing: 0.01em;\n  text-transform: uppercase;\n}}\n.slim .where {{ color: var(--dim); font-size: 0.82rem; }}\n</style>'


PAGES = [
    (
        "index",
        "A equipa",
        [
            ("A folha entregue", "sheet"),
            ("Onde está o risco", "exposure"),
            ("As próximas jornadas", "grid"),
        ],
    ),
    (
        "proxima",
        "A próxima jornada",
        [("O que o modelo faria", "model"), ("As próximas jornadas", "grid")],
    ),
    (
        "jogadores",
        "Os jogadores",
        [
            ("O que sabemos de cada um", "seasons"),
            ("Os melhores até agora", "best"),
            ("Quem rende mais do que a posse dele", "differentials"),
            ("A nota do Record", "ratings"),
        ],
    ),
    (
        "decisoes",
        "As decisões",
        [
            ("O treinador", "coach"),
            ("As Férias", "holiday"),
            ("A corrida ao melhor marcador", "scorer"),
        ],
    ),
    (
        "ligas",
        "As ligas",
        [("A liga privada", "league"), ("A Primeira Liga", "liga")],
    ),
    (
        "modelo",
        "O modelo",
        [("Projetado contra real", "ledger"), ("Por resolver", "questions")],
    ),
]

#: Which renderer draws each section. Kept as names rather than function
#: references so PAGES stays readable as a table of contents.
SECTIONS = {
    "sheet": lambda data, public: sheet_section(data),
    "exposure": lambda data, public: exposure_section(data),
    "grid": lambda data, public: grid_section(data),
    "model": lambda data, public: model_section(data),
    "seasons": lambda data, public: seasons_section(data),
    "best": lambda data, public: best_section(data),
    "differentials": lambda data, public: differentials_section(data),
    "ratings": lambda data, public: ratings_section(data),
    "coach": lambda data, public: coach_section(data),
    "holiday": lambda data, public: holiday_section(data),
    "scorer": lambda data, public: scorer_section(data),
    "league": lambda data, public: league_section(data, public),
    "liga": lambda data, public: liga_section(data),
    "ledger": lambda data, public: ledger_section(data),
    "questions": lambda data, public: (
        '      <div class="questions">' + chr(10) + questions_section() + chr(10)
        + "      </div>"
    ),
}


# A page naming a section that has no renderer used to fail halfway through
# writing that page, leaving a truncated file behind. Checked at import so it
# fails before anything is written.
_named = {key for _, _, blocks in PAGES for _, key in blocks}
assert _named <= set(SECTIONS), f"no renderer for {sorted(_named - set(SECTIONS))}"


def head(title: str) -> str:
    return head_body.format(title=title)


def visible(public: bool) -> list[tuple[str, str, list]]:
    """The pages this build actually writes.

    The public build has no league page — the private league is the one thing
    that is not ours to publish — and the nav has to be built from the same
    list, or it links to a file that was never written.
    """
    return [page for page in PAGES if not (public and page[0] == "ligas")]


def nav(current: str, public: bool = False) -> str:
    """The same five links on every page, the current one marked.

    Plain relative hrefs, because these pages are opened straight off the disk
    as often as they are served: anything needing a base URL or a router would
    work in one of those two places and not the other.
    """
    links = chr(10).join(
        f'      <a href="{slug}.html"{" aria-current=\"page\"" if slug == current else ""}>'
        f"{esc(title)}</a>"
        for slug, title, _ in visible(public)
    )
    return f'    <nav class="nav">{chr(10)}{links}{chr(10)}    </nav>'


def slim_header(data: dict, title: str) -> str:
    """A line instead of a hero, for the pages that are not the front one."""
    return f"""  <div class="slim">
    <span class="mark">Melro</span>
    <span class="where">{esc(title)} · jornada {data['round']}</span>
  </div>"""


def render_page(data: dict, slug: str, title: str, blocks, public: bool = False) -> str:
    stamp = (data["as_of"] or "")[:16].replace("T", " ")
    recorded = data["stored"]["recorded_at"][:16].replace("T", " ")
    top = hero(data, public) if slug == "index" else slim_header(data, title)
    body = chr(10).join(
        f"""    <section>
      <h2>{esc(heading)}</h2><div class="rule"></div>
{SECTIONS[key](data, public)}
    </section>"""
        for heading, key in blocks
    )
    return f"""{head("Melro · " + title)}

<div class="page">
{top}
  <div class="hinge"></div>
  <div class="body">
{nav(slug, public)}

{body}

    <footer>
      <span>Lido {esc(stamp)} UTC. Projeções registadas a {esc(recorded)} UTC, antes do primeiro pontapé.</span>
      <span><code>scripts/build_dashboard.py</code></span>
    </footer>
  </div>
</div>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, default=3)
    parser.add_argument("--out")
    parser.add_argument(
        "--public",
        action="store_true",
        help="omit everything that belongs to other people, for a public host",
    )
    args = parser.parse_args()

    data = gather(args.round)
    # The private build writes into its own directory, and that whole directory
    # is gitignored. A list of filenames would have to be remembered every time
    # a page is added, and forgetting once put twenty-nine other people's names
    # on a public remote.
    out_dir = Path(args.out) if args.out else (
        ROOT / "docs" if args.public else ROOT / "docs" / "private"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for slug, title, blocks in visible(args.public):
        page = out_dir / f"{slug}.html"
        page.write_text(
            render_page(data, slug, title, blocks, public=args.public),
            encoding="utf-8",
        )
        written.append(page.name)

    settled = sum(1 for r in data["stored"]["players"].values() if r["actual"] is not None)
    print(
        f"wrote {len(written)} pages to {out_dir} — round {args.round}, "
        f"{settled}/23 settled, {len(data['league'])} in the league"
    )
    print("  " + ", ".join(written))


if __name__ == "__main__":
    main()
