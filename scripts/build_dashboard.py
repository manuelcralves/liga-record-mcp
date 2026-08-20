"""Render the season dashboard from live standings and the projection log.

The point of this page is the gap between two columns: what was projected
before a round, and what actually happened. Everything else on it is context
for reading that gap.

So the projection ledger never shows a blank cell for a result that has not
arrived. A blank reads as zero, and a zero here would be a lie — it is exactly
the confusion between "pending" and "scored" that cost this project a day.
Unsettled rows carry an explicit pending state instead.

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
    national = mcp.standings(team="Melro")

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

    field = mcp.standings(page_size=20).get("field_size_estimate")

    history = []
    if HISTORY_PATH.exists():
        raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["rounds"]
        for key, entry in sorted(raw.items(), key=lambda kv: int(kv[0])):
            row = entry["teams"].get(str(MY_TEAM_ID))
            if row:
                history.append({"round": int(key), **row})

    differentials = mcp.find_differentials(limit=10, min_matches=1)

    return {
        "round": round_number,
        "stored": stored,
        "league": (league or {}).get("teams") or [],
        "me": me,
        "field": field,
        "history": history,
        "differentials": differentials,
        "as_of": national.get("as_of", ""),
    }


def history_section(data: dict) -> str:
    """Points per round, with the national position each one left behind."""
    rounds = data["history"]
    if not rounds:
        return """      <p class="section-note">Sem histórico registado —
      corre <code>scripts/record_history.py</code>.</p>"""

    top = max(r["points_round"] for r in rounds) or 1
    bars = []
    for i, row in enumerate(rounds):
        height = max(4, row["points_round"] / top * 100)
        move = ""
        if i:
            delta = row["position"] - rounds[i - 1]["position"]
            if delta:
                # Position grew means the team fell down the table.
                arrow, cls = ("▼", "down") if delta > 0 else ("▲", "up")
                move = f'<span class="move {cls}">{arrow} {group(abs(delta))}</span>'
        bars.append(
            f"""          <div class="bar-slot">
            <span class="bar-value">{row['points_round']}</span>
            <div class="bar" style="height:{height:.1f}%"></div>
            <span class="bar-round">J{row['round']}</span>
            <span class="bar-pos">{group(row['position'])}.º</span>
            {move}
          </div>"""
        )

    first, last = rounds[0], rounds[-1]
    swing = last["position"] - first["position"]
    story = (
        f"Da jornada {first['round']} à {last['round']} a posição nacional "
        f"{'caiu' if swing > 0 else 'subiu'} {group(abs(swing))} lugares."
        if swing
        else "A posição nacional não se moveu."
    )
    return f"""      <p class="section-note">{story} A barra é o que rendeu cada
      jornada; por baixo, a posição no país depois dela.</p>
      <div class="chart">
        <div class="bars">
{chr(10).join(bars)}
        </div>
      </div>"""


def differentials_section(data: dict) -> str:
    """Players out-producing the attention they get."""
    result = data["differentials"]
    rows = result.get("players") or []
    if not rows:
        return f"""      <p class="section-note">Sem candidatos —
      {esc(result.get('detail', 'nada devolvido'))}.</p>"""

    body = []
    for row in rows:
        thin = row["matches_played"] < 2
        flag = (
            '<span class="pending"><span class="dot"></span>1 jogo</span>'
            if thin
            else f'<span class="num">{row["matches_played"]}</span>'
        )
        body.append(
            f"""            <tr>
              <td class="cell-name">{esc(row['name'])}<span class="cell-club">{esc(row['club'])}</span></td>
              <td><span class="pos-chip">{POS_PT[row['position']]}</span></td>
              <td class="num">{row['owned_percent']:.1f}%</td>
              <td class="num">{row['observed_rate']:.1f}</td>
              <td class="num">{row['expected_rate']:.1f}</td>
              <td class="num strong delta over">+{row['residual']:.2f}</td>
              <td class="num">{group(row['value'])}</td>
              <td>{flag}</td>
            </tr>"""
        )
    fit = result.get("fit", {})
    slopes = " · ".join(
        f"{pos} {vals['slope']:.2f}" for pos, vals in sorted(fit.items())
    )
    return f"""      <p class="section-note">A posse prevê a pontuação melhor do que
      qualquer outra coisa no mercado — de <em>−0,15</em> pontos por jogo abaixo
      de 1% de posse até <em>5,33</em> acima de 30%. Por isso comprar quem
      ninguém tem é mau negócio. O que interessa é o <em>resíduo</em>: quem
      rende acima da linha que a sua posse e posição preveem. Declive por
      posição: {esc(slopes)}.</p>
      <div class="table-wrap">
        <table class="ledger">
          <thead>
            <tr>
              <th>Jogador</th><th>Pos</th><th class="num">Posse</th>
              <th class="num">Rende</th><th class="num">Esperado</th>
              <th class="num">Resíduo</th><th class="num">Preço</th><th>Jogos</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>
      <p class="section-note" style="margin-top:14px">{esc(result.get('caveat', ''))}</p>"""


def summary_cards(data: dict, public: bool = False) -> str:
    me, league, field = data["me"], data["league"], data["field"]
    if me is None:
        return ""
    live = [t for t in league if t["points_total"] > 0]
    cards = [
        (
            "Na liga privada",
            f"{me['position_league']}<span class=\"of\">.º de {len(league)}</span>"
            if me.get("position_league")
            else "—",
            f"{len(live)} equipas com pontos" if league else "",
        ),
        (
            "No país",
            group(me["position"]) + "<span class=\"of\">.º</span>",
            f"de cerca de {group(field)}" if field else "",
        ),
        ("Pontos", f"{me['points_total']}", "acumulados em 2 jornadas"),
    ]
    if league:
        leader = league[0]
        # The gap is Manuel's own number; the name attached to it is not his to
        # publish, so the public build states the total without the team.
        note = (
            f"o 1.º tem {leader['points_total']}"
            if public
            else f"{esc(leader['team'])} tem {leader['points_total']}"
        )
        cards.append(
            (
                "Atraso para o 1.º",
                f"−{leader['points_total'] - me['points_total']}",
                note,
            )
        )
    return "\n".join(
        f"""      <article class="card">
        <p class="card-label">{esc(label)}</p>
        <p class="card-value">{value}</p>
        <p class="card-note">{note}</p>
      </article>"""
        for label, value, note in cards
    )


def sheet_section(data: dict) -> str:
    rows = data["stored"]["players"]
    groups: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for pid in XI:
        row = rows[pid]
        groups[row["position"]].append((pid, row))

    blocks = []
    for pos in ("GK", "DEF", "MID", "FWD"):
        members = sorted(groups[pos], key=lambda kv: -kv[1]["projected"])
        if not members:
            continue
        lines = []
        for pid, row in members:
            late = "SET" in (row["kickoff"] or "")
            marks = []
            if pid == CAPTAIN:
                marks.append('<span class="badge badge-cap" title="Capitão">C</span>')
            if late:
                marks.append(
                    f'<span class="badge badge-late">{esc(row["kickoff"])}</span>'
                )
            where = "casa" if row["at_home"] else "fora"
            lines.append(
                f"""          <li class="player{' is-late' if late else ''}">
            <span class="player-name">{esc(row['name'])} {''.join(marks)}</span>
            <span class="player-fixture">{esc(row['club'])} · {where} v {esc(row['opponent'])}</span>
            <span class="player-proj">{row['projected']:.1f}</span>
          </li>"""
            )
        blocks.append(
            f"""        <div class="line">
          <h3 class="line-label">{POS_PT[pos]}</h3>
          <ul class="line-list">
{chr(10).join(lines)}
          </ul>
        </div>"""
        )

    bench = []
    for n, pid in enumerate(BENCH, 1):
        row = rows[pid]
        bench.append(
            f"""          <li class="sub">
            <span class="sub-order">{n}</span>
            <span class="sub-name">{esc(row['name'])}</span>
            <span class="sub-pos">{POS_PT[row['position']]}</span>
            <span class="sub-proj">{row['projected']:.1f}</span>
          </li>"""
        )

    coach = data["stored"].get("coach")
    total = sum(rows[p]["projected"] for p in XI) + rows[CAPTAIN]["projected"]
    if coach:
        total += coach["projected_rate"]
    coach_line = (
        f'{esc(coach["name"])} <span class="muted">{esc(coach["club"])}</span>'
        f'<span class="coach-proj">{coach["projected_rate"]:.1f}</span>'
        if coach
        else f'{esc(COACH[0])} <span class="muted">{esc(COACH[1])}</span>'
    )
    return f"""      <div class="sheet">
        <div class="sheet-main">
{chr(10).join(blocks)}
        </div>
        <aside class="sheet-side">
          <h3 class="line-label">Suplentes <span class="hint">ordem de entrada</span></h3>
          <ul class="sub-list">
{chr(10).join(bench)}
          </ul>
          <dl class="sheet-meta">
            <dt>Treinador</dt><dd>{coach_line}</dd>
            <dt>Formação</dt><dd>3-4-3</dd>
            <dt>Projetado</dt><dd class="num">{total:.1f} <span class="muted">onze, capitão e treinador</span></dd>
          </dl>
        </aside>
      </div>"""


def ledger_section(data: dict) -> str:
    rows = data["stored"]["players"]
    starters, subs = set(XI), set(BENCH)
    ordered = sorted(
        rows.items(),
        key=lambda kv: (ORDER[kv[1]["position"]], -kv[1]["projected"]),
    )
    body = []
    for pid, row in ordered:
        if pid in starters:
            role, role_class = "Titular", "role-xi"
        elif pid in subs:
            role, role_class = "Suplente", "role-sub"
        else:
            role, role_class = "Fora", "role-out"

        if row["actual"] is None:
            result = '<span class="pending"><span class="dot"></span>por jogar</span>'
            error = '<span class="muted">—</span>'
        else:
            diff = row.get("error", 0)
            sign = "over" if diff >= 0 else "under"
            result = f'<span class="num strong">{row["actual"]}</span>'
            error = f'<span class="num delta {sign}">{diff:+.1f}</span>'

        late = "SET" in (row["kickoff"] or "")
        fixture = f"{'casa' if row['at_home'] else 'fora'} v {esc(row['opponent'])}"
        if late:
            fixture += f' <span class="badge badge-late">{esc(row["kickoff"])}</span>'

        body.append(
            f"""            <tr>
              <td class="cell-name">{esc(row['name'])}<span class="cell-club">{esc(row['club'])}</span></td>
              <td><span class="pos-chip">{POS_PT[row['position']]}</span></td>
              <td><span class="role {role_class}">{role}</span></td>
              <td class="cell-fixture">{fixture}</td>
              <td class="num">{row['season_rate']:.1f}</td>
              <td class="num strong">{row['projected']:.1f}</td>
              <td>{result}</td>
              <td>{error}</td>
            </tr>"""
        )

    settled = sum(1 for r in rows.values() if r["actual"] is not None)
    note = (
        f"{settled} de {len(rows)} liquidados"
        if settled
        else "Nenhum jogo desta jornada foi disputado ainda"
    )
    return f"""      <p class="section-note">{note}. A coluna <em>real</em> só é preenchida
      depois de o clube de cada jogador ter jogado — um clube com jogo adiado fica
      pendente, nunca a zero.</p>
      <div class="table-wrap">
        <table class="ledger">
          <thead>
            <tr>
              <th>Jogador</th><th>Pos</th><th>Papel</th><th>Jogo</th>
              <th class="num">Época</th><th class="num">Projetado</th>
              <th>Real</th><th>Erro</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>"""


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

    marks = []
    for value in totals:
        left = value / span * 100
        is_me = value == mine
        marks.append(
            f'<span class="mark{" is-me" if is_me else ""}" '
            f'style="left:{left:.2f}%"'
            f'{" title=\"Melro\"" if is_me else ""}></span>'
        )
    return f"""      <p class="section-note">Trinta equipas, {len(live)} com pontos.
      Cada marca é uma equipa; a vermelha é a minha. Os nomes ficam de fora —
      são de outras pessoas.</p>
      <div class="dist">
        <div class="dist-track">
{chr(10).join('          ' + m for m in marks)}
        </div>
        <div class="dist-axis">
          <span>0</span>
          <span class="dist-me" style="left:{mine / span * 100:.2f}%">{mine} · eu</span>
          <span>{top}</span>
        </div>
      </div>"""


def league_section(data: dict, public: bool = False) -> str:
    league = data["league"]
    if public:
        return league_distribution(data)
    if not league:
        return """      <p class="section-note">A liga privada não foi lida — defina
      <code>LIGA_RECORD_LEAGUE</code> no ambiente com o guid da liga.</p>"""
    body = []
    for t in league:
        mine = t["team"] == "Melro"
        dead = t["points_total"] == 0
        classes = " ".join(c for c in ("is-me" if mine else "", "is-dead" if dead else "") if c)
        body.append(
            f"""            <tr class="{classes}">
              <td class="num rank">{t['position_league']}</td>
              <td class="cell-name">{esc(t['team'])}<span class="cell-club">{esc(t['user'])}</span></td>
              <td class="num strong">{t['points_total']}</td>
              <td class="num">{group(t['position'])}</td>
            </tr>"""
        )
    return f"""      <div class="table-wrap">
        <table class="ledger league">
          <thead>
            <tr><th class="num">#</th><th>Equipa</th><th class="num">Pontos</th><th class="num">No país</th></tr>
          </thead>
          <tbody>
{chr(10).join(body)}
          </tbody>
        </table>
      </div>"""


OPEN_QUESTIONS = [
    (
        "Um jogo adiado devolve os pontos?",
        "Por verificar",
        "Quando um jogo não se realiza, os 57 jogadores dos dois clubes ficam a "
        "0 — enquanto 48,5% dos jogadores dos clubes que jogaram estão a −1. O "
        "−1 é o código para «não jogou», portanto o 0 parece ser «ainda não "
        "atribuído». É leitura, não é prova.",
    ),
    (
        "Sp. Braga–Gil Vicente da jornada 2",
        "Sem data",
        "Adiado a 16 de agosto por um surto gastrointestinal no plantel do Braga. "
        "A Liga Portugal ainda não marcou nova data. Dez dos 23 jogadores do "
        "plantel dessa jornada eram destes dois clubes.",
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
    items = []
    for title, state, body in OPEN_QUESTIONS:
        items.append(
            f"""        <article class="question">
          <div class="question-head">
            <h3>{esc(title)}</h3>
            <span class="state"><span class="dot"></span>{esc(state)}</span>
          </div>
          <p>{body}</p>
        </article>"""
        )
    return "\n".join(items)


def render(data: dict, public: bool = False) -> str:
    stamp = (data["as_of"] or "")[:16].replace("T", " ")
    recorded = data["stored"]["recorded_at"][:16].replace("T", " ")
    return f"""<title>Melro · Liga Record</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root {{
  --paper:      #f2f5f4;
  --surface:    #ffffff;
  --surface-2:  #eef1f0;
  --ink:        #14181d;
  --ink-soft:   #59636b;
  --ink-faint:  #8d979d;
  --rule:       #dbe1e0;
  --rule-firm:  #c2cbc9;
  --accent:     #c8102e;
  --accent-ink: #ffffff;
  --pending:    #a8701a;
  --pending-bg: #f6ecdb;
  --settled:    #2f6b4f;
  --over:       #2f6b4f;
  --under:      #b03a2e;
  --shadow:     0 1px 2px rgba(20, 24, 29, .06), 0 8px 24px rgba(20, 24, 29, .05);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:      #0f1216;
    --surface:    #171c21;
    --surface-2:  #1e242a;
    --ink:        #e7ebea;
    --ink-soft:   #9aa4aa;
    --ink-faint:  #6d777d;
    --rule:       #2a3138;
    --rule-firm:  #3b444c;
    --accent:     #ef4a5f;
    --accent-ink: #14181d;
    --pending:    #d9a441;
    --pending-bg: #2c2418;
    --settled:    #5aa87f;
    --over:       #5aa87f;
    --under:      #e2705f;
    --shadow:     0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px rgba(0, 0, 0, .3);
  }}
}}
:root[data-theme="dark"] {{
  --paper:      #0f1216;
  --surface:    #171c21;
  --surface-2:  #1e242a;
  --ink:        #e7ebea;
  --ink-soft:   #9aa4aa;
  --ink-faint:  #6d777d;
  --rule:       #2a3138;
  --rule-firm:  #3b444c;
  --accent:     #ef4a5f;
  --accent-ink: #14181d;
  --pending:    #d9a441;
  --pending-bg: #2c2418;
  --settled:    #5aa87f;
  --over:       #5aa87f;
  --under:      #e2705f;
  --shadow:     0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px rgba(0, 0, 0, .3);
}}

* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 1080px; margin: 0 auto; padding: 40px 24px 72px; }}

.masthead {{
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 16px;
  padding-bottom: 18px; border-bottom: 2px solid var(--ink);
}}
.masthead h1 {{
  font-family: Archivo, "IBM Plex Sans", sans-serif;
  font-weight: 700; font-size: clamp(30px, 5vw, 44px);
  letter-spacing: -.025em; margin: 0; text-wrap: balance;
}}
.masthead .rule-tag {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 12px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--accent-ink); background: var(--accent);
  padding: 3px 9px; border-radius: 2px;
}}
.masthead .stamp {{
  margin-left: auto; font-family: "IBM Plex Mono", monospace;
  font-size: 12px; color: var(--ink-faint);
}}

section {{ margin-top: 44px; }}
h2 {{
  font-family: Archivo, sans-serif; font-weight: 600;
  font-size: 13px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--ink-soft); margin: 0 0 16px;
  padding-bottom: 8px; border-bottom: 1px solid var(--rule);
}}
.section-note {{
  margin: 0 0 18px; max-width: 65ch;
  color: var(--ink-soft); font-size: 14.5px;
}}
.section-note em {{ color: var(--ink); font-style: normal; font-weight: 600; }}

.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-top: 24px; }}
.card {{
  background: var(--surface); border: 1px solid var(--rule);
  border-radius: 3px; padding: 16px 18px 14px; box-shadow: var(--shadow);
}}
.card-label {{
  margin: 0; font-family: "IBM Plex Mono", monospace; font-size: 11px;
  letter-spacing: .09em; text-transform: uppercase; color: var(--ink-faint);
}}
.card-value {{
  margin: 6px 0 2px; font-family: Archivo, sans-serif; font-weight: 700;
  font-size: 38px; line-height: 1; letter-spacing: -.03em;
  font-variant-numeric: tabular-nums;
}}
.card-value .of {{ font-size: 15px; font-weight: 500; color: var(--ink-soft); letter-spacing: 0; }}
.card-note {{ margin: 0; font-size: 13px; color: var(--ink-soft); }}

