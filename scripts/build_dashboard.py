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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp import server as mcp  # noqa: E402
from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import OpenFootballClient  # noqa: E402
from liga_record_mcp.stats import (  # noqa: E402
    adjust_for_fixture,
    fixture_multipliers,
    matches_played,
    per_match,
    upcoming_opponents,
)

LOG_PATH = ROOT / "data" / "projections.json"
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
        "as_of": national.get("as_of", ""),
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


def render(data: dict, public: bool = False) -> str:
    stamp = (data["as_of"] or "")[:16].replace("T", " ")
    recorded = data["stored"]["recorded_at"][:16].replace("T", " ")
    return f"""<title>Melro · Liga Record</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Anton&family=IBM+Plex+Sans+Condensed:wght@400;500;600;700&family=Instrument+Serif&display=swap">
<style>
/* Paper, below the hinge. The reader's theme moves these and nothing else. */
/* Declared so the browser knows this page handles its own themes. Without it
   Chrome's automatic dark mode forces a scheme on top of ours, and it reaches
   inside tables: on a light ground the paper stayed light while every table
   inherited dark ink, which renders the player names invisible. */
:root {{
  color-scheme: light;
  --paper: #eceae5;
  --ink:   #16150f;
  --dim:   #6a6860;
  --rule:  #c9c6bd;
  --hair:  #dcd9d1;
  --wash:  #e3e0d9;
  --red:   #a8112a;
  --green: #1f6b4a;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --paper: #14140f;
    --ink:   #efece4;
    --dim:   #918e85;
    --rule:  #3a3931;
    --hair:  #2a2923;
    --wash:  #1e1e17;
    --red:   #e0374f;
    --green: #4fa87c;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --paper: #14140f;
  --ink:   #efece4;
  --dim:   #918e85;
  --rule:  #3a3931;
  --hair:  #2a2923;
  --wash:  #1e1e17;
  --red:   #e0374f;
  --green: #4fa87c;
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  font-size: 16.5px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
.page {{ max-width: 1020px; margin: 0 auto; }}
.body {{ padding: 0 32px 64px; }}

/* ------------------------------------------------------------------ *
 * The broadcast panel. A fixed graphic: it does not follow the        *
 * reader's theme, the way a front page's photograph is the photograph *
 * whatever paper it is printed on.                                    *
 * ------------------------------------------------------------------ */
.bcast {{
  --bg:   #0a0d14;
  --sunk: #141a27;
  --bink: #ffffff;
  --bdim: #8b95a8;
  --hot:  #ff2d46;
  --mint: #00d9a3;
  background: var(--bg); color: var(--bink); padding-bottom: 32px;
}}
.mast {{ display: flex; align-items: stretch; height: 82px; }}
.mast-name {{
  background: var(--hot); display: flex; align-items: center;
  padding: 0 40px 0 32px;
  clip-path: polygon(0 0, 100% 0, calc(100% - 22px) 100%, 0 100%);
}}
.mast-name span {{ font-family: Anton, Impact, "Arial Narrow", sans-serif; font-size: 44px; line-height: 1; }}
.mast-meta {{ flex: 1; display: flex; align-items: center; justify-content: flex-end; gap: 20px; padding: 0 32px; }}
.mast-tag {{ font-size: 12.5px; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; color: var(--bdim); }}
.mast-round {{ font-family: Anton, Impact, sans-serif; font-size: 25px; letter-spacing: .04em; }}

.hero {{ display: flex; align-items: flex-end; gap: 26px; padding: 30px 32px 0; }}
.hero-figure {{ font-family: Anton, Impact, sans-serif; font-size: 178px; line-height: .78; letter-spacing: -.03em; color: var(--hot); }}
.hero-side {{ display: flex; flex-direction: column; gap: 3px; padding-bottom: 15px; }}
.hero-of {{ font-family: Anton, Impact, sans-serif; font-size: 38px; line-height: 1; }}
.hero-label {{ font-size: 16px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; color: var(--bdim); }}

.figs {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 3px; padding: 28px 32px 0; }}
.fig-block {{ background: var(--sunk); padding: 15px 19px 13px; }}
.fig-label {{ display: block; font-size: 11.5px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; color: var(--bdim); }}
.fig-value {{ display: block; font-family: Anton, Impact, sans-serif; font-size: 40px; line-height: 1.15; font-variant-numeric: tabular-nums; }}
.fig-value.hot {{ color: var(--hot); }}
.fig-note {{ display: block; font-size: 14.5px; color: var(--bdim); }}

.form {{ display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 3px; padding: 28px 32px 0; }}
.form-bars {{ display: grid; grid-auto-flow: column; grid-auto-columns: 126px; grid-template-rows: 140px auto auto; gap: 0 3px; }}
.bar-slot {{ display: grid; grid-row: 1 / -1; grid-template-rows: subgrid; justify-items: center; align-items: end; }}
.bar {{ width: 100%; background: var(--sunk); display: flex; align-items: flex-start; justify-content: center; padding-top: 9px; align-self: end; }}
.bar.lead {{ background: var(--hot); }}
.bar-value {{ font-family: Anton, Impact, sans-serif; font-size: 44px; line-height: 1; }}
.bar-round {{ font-size: 12.5px; font-weight: 600; letter-spacing: .08em; color: var(--bdim); padding-top: 7px; }}
.move {{ display: block; font-size: 12px; font-weight: 600; line-height: 1.4; min-height: 1.4em; }}
.move.down {{ color: var(--hot); }}
.move.up {{ color: var(--mint); }}
.form-story {{
  background: var(--sunk); display: flex; flex-direction: column;
  justify-content: center; gap: 1px; padding: 0 28px;
  clip-path: polygon(0 0, 100% 0, 100% 100%, 26px 100%);
}}
.form-figure {{ font-family: Anton, Impact, sans-serif; font-size: 46px; line-height: 1; color: var(--hot); }}
.form-note {{ font-size: 15.5px; color: var(--bdim); }}

/* The hinge: graphics above, type below. */
.hinge {{ height: 5px; background: var(--ink); }}

/* ------------------------------------------------------------------ *
 * The results page. No boxes, no shadows: rules and weight only.      *
 * ------------------------------------------------------------------ */
section {{ padding-top: 34px; }}
h2 {{
  font-family: "Instrument Serif", Georgia, serif; font-weight: 400;
  font-size: 31px; line-height: 1.1; margin: 0 0 3px; letter-spacing: -.01em;
  text-wrap: balance;
}}
.rule {{ height: 2px; background: var(--ink); margin-bottom: 14px; }}
.lede {{ margin: 0 0 16px; max-width: 68ch; color: var(--dim); }}
.lede strong {{ color: var(--ink); font-weight: 600; }}
.footnote {{ margin: 12px 0 0; font-size: 14.5px; color: var(--dim); max-width: 68ch; }}
code {{ font-family: ui-monospace, monospace; font-size: 14px; }}

.cols {{ display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 0 34px; }}
@media (max-width: 780px) {{ .cols {{ grid-template-columns: 1fr; gap: 28px; }} .rail {{ border-left: none; padding-left: 0; }} }}
.rail {{ border-left: 1px solid var(--rule); padding-left: 26px; }}
.rail h3 {{ font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 23px; margin: 0; }}
.rail-note {{ margin: 0 0 4px; font-size: 14px; color: var(--dim); }}

.scroll {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{
  text-align: left; font-size: 11.5px; font-weight: 600; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim); padding: 0 12px 6px 0;
  border-bottom: 1px solid var(--rule); white-space: nowrap;
}}
td {{ padding: 8px 12px 8px 0; border-bottom: 1px solid var(--hair); vertical-align: baseline; }}
tbody tr:last-child td {{ border-bottom: none; }}
.fig {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
th.fig {{ text-align: right; }}
.name {{ font-weight: 600; }}
.sub {{ font-size: 14px; color: var(--dim); font-weight: 400; }}
td.name .sub {{ display: block; }}
.muted {{ color: var(--dim); font-weight: 400; }}
.strong {{ font-weight: 600; }}
.up {{ color: var(--green); }}
.down {{ color: var(--red); }}
.pending {{ font-size: 14px; color: var(--dim); font-style: italic; }}

.pos-row {{
  font-family: "Instrument Serif", Georgia, serif; font-size: 17px;
  font-weight: 400; letter-spacing: .03em; text-transform: none;
  color: var(--ink); border-bottom: 1px solid var(--rule); padding-top: 18px;
}}
.ord {{ color: var(--dim); font-size: 13px; width: 16px; padding-right: 8px; }}
.cap, .late {{ font-size: 11px; font-weight: 600; letter-spacing: .04em; padding: 1px 5px; white-space: nowrap; }}
.cap {{ background: var(--red); color: #fff; }}
.late {{ color: var(--red); border: 1px solid currentColor; }}
.role.is-xi {{ font-weight: 600; }}
.role.is-xi::before {{ content: ""; display: inline-block; width: 3px; height: 11px; background: var(--red); margin-right: 7px; vertical-align: -1px; }}

.lead-fig {{ font-family: "Instrument Serif", Georgia, serif; font-size: 30px; width: 46px; text-align: right; padding-right: 16px; }}

.facts {{ margin: 20px 0 0; padding-top: 12px; border-top: 2px solid var(--ink); display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 7px 14px; font-size: 15px; }}
.facts dt {{ font-size: 11.5px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--dim); }}
.facts dd {{ margin: 0; font-weight: 600; min-width: 0; }}
.facts .big {{ font-family: "Instrument Serif", Georgia, serif; font-size: 29px; font-weight: 400; }}

.grid td.cell {{ text-align: center; min-width: 92px; padding: 6px 8px; }}
.cell-opp {{ display: block; font-size: 12px; color: var(--dim); white-space: nowrap; }}
.cell-fig {{ display: block; font-weight: 600; font-variant-numeric: tabular-nums; }}
.cell.easy {{ background: color-mix(in srgb, var(--green) 13%, transparent); }}
.cell.easy .cell-fig {{ color: var(--green); }}
.cell.hard {{ background: color-mix(in srgb, var(--red) 13%, transparent); }}
.cell.hard .cell-fig {{ color: var(--red); }}

tr.is-me td {{ background: var(--wash); }}
tr.is-me .name {{ font-weight: 700; }}
tr.is-me td:first-child {{ box-shadow: inset 3px 0 0 var(--red); }}
tr.is-dead td, tr.is-dead .name {{ color: var(--dim); font-weight: 400; }}
.rank {{ width: 38px; color: var(--dim); }}

.dist {{ padding: 6px 0 0; }}
.dist-track {{ position: relative; height: 34px; border-bottom: 1px solid var(--rule); }}
.mark {{ position: absolute; bottom: 0; width: 2px; height: 20px; background: var(--rule); transform: translateX(-1px); }}
.mark.is-me {{ background: var(--red); height: 34px; width: 3px; }}
.dist-axis {{ position: relative; height: 22px; margin-top: 6px; font-size: 12.5px; color: var(--dim); font-variant-numeric: tabular-nums; }}
.dist-end {{ position: absolute; left: 0; }}
.dist-end.right {{ left: auto; right: 0; }}
.dist-me {{ position: absolute; transform: translateX(-50%); color: var(--red); font-weight: 600; white-space: nowrap; }}

.quad {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 0 30px; }}
.quad h3 {{ font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 21px; margin: 0 0 2px; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }}
.quad .cap {{ margin-left: 6px; }}
.spread {{ margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--rule); }}
.spread-label {{ margin: 0 0 10px; font-size: 13px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--dim); }}
.ticks {{ display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); grid-template-rows: 76px auto auto; gap: 0 14px; max-width: 460px; }}
.tick {{ display: grid; grid-row: 1 / -1; grid-template-rows: subgrid; justify-items: center; align-items: end; }}
.tick-bar {{ width: 100%; background: var(--red); align-self: end; min-height: 2px; }}
.tick-n {{ font-size: 12px; font-variant-numeric: tabular-nums; color: var(--dim); padding-top: 4px; }}
.tick-v {{ font-family: "Instrument Serif", Georgia, serif; font-size: 19px; padding-top: 2px; }}
.questions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 4px 30px; }}
.q h3 {{ font-family: "Instrument Serif", Georgia, serif; font-weight: 400; font-size: 21px; margin: 0; text-wrap: balance; }}
.q-state {{ margin: 1px 0 7px; font-size: 11.5px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; color: var(--red); }}
.q p {{ margin: 0; font-size: 14.5px; color: var(--dim); }}

footer {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--rule); font-size: 13.5px; color: var(--dim); display: flex; flex-wrap: wrap; gap: 4px 20px; }}
a {{ color: var(--red); }}
:focus-visible {{ outline: 2px solid var(--red); outline-offset: 2px; }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
</style>

<div class="page">
{hero(data, public)}
  <div class="hinge"></div>
  <div class="body">

    <section>
      <h2>A folha entregue</h2><div class="rule"></div>
{sheet_section(data)}
    </section>

    <section>
      <h2>Onde está o risco</h2><div class="rule"></div>
{exposure_section(data)}
    </section>

    <section>
      <h2>As próximas jornadas</h2><div class="rule"></div>
{grid_section(data)}
    </section>

    <section>
      <h2>Os melhores até agora</h2><div class="rule"></div>
{best_section(data)}
    </section>

    <section>
      <h2>Quem rende mais do que a posse dele</h2><div class="rule"></div>
{differentials_section(data)}
    </section>

    <section>
      <h2>A nota do Record</h2><div class="rule"></div>
{ratings_section(data)}
    </section>

    <section>
      <h2>Projetado contra real</h2><div class="rule"></div>
{ledger_section(data)}
    </section>

    <section>
      <h2>A liga privada</h2><div class="rule"></div>
{league_section(data, public)}
    </section>

    <section>
      <h2>A Primeira Liga</h2><div class="rule"></div>
{liga_section(data)}
    </section>

    <section>
      <h2>Por resolver</h2><div class="rule"></div>
      <div class="questions">
{questions_section()}
      </div>
    </section>

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
    # index.html so GitHub Pages serves it at the bare /docs URL.
    default = "index.html" if args.public else "dashboard.html"
    out = Path(args.out or ROOT / "docs" / default)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data, public=args.public), encoding="utf-8")
    settled = sum(1 for r in data["stored"]["players"].values() if r["actual"] is not None)
    print(
        f"wrote {out} — round {args.round}, {settled}/23 settled, "
        f"{len(data['league'])} in the league"
    )


if __name__ == "__main__":
    main()
