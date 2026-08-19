# Liga Record MCP Server

## Goal
MCP server that exposes my fantasy football squad data (Liga Record) as tools Claude can query directly — players, fixtures/gameweeks, stats.

## Why
Learning project to understand MCP hands-on, starting point for other agent projects.

## Decisions
| Decision | Choice |
| --- | --- |
| Write actions | **Read-only.** Claude advises; I click the buttons. |
| Rules engine | **Full.** Validation, price projection, auto-sub simulation. |
| Language | **Python** + official MCP SDK (FastMCP). |

---

## 1. Data source

Site: `https://liga.record.pt` · Team **"Melro"**, `id_team=156412`.

### Verified
- **ASP.NET Web Forms** (`.aspx`), jQuery-era stack. Pages are server-rendered HTML.
- **Zero first-party XHR** on page load — all network traffic is Google ads/analytics.
- `__VIEWSTATE` is only ~68 chars and there is **no `__EVENTVALIDATION`**. This app barely uses postback state, which keeps scraping (and any future writes) unusually cheap for Web Forms.
- A service layer exists under `/common/services/`:

| Endpoint | Params | Returns |
| --- | --- | --- |
| `playersearch.ashx` | `playerposition`, `name`, `club`, `minval`, `maxval` | **JSON** (probed: `200`, body `[]`) |
| `players_ranking_search.ashx` | `topage` | HTML fragment (`.html(data)` in `gestao-plantel-modified.js`) |
| `team_renegociateplayer.ashx` | `id_team` | write — not used, read-only project |
| `team_import.ashx` | `team_source` | write — not used |

### Page map
| Path | Content |
| --- | --- |
| `/gerir-equipas/default.aspx` | Team list |
| `/gerir-equipas/plantel.aspx?id_team=` | Squad (23 players, budget, values) |
| `/gerir-equipas/jogar.aspx?id_team=` | Round selection — XI / bench / coach / captain |
| `/info/calendario.aspx` | Fixtures |
| `/info/rankings.aspx` | Global standings |
| `/ligas-privadas/membro.aspx` | Private leagues |
| `/info/ajuda.aspx` | Full regulation (source for §2 below) |

### Open
- Correct `playersearch.ashx` parameter values — my guess returned `[]`. Fix by using the site's own search form with the Network tab open and copying the real request.
- `jogar.aspx` internals — **deliberately not inspected** (state-changing page). Only matters if read-only is ever revisited.
- Whether an editorial-rating → points table exists. A `0→0, 1→1, 2→2, 3→3, 4→4, 5→7` mapping appears next to §10 but its meaning is unconfirmed.

### Politeness
Scraping my own account for personal use. Cache aggressively, never fetch on tool call, rate-limit to roughly one sync per round.

---

## 2. Ruleset (from `/info/ajuda.aspx`, season 2026/27)

### Squad
- **23 players: 3 GK, 8 DEF, 8 MID, 4 FWD.**
- Budget **€40,000,000**, never exceeded except via earned bonus.
- **No limit on players per club**, no foreigner limit (§6.5).
- Player price floor **€500,000**.

### Each round
- Pick **11 starters + 4 subs + 1 coach**; captain optional.
- Formation: exactly **1 GK, 3–5 DEF, 3–5 MID, 1–3 FWD**.
- Only **3 of the 4 subs** can come on.
- Captain **doubles points, including negative ones**.
- Unchanged selections **repeat** from the previous round (§6.14, §6.16).
- **Zero points for the round** if XI + 4 subs + coach are not all set (§6.17).
- Deadline: **15 minutes before the jornada's first match**.
- **"Férias" chip:** 3 rounds per season (never the last three) — banks half the round winner's points, rounded up.

### Transfers
- **One transfer per round.** No hits, no rollover.
- Must preserve the positional contingent and stay within budget.
- **February:** transfers disabled; market reopens 2 Feb 2027 → jornada 24 (28 Feb 2027) with **max 6 swaps**.
- Player changes position → **+2** extra transfers that round (§6.10).
- Player changes competition → **+1** extra transfer, from round 2 (§6.11).
- The February window is an exception to both compensations.

### Pricing (§12.3–12.4) — fully deterministic
| Round points | Price change |
| --- | --- |
| 10 or more | +€150,000 |
| 6 to 9 | +€100,000 |
| 4 or 5 | +€50,000 |
| 0 | −€50,000 |
| negative | −€100,000 |

Floor €500k. If a player's price rises you keep your purchase price; if it falls you benefit at the February renewal (§12.2). This is why the squad page shows both **V.A.** (valor atual) and **V.I.** (valor inicial).

### Player scoring (§10.3), stacked on the editorial rating
| Event | Points |
| --- | --- |
| Goal | FWD +2 · MID +3 · DEF +4 · **GK +20** |
| Goal conceded | GK −2 · DEF −1 |
| Clean sheet | GK +2 · DEF +1 |
| Penalty | saved by GK +2 · converted +2 · missed −2 |
| Team wins | +1 to each player used (subs only if they came on) |
| Hat-trick (3+) | +5 |
| Red card | direct −3 · two yellows −1 (applies even on the bench) |
| FWD 75+ min, no goal | −1 |
| Unused player | −1 |
| Own goal | −2 |
| Player of the Week | +5 |
| Captain | doubles the round total, positive or negative |

Season side-bet on top scorer: golden boot +20, silver +10, bronze +5. Set once at team creation; changeable in the winter window for a €100,000 budget penalty.