.sheet {{ display: grid; grid-template-columns: minmax(0, 1.9fr) minmax(0, 1fr); gap: 18px; align-items: start; }}
@media (max-width: 760px) {{ .sheet {{ grid-template-columns: 1fr; }} }}
.sheet-main, .sheet-side {{
  background: var(--surface); border: 1px solid var(--rule);
  border-radius: 3px; padding: 6px 18px 18px; box-shadow: var(--shadow);
}}
.line + .line {{ margin-top: 4px; }}
.line-label {{
  font-family: "IBM Plex Mono", monospace; font-size: 11px; font-weight: 600;
  letter-spacing: .1em; text-transform: uppercase; color: var(--ink-faint);
  margin: 16px 0 6px;
}}
.line-label .hint {{ text-transform: none; letter-spacing: 0; color: var(--ink-faint); font-weight: 400; }}
.line-list, .sub-list {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }}
.player {{
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  gap: 0 12px; padding: 7px 0; border-top: 1px solid var(--rule);
}}
.player-name {{ grid-column: 1; grid-row: 1; font-weight: 600; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }}
.player-fixture {{ grid-column: 1; grid-row: 2; font-size: 13px; color: var(--ink-soft); }}
.player-proj {{
  grid-column: 2; grid-row: 1 / span 2; align-self: center; justify-self: end;
  font-family: "IBM Plex Mono", monospace; font-weight: 600; font-size: 17px;
  font-variant-numeric: tabular-nums;
}}
.player.is-late .player-proj {{ color: var(--pending); }}

