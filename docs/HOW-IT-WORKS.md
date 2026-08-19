# How it works

The explainer. [README.md](../README.md) covers setup and usage;
[PLANNING.md](PLANNING.md) has the original plan and the regulation transcription.
This file is about *how the thing is built and why*.

## One split does all the work

Claude is good at judgement and bad at arithmetic. Football rules are the
opposite: they are arithmetic. So the whole project rests on one division of
labour.

> **Deterministic rules live in code. Judgement stays with Claude.**
> Formation legality, budget maths and automatic substitutions are computed by
> the program and are authoritative. Who to start and who to sell is a
> conversation.

Before this existed, asking Claude about the squad meant typing out 23 players
every time, and any claim about legality was a guess. Now the rules are
executable: when the server says a lineup is a legal 4-4-2, it counted.

## From a question to an answer

There is no website and no separate app. You type a question in the ordinary
Claude chat box. Behind it, a Python program is running on your machine.

```
You ask  ──▶  Claude picks a tool  ──▶  liga-record-mcp.exe
                                              │
                                              ▼
                                    data/squad.yaml
                                    liga.record.pt (market only)
                                              │
   answer  ◀──────────────────────────  rules applied
```

Nothing leaves the machine except a read of the public market. The squad file
never goes anywhere.

`.mcp.json` is what starts it: it tells Claude Code *when this project opens,
run this program and talk to it*. That is the whole connection — no login, no
service, no dashboard.

## Four files that matter

Everything else is tests and scaffolding.

| File | What it is |
| --- | --- |
| `data/squad.yaml` | **Your data.** Re-read on every question — edit it and the next answer is current, no restart. |
| `src/liga_record_mcp/rules.py` | **The rulebook as code.** Pure functions: no network, no files, no clock. |
| `src/liga_record_mcp/server.py` | **The questions Claude may ask.** Wiring only — fetch a squad, call a rule, shape the answer. |
| `src/liga_record_mcp/source/` | **Where data comes from.** YAML today, live site for the market. |

### Why the squad file is hand-written

This looks like a shortcut and isn't. Putting an interface (`SquadSource`)
between the rules and the data source meant the rules engine, the server and all
129 tests could be finished and trusted before the website was touched at all.
When the live client arrived it slotted in behind the same interface, and
nothing above it changed.

## What the code enforces

Section markers are the real ones — they appear in the code and in every error
message the server produces.

| Rule | Meaning |
| --- | --- |
| §6.4 | €40 000 000 budget, raised only by earned bonus |
| §6.5 | No limit on players per club — deliberately *not* coded |
| §6.6 | Exactly 23 players: 3 GK, 8 DEF, 8 MID, 4 FWD |
| §6.8 | One transfer per round, like for like by position |
| §6.13 | 11 starters, 4 substitutes in order, only 3 may come on |
| §6.17 | No coach selected means the round scores zero |
| §10.3(l) | The captain doubles points and must be a starter |
| §11 | Automatic substitutions: same position, bench order, cap of 3 |
| §12.1–12.4 | Quote movements, never below €500 000 |

**Only seven formations are legal.** The position ranges alone allow 8 to 14
outfielders, so the eleven-player limit is a separate constraint on top.
Intersecting them leaves 3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-3-2 and 5-4-1.
`legal_formations()` derives that from the ranges rather than hardcoding it, so
a misread range fails a test instead of passing silently.

## Three readings that might be wrong

129 passing tests prove the code agrees with *this reading* of the regulation.
They cannot prove the reading is right. Each of these is checkable by looking at
the site once a round is scored.

- **§11.3 covers the cheaper player.** When two same-position starters are
  missing and one substitute is available, it goes to *o jogador de menor valor*.
  Tested live: Zaidu (€1.5M) got the sub, Martim Fernandes (€2M) scored nothing.
  That is the literal text and the opposite of what you'd expect.
- **Scores of 1–3 move no price.** §12.3 starts at 4, §12.4 stops at 0. Nothing
  covers 1–3. Treated as no movement — the only sensible reading, still an
  inference.
- **What a sold player is worth.** §12.2 implies you keep the price paid when a
  quote rises. If selling returns the *current* price instead, holding risers
  funds upgrades — which changes the whole transfer strategy. Left as an explicit
  `Valuation` parameter rather than a baked-in guess.

