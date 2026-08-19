# Liga Record MCP

An MCP server that exposes my [Liga Record](https://liga.record.pt) fantasy football
squad to Claude — the rules as tools, the regulation as a resource, and two
prompts for the decisions I actually make each round.

The split it's built on: **deterministic rules live in code, judgement stays with
Claude.** Formation legality, budget arithmetic and the §11 automatic
substitutions are computed here and are authoritative. Who to start and who to
sell is Claude's to reason about, given real data and the real rulebook.

See [docs/PLANNING.md](docs/PLANNING.md) for the design and the rule ambiguities
this reading of the regulation leaves open.

## Setup

```bash
python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -e .
```

Your squad lives in `data/squad.yaml`. Copy `data/squad.example.yaml` and fill in
your 23 players — the loader checks the file against the regulation on every read
and names whatever is missing, so you don't have to count by hand. Point
`LIGA_RECORD_SQUAD` at a different file to run a second team.

## Connecting it to Claude

Claude Code:

```bash
claude mcp add liga-record -- ./.venv/Scripts/python.exe -m liga_record_mcp.server
```

Claude Desktop — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "liga-record": {
      "command": "C:\\path\\to\\liga-record-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "liga_record_mcp.server"]
    }
  }
}
```

Then ask it something: *"Is my current XI legal, and who comes on if Diogo Costa
doesn't play?"*

## What it exposes

**Tools** — `get_squad`, `get_player`, `search_squad`, `validate_selection`,
`simulate_autosubs`, `check_transfer`, `project_price`. Every read carries an
`as_of` timestamp so Claude can say how fresh the data is instead of presenting a
stored squad as live.

**Resources** — `ligarecord://regulamento` (generated from the same constants the
rules enforce, so it can't drift from the code) and `ligarecord://squad`.

**Prompts** — `pick_starting_xi`, `plan_transfers`.

## Tests

```bash
./.venv/Scripts/python.exe -m pytest -q
```

`rules.py` is pure — no I/O, no network, no clock — so the whole rulebook is
tested without touching the site.

## Status

Steps 1–3 of the plan are done: the rules engine, the data-source seam with a
hand-maintained YAML squad, and the MCP server. Step 4 swaps in a live client
behind the same `SquadSource` protocol — the site turns out to have a JSON
backend at `common/services/playersearch.ashx`, so that's a client rather than a
scraper.