.badge {{
  font-family: "IBM Plex Mono", monospace; font-size: 10.5px; font-weight: 600;
  letter-spacing: .04em; padding: 2px 6px; border-radius: 2px; white-space: nowrap;
}}
.badge-cap {{ background: var(--accent); color: var(--accent-ink); }}
.badge-late {{ background: var(--pending-bg); color: var(--pending); border: 1px solid currentColor; }}

.sub {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: 10px; align-items: center; padding: 7px 0; border-top: 1px solid var(--rule); }}
.sub-order {{
  font-family: "IBM Plex Mono", monospace; font-size: 11px; font-weight: 600;
  width: 20px; height: 20px; display: grid; place-items: center;
  border: 1px solid var(--rule-firm); border-radius: 50%; color: var(--ink-soft);
}}
.sub-name {{ font-weight: 500; font-size: 14.5px; }}
.sub-pos {{ font-family: "IBM Plex Mono", monospace; font-size: 11px; color: var(--ink-faint); }}
.sub-proj {{ font-family: "IBM Plex Mono", monospace; font-size: 14px; font-variant-numeric: tabular-nums; color: var(--ink-soft); }}

.sheet-meta {{ margin: 20px 0 0; padding-top: 14px; border-top: 2px solid var(--ink); display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; font-size: 14px; }}
.sheet-meta dt {{ font-family: "IBM Plex Mono", monospace; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-faint); align-self: center; }}
.sheet-meta dd {{ margin: 0; font-weight: 600; }}
.muted {{ color: var(--ink-soft); font-weight: 400; }}
.coach-proj {{ float: right; font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; color: var(--ink-soft); }}