### Coach scoring (§14.3)
| Event | Points |
| --- | --- |
| Win / draw / loss | +1 / 0 / −1 |
| Draw after trailing by N | +N (win instead of draw → doubled) |
| Team fails to score | −1 |
| Team keeps clean sheet | +1 |
| Win by 3+ goal margin | +1 |

### Auto-subs (§11) — the algorithm
A sub replaces a starter who does not play, or whose match is abandoned/postponed after the round closes.
1. **Same position only.**
2. Bench **order** decides (left to right as placed).
3. Two starters needing replacement but one eligible sub → tie-break on **lowest value**, then **lowest points**, both as of the end of the previous round.
4. Processed after the jornada's last scoring match.
5. If the captain is auto-subbed, **the replacement inherits the captaincy**.

### Open rule questions (found while implementing)
- **Scores of 1, 2 or 3 points move no price.** §12.3 tabulates 4–5, 6–9 and 10+ upward; §12.4 covers 0 and negative. Nothing covers 1–3. Implemented as no movement — confirm against a real round.
- **§11.3 awards the substitute to the *lower-valued* failing starter.** That is the literal text ("o jogador de menor valor"), but it is counterintuitive — you would expect your better player to be covered first. Implemented literally; worth verifying against a round where it actually bites.
- **Squad valuation basis is unsettled.** §12.2 says you keep the price paid when a quote rises and only realise decreases in February, so in-season squad value may not be the plain sum of current quotes. `Valuation.CURRENT` vs `Valuation.PAID` is an explicit parameter rather than a guess.

---

## 3. What this server can and cannot do

Base player points are assigned by Record's editorial staff, and §10.2 states they will not discuss individual scores. Everything in the tables above is stacked *on top of* that subjective rating.

**So this is not a points predictor** — that framing is a dead end. It is a **rules engine plus memory**: what are my legal options, what does this transfer cost, which sub comes on if a starter is out, what did I score and why. Deterministic tools feeding Claude's judgment, which is the right shape for MCP anyway.

---

## 4. MCP surface

Deliberately touches all three primitives, not just tools.

### Tools — reads (served from local cache)
| Tool | Input | Output |
| --- | --- | --- |
| `get_squad` | `team_id?` | 23 players, budget, squad value, balance, penalties, bonus |
| `get_round_selection` | `round?` | XI, ordered bench, captain, coach, locked flag |
| `get_player` | `player_id` | name, position, club, V.A./V.I., total + round points |
| `search_players` | `position?`, `name?`, `club?`, `min_val?`, `max_val?` | matching players |
| `get_round_status` | — | current ronda, deadline, is_locked |
| `get_fixtures` | `round?`, `club?` | fixtures |
| `get_coaches` | — | the 18 coaches + points |
| `get_standings` | `scope` | global or private league |
| `get_my_history` | `from?`, `to?` | points and rank per round |

### Tools — deterministic compute (pure, no I/O)
| Tool | Checks |
| --- | --- |
| `validate_selection` | 11 starters, 1 GK / 3–5 DEF / 3–5 MID / 1–3 FWD, 4 bench from the remaining 12, coach set |
| `validate_transfer` | positional contingent preserved, budget, transfers available, February window state |
| `project_price_change` | applies the §12.3–12.4 table |
| `simulate_autosubs` | full §11 algorithm, including captaincy inheritance |

### Tools — sync
`refresh(scope)` → explicit sync. **Nothing else ever hits the network.**

### Resources
- `ligarecord://regulamento` — the full rules text
- `ligarecord://scoring` — scoring tables as structured data
- `ligarecord://squad/current` — squad as a readable document

### Prompts
- `escolher-onze` — pulls squad + fixtures + rules, asks Claude to reason about the XI
- `planear-transferencia` — same for the single weekly transfer

Every read returns **`fetched_at`**, so Claude can say "this is from Tuesday" instead of asserting stale data. Players are keyed by **stable ID**; names resolve through `search_players` (accents and nicknames make name-matching a swamp).

---

## 5. Structure

```
liga-record-mcp/
├── docs/PLANNING.md
├── pyproject.toml
├── src/liga_record_mcp/
│   ├── server.py          # MCP wiring ONLY — tools/resources/prompts
│   ├── models.py          # pydantic: Player, Squad, Selection, Coach, Fixture
│   ├── rules.py           # pure functions — validation, pricing, auto-subs
│   ├── store.py           # SQLite cache + fetched_at
│   └── source/
│       ├── base.py        # SquadSource protocol  <- the seam
│       ├── manual.py      # reads data/squad.yaml <- build FIRST
│       └── ligarecord/
│           ├── services.py  # .ashx endpoints (JSON)
│           └── pages.py     # HTML scraping, all selectors isolated here
├── data/{squad.yaml, cache.db}
└── tests/                 # rules.py is pure -> trivially testable
```

`server.py` holds no business logic and no HTTP. Every CSS selector lives in `pages.py` so a site redesign breaks exactly one file; snapshot-test it against saved HTML fixtures.

## 6. Build order
1. `models.py` + `rules.py` + tests — no network needed, and the auto-sub simulator is the best first thing to write.
2. `manual.py` reading a hand-written `data/squad.yaml`.
3. `server.py` against manual data; connect to Claude and actually use it.
4. `source/ligarecord/` — `services.py` first (JSON is easier), then `pages.py`.

Steps 1–3 are fully unblocked today. The data question only gates step 4.
