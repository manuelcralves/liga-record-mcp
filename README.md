# Liga Record MCP

An MCP server I built to learn how the Model Context Protocol works, on a problem I actually have: managing my team in [Liga Record](https://liga.record.pt), the fantasy football game of Record, a Portuguese sports newspaper. The design rests on one split: the rulebook is arithmetic, so it lives in Python as pure functions (legal formations, the €40M budget, automatic substitutions), while judgement, like who to start or who to sell, stays with Claude. Claude gets 27 tools, 3 prompts for the weekly routine and the regulation as a resource generated from the same constants the code enforces, and every read carries an `as_of` timestamp so Claude can tell stored data from live data. The server is read-only on purpose: the site's buy and sell endpoints are known but not implemented, and a test checks it stays that way, so a transfer is always a human click. It later grew a points model, and a scheduled GitHub Action records its predictions before every round and scores them afterwards, so the model is only judged on calls made in advance.

## Run it

```bash
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

The editable install creates the `liga-record-mcp` launcher that `.mcp.json` points at, so Claude Code starts the server when you open this folder. That path is absolute, so edit it after cloning. The squad lives in `data/squad.yaml` (start from `data/squad.example.yaml`), and the loader checks it against the regulation on every read. Then ask something like *"Is my current XI legal, and who comes on if Diogo Costa doesn't play?"*

More on the design in [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).