.table-wrap {{ overflow-x: auto; background: var(--surface); border: 1px solid var(--rule); border-radius: 3px; box-shadow: var(--shadow); }}
table.ledger {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
.ledger th {{
  text-align: left; font-family: "IBM Plex Mono", monospace; font-weight: 500;
  font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); padding: 11px 12px; border-bottom: 1px solid var(--rule-firm);
  white-space: nowrap; background: var(--surface-2);
}}
.ledger td {{ padding: 9px 12px; border-bottom: 1px solid var(--rule); vertical-align: middle; }}
.ledger tr:last-child td {{ border-bottom: none; }}
.num {{ text-align: right; font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; white-space: nowrap; }}
th.num {{ text-align: right; }}
.strong {{ font-weight: 600; }}
.cell-name {{ font-weight: 600; min-width: 150px; }}
.cell-club {{ display: block; font-weight: 400; font-size: 12.5px; color: var(--ink-faint); }}
.cell-fixture {{ font-size: 13px; color: var(--ink-soft); white-space: nowrap; }}

.pos-chip {{ font-family: "IBM Plex Mono", monospace; font-size: 11px; color: var(--ink-soft); border: 1px solid var(--rule-firm); border-radius: 2px; padding: 1px 5px; }}
.role {{ font-size: 12.5px; white-space: nowrap; }}
.role-xi {{ font-weight: 600; }}
.role-xi::before {{ content: ""; display: inline-block; width: 3px; height: 11px; background: var(--accent); margin-right: 6px; vertical-align: -1px; }}
.role-sub {{ color: var(--ink-soft); }}
.role-out {{ color: var(--ink-faint); }}