## What the site taught us

Found by reading the site's own JavaScript rather than guessing.

- **Player codes are plain integers** (`42180`), not §4.5's "uma letra e cinco
  algarismos". Enforcing the documented format would have made every real player
  fail to load.
- **The market needs no login.** The session-token problem the plan treated as
  step 4's main risk does not exist for market data.
- **The two "Pts" columns** are season total then last round. The API names them;
  that had been an inference.
- **Every search parameter is required**, and position takes `GR/DF/MD/AV`, not a
  number. A malformed query returns an empty list under a healthy `200` — silent
  unless you read their code.

### A line that was not crossed

The same JavaScript revealed the buy, sell and renegotiate endpoints. Their
contracts are known and they are **deliberately not implemented** — a test
asserts they stay absent from the client.

A tool that reads is a different risk class from a tool that can spend a €40M
budget. Confirming a transfer stays a human's click on Record's own site.

## Status

| Step | State |
| --- | --- |
| 1 · Rules engine | done |
| 2 · Data-source seam + YAML squad | done |
| 3 · MCP server | done |
| 4 · Live site — market and calendar read, squad does not | mostly |

**Honestly missing:** league standings, the live squad read, and per-player
history (minutes, injuries) — which would need a keyed API.

### The projection, and why it is not a prediction

`project_points` blends observed form with a prior from completed seasons
(openfootball, open data, no key) and Record's own pricing. Three measurements
shaped it:

- **62% of a Liga Record score is the editorial rating** — a journalist's
  opinion, published nowhere. It cannot be reconstructed from any stats feed,
  which is why "fetch the data from elsewhere and rebuild past points" does not
  work.
- **42% of the market never plays**, scoring −1 a round. The gap between not
  playing and playing averagely is 3–4 points a round, larger than any other
  effect in the game.
- **Price and club strength correlate at r = 0.62.** Multiplying a raw price
  factor by a club factor counted the same fact twice, so price is now measured
  net of the club's own price level.

The model is unvalidated by construction: past Liga Record scores do not exist
to test against. The only honest check is forward — record projections now,
compare in a few rounds.

### Why the coach list is a file

`data/coaches.yaml` is hand-maintained for two reasons, not one. The list only
renders for a signed-in user — fetched anonymously the page returns the popover
shell with zero coaches in it. And the endpoint that touches coaches,
`team_manager.ashx`, is a **POST that sets your coach**; it does not list them.
The same is true of `team.ashx` (sets the lineup) and `team_captain.ashx` (sets
the captain). All three are writes, so none are implemented — the same line
drawn around buy and sell.

That makes §6.15 checkable: a chosen coach is verified against the real 18
rather than accepted as any text. When the list can't be read, the result says
`coach_unverified` instead of quietly skipping the check — a skipped check that
looks like a passed one is the worst of the three outcomes.

### The calendar is the one scraped surface

The market has a JSON endpoint; the calendar does not, so all 306 fixtures are
parsed out of one HTML page. That is brittle by nature, so it is contained:
every selector lives in `parse_fixtures`, and a recorded copy of the page is
checked into `tests/fixtures/calendario.html`. A redesign fails one test rather
than quietly producing wrong opponents.

Two things reality taught the parser. The site separates day from month with a
**non-breaking space** — the same U+00A0 that bit the euro formatter back in
step 1 — and **far-future rounds have a date but no kickoff time**, which is a
real state rather than a parse failure.

### "Next round" is not what the calendar says

A single postponed match keeps a finished round open. On the day this was built,
round 2 still had Sp. Braga vs Gil Vicente outstanding, so "first round with an
unplayed match" answered 2 while the actual fantasy ronda was 3. `squad_fixtures`
therefore trusts the squad file's own `round:` and falls back to the calendar
only when it has nothing better. `get_fixtures` stays literal, because it is a
question about the league rather than about your team.

## Next, in order

1. **Restructure the squad.** The only item with a deadline — points start at
   Ronda 5 and the site still reports `team_has_played: false`. Not code.
2. **Fill in the `selection:` block** in `data/squad.yaml`.
3. **Finish the live squad read.** Needs a login; real work for convenience.
4. **Verify the three uncertain rules.** Costs nothing once a round is scored.