.pending {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--pending); background: var(--pending-bg); border-radius: 2px; padding: 2px 8px; white-space: nowrap; }}
.dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }}
.delta.over {{ color: var(--over); }}
.delta.under {{ color: var(--under); }}

.league .rank {{ width: 44px; color: var(--ink-soft); }}
.league tr.is-me td {{ background: var(--surface-2); }}
.league tr.is-me td:first-child {{ box-shadow: inset 3px 0 0 var(--accent); font-weight: 700; color: var(--ink); }}
.league tr.is-me .cell-name {{ font-weight: 700; }}
.league tr.is-dead td {{ color: var(--ink-faint); }}
.league tr.is-dead .cell-name {{ font-weight: 400; }}

.dist {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 3px; padding: 26px 22px 14px; box-shadow: var(--shadow); }}
.dist-track {{ position: relative; height: 34px; border-bottom: 1px solid var(--rule-firm); }}
.mark {{ position: absolute; bottom: 0; width: 2px; height: 20px; background: var(--rule-firm); transform: translateX(-1px); border-radius: 1px; }}
.mark.is-me {{ background: var(--accent); height: 34px; width: 3px; }}
.dist-axis {{ position: relative; height: 22px; margin-top: 7px; font-family: "IBM Plex Mono", monospace; font-size: 11px; color: var(--ink-faint); }}
.dist-axis > span:first-child {{ position: absolute; left: 0; }}
.dist-axis > span:last-child {{ position: absolute; right: 0; }}
.dist-me {{ position: absolute; transform: translateX(-50%); color: var(--accent); font-weight: 600; white-space: nowrap; }}
.chart {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 3px; padding: 22px 22px 16px; box-shadow: var(--shadow); }}
.bars {{ display: grid; grid-auto-flow: column; grid-auto-columns: minmax(64px, 1fr); grid-template-rows: auto 130px auto auto auto; gap: 0 10px; overflow-x: auto; }}
.bar-slot {{ display: grid; grid-row: 1 / -1; grid-template-rows: subgrid; justify-items: center; align-items: end; }}
.bar-value {{ font-family: "IBM Plex Mono", monospace; font-weight: 600; font-size: 15px; font-variant-numeric: tabular-nums; margin-bottom: 5px; }}
.bar {{ width: 100%; max-width: 62px; background: var(--accent); border-radius: 2px 2px 0 0; min-height: 4px; align-self: end; }}
.bar-round {{ font-family: "IBM Plex Mono", monospace; font-size: 11px; letter-spacing: .06em; color: var(--ink-soft); margin-top: 7px; padding-top: 7px; border-top: 1px solid var(--rule-firm); width: 100%; text-align: center; }}
.bar-pos {{ font-family: "IBM Plex Mono", monospace; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--ink-faint); margin-top: 3px; }}
.move {{ display: block; font-family: "IBM Plex Mono", monospace; font-size: 10.5px; line-height: 1.4; margin-top: 2px; white-space: nowrap; min-height: 1.4em; }}
.move.up {{ color: var(--over); }}
.move.down {{ color: var(--under); }}
.questions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 14px; }}
.question {{ background: var(--surface); border: 1px solid var(--rule); border-left: 3px solid var(--pending); border-radius: 3px; padding: 15px 17px; box-shadow: var(--shadow); }}
.question-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 7px; }}
.question h3 {{ font-family: Archivo, sans-serif; font-size: 15.5px; font-weight: 600; margin: 0; text-wrap: balance; }}
.question .state {{ display: inline-flex; align-items: center; gap: 5px; font-family: "IBM Plex Mono", monospace; font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase; color: var(--pending); white-space: nowrap; }}
.question p {{ margin: 0; font-size: 13.5px; color: var(--ink-soft); }}

footer {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--rule); font-size: 13px; color: var(--ink-faint); display: flex; flex-wrap: wrap; gap: 6px 18px; }}
footer code {{ font-family: "IBM Plex Mono", monospace; font-size: 12px; color: var(--ink-soft); }}
a {{ color: var(--accent); }}
:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
@media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
</style>

<div class="wrap">
  <header class="masthead">
    <h1>Melro</h1>
    <span class="rule-tag">Jornada {data['round']}</span>
    <span class="stamp">lido {esc(stamp)} UTC</span>
  </header>

  <section>
    <h2>Onde estamos</h2>
    <div class="cards">
{summary_cards(data, public)}
    </div>
  </section>

  <section>
    <h2>A época até agora</h2>
{history_section(data)}
  </section>

  <section>
    <h2>A folha entregue</h2>
    <p class="section-note">Escalada antes do fecho. Os dois jogadores marcados
    a âmbar têm o jogo da jornada {data['round']} em setembro — o Benfica a 9, o
    Sp. Braga a 10 — portanto os pontos deles chegam semanas depois dos outros.</p>
{sheet_section(data)}
  </section>

  <section>
    <h2>Projetado contra real</h2>
{ledger_section(data)}
  </section>

  <section>
    <h2>Quem rende mais do que a posse dele</h2>
{differentials_section(data)}
  </section>

  <section>
    <h2>A liga privada</h2>
{league_section(data, public)}
  </section>

  <section>
    <h2>Por resolver</h2>
    <div class="questions">
{questions_section()}
    </div>
  </section>

  <footer>
    <span>Projeções registadas a {esc(recorded)} UTC, antes do primeiro pontapé.</span>
    <span><code>scripts/build_dashboard.py</code></span>
  </footer>
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
    print(f"wrote {out} — round {args.round}, {settled}/23 settled, {len(data['league'])} in the league")


if __name__ == "__main__":
    main()
