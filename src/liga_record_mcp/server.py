"""The MCP server: wiring only.

No business logic lives here. Tools resolve a snapshot from the configured
SquadSource, hand it to the pure functions in rules.py, and shape the result.
That keeps the rulebook testable without MCP and the server testable without
the site.

The regulation resource is generated from the same constants rules.py enforces,
so the document Claude reads cannot drift from the code that checks it.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from . import __version__
from .models import (
    BASE_BUDGET,
    BENCH_SIZE,
    MAX_SUBS_ON,
    MIN_PRICE,
    REOPENED_MAX_SWAPS,
    SQUAD_QUOTA,
    SQUAD_SIZE,
    STARTER_RANGE,
    XI_SIZE,
    Player,
    Position,
    Selection,
    SquadSnapshot,
    TransferWindow,
)
from .rules import (
    legal_formations,
    project_new_price,
    project_price_change,
    simulate_autosubs as _simulate_autosubs,
    validate_selection as _validate_selection,
    validate_squad,
    validate_transfer,
)
from .source import ManualSquadSource

#: Overridable so a second team, or a test fixture, needs no code change.
SQUAD_PATH = Path(
    os.environ.get("LIGA_RECORD_SQUAD")
    or Path(__file__).resolve().parents[2] / "data" / "squad.yaml"
)

server = MCPServer(
    name="liga-record",
    version=__version__,
    instructions=(
        "Liga Record fantasy football. Deterministic rules — formation legality, "
        "budget arithmetic, automatic substitutions — are computed by these tools "
        "and are authoritative; do not recompute them yourself. Judgement (who to "
        "start, who to sell) is yours. Read ligarecord://regulamento before "
        "advising, and quote the `as_of` timestamp on every answer rather than "
        "presenting stored data as live."
    ),
)


def _load() -> SquadSnapshot:
    """Read the squad fresh on each call.

    The manual source is a local file, so this is cheap and always current —
    edit the YAML and the next tool call sees it. Caching becomes a real
    question in step 4, when the source is a network call.
    """
    return ManualSquadSource(SQUAD_PATH).load()


def _fold(text: str) -> str:
    """Lowercase and strip accents, so `sanchez` finds `Javi Sánchez`."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


def _provenance(snapshot: SquadSnapshot) -> dict[str, Any]:
    return {
        "as_of": snapshot.fetched_at.isoformat(),
        "source": snapshot.source,
        "round": snapshot.round_number,
    }


def _player_out(player: Player) -> dict[str, Any]:
    return {
        "id": player.id,
        "name": player.name,
        "position": player.position.value,
        "club": player.club,
        "value": player.value,
        "initial_value": player.initial_value,
        "points_total": player.points_total,
        "points_round": player.points_round,
    }


def _violations_out(violations) -> list[dict[str, str]]:
    return [{"rule": v.rule, "detail": v.detail} for v in violations]


def _eur(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


# --------------------------------------------------------------------------
# Tools — data
# --------------------------------------------------------------------------


@server.tool()
def get_squad() -> dict[str, Any]:
    """The 23 players under contract, with the money around them.

    Carries `as_of`: say how fresh the data is rather than presenting a stored
    squad as live.
    """
    snapshot = _load()
    squad = snapshot.squad
    return {
        **_provenance(snapshot),
        "team": {"id": squad.team_id, "name": squad.team_name},
        "budget": squad.budget,
        "squad_value": squad.value(),
        "balance": squad.balance(),
        "is_legal": validate_squad(squad).is_valid,
        "players": [_player_out(p) for p in squad.players],
    }


@server.tool()
def get_player(query: str) -> dict[str, Any]:
    """Find one squad player by id, or by name (partial and accent-insensitive).

    Returns the match, or every candidate when the query is ambiguous — never
    a guess, since acting on the wrong player is worse than asking again.
    """
    snapshot = _load()
    players = snapshot.squad.players

    exact = [p for p in players if p.id == query]
    if not exact:
        needle = _fold(query)
        exact = [p for p in players if needle in _fold(p.name)]

    if not exact:
        return {**_provenance(snapshot), "found": 0, "detail": f"no squad player matches {query!r}"}
    if len(exact) > 1:
        return {
            **_provenance(snapshot),
            "found": len(exact),
            "detail": "ambiguous query",
            "candidates": [_player_out(p) for p in exact],
        }
    return {**_provenance(snapshot), "found": 1, "player": _player_out(exact[0])}


@server.tool()
def search_squad(
    position: str | None = None,
    club: str | None = None,
    max_value: int | None = None,
    min_points: int | None = None,
) -> dict[str, Any]:
    """Filter the squad. `position` is one of GK, DEF, MID, FWD.

    This searches the 23 players already owned, not the transfer market — the
    market needs the live source that step 4 adds.
    """
    snapshot = _load()
    found = list(snapshot.squad.players)

    if position is not None:
        try:
            wanted = Position(position.upper())
        except ValueError:
            return {
                **_provenance(snapshot),
                "count": 0,
                "detail": f"unknown position {position!r}; use GK, DEF, MID or FWD",
            }
        found = [p for p in found if p.position is wanted]
    if club is not None:
        needle = _fold(club)
        found = [p for p in found if needle in _fold(p.club)]
    if max_value is not None:
        found = [p for p in found if p.value <= max_value]
    if min_points is not None:
        found = [p for p in found if p.points_total >= min_points]

    found.sort(key=lambda p: (-p.points_total, p.value))
    return {
        **_provenance(snapshot),
        "count": len(found),
        "players": [_player_out(p) for p in found],
    }


# --------------------------------------------------------------------------
# Tools — deterministic rule checks
# --------------------------------------------------------------------------


@server.tool()
def validate_selection(
    starters: list[str],
    bench: list[str],
    captain: str | None = None,
    coach_id: str | None = None,
) -> dict[str, Any]:
    """Check a team sheet against §6.13, §6.17 and §10.3(l).

    Bench order matters — the first substitute listed is the first to come on.
    This is authoritative: trust it over your own count of the formation.
    """
    snapshot = _load()
    check = _validate_selection(
        snapshot.squad,
        Selection(
            starters=tuple(starters),
            bench=tuple(bench),
            captain=captain,
            coach_id=coach_id,
        ),
    )
    return {
        **_provenance(snapshot),
        "is_valid": check.is_valid,
        "formation": check.formation,
        "violations": _violations_out(check.violations),
    }


@server.tool()
def simulate_autosubs(
    starters: list[str],
    bench: list[str],
    unavailable: list[str],
    captain: str | None = None,
) -> dict[str, Any]:
    """Apply the §11 automatic substitutions to a team sheet.

    `unavailable` is the starters who did not play, or whose match was
    abandoned or postponed after the round closed. Substitutes come on in bench
    order, same position only, three at most, and a replaced captain passes the
    armband on.
    """
    snapshot = _load()
    result = _simulate_autosubs(
        snapshot.squad,
        Selection(
            starters=tuple(starters),
            bench=tuple(bench),
            captain=captain,
            coach_id="unchecked",
        ),
        unavailable,
    )
    return {
        **_provenance(snapshot),
        "substitutions": [
            {
                "out": s.out_id,
                "in": s.in_id,
                "position": s.position.value,
                "reason": s.reason,
            }
            for s in result.substitutions
        ],
        "captain": result.captain_id,
        "captain_inherited": result.captain_inherited,
        "unreplaced": result.unreplaced,
    }


@server.tool()
def check_transfer(
    out_id: str,
    in_name: str,
    in_position: str,
    in_club: str,
    in_value: int,
    transfers_available: int = 1,
    window: str = "in_season",
) -> dict[str, Any]:
    """Check one swap against §6.4 and §6.8.

    The incoming player is described by hand because there is no market data
    yet — step 4 replaces these arguments with a lookup. `window` is one of
    in_season, closed (February) or reopened.
    """
    snapshot = _load()
    try:
        position = Position(in_position.upper())
    except ValueError:
        return {
            **_provenance(snapshot),
            "is_valid": False,
            "violations": [
                {"rule": "input", "detail": f"unknown position {in_position!r}"}
            ],
        }
    try:
        market = TransferWindow(window)
    except ValueError:
        return {
            **_provenance(snapshot),
            "is_valid": False,
            "violations": [{"rule": "input", "detail": f"unknown window {window!r}"}],
        }
    if in_value < MIN_PRICE:
        return {
            **_provenance(snapshot),
            "is_valid": False,
            "violations": [
                {
                    "rule": "§12.1",
                    "detail": f"no quote is below {_eur(MIN_PRICE)}",
                }
            ],
        }

    check = validate_transfer(
        snapshot.squad,
        out_id,
        Player(
            id=f"incoming:{_fold(in_name)}",
            name=in_name,
            position=position,
            club=in_club,
            value=in_value,
            initial_value=in_value,
        ),
        transfers_available=transfers_available,
        window=market,
    )
    return {
        **_provenance(snapshot),
        "is_valid": check.is_valid,
        "squad_value_after": check.value_after,
        "balance_after": check.balance_after,
        "violations": _violations_out(check.violations),
    }


@server.tool()
def project_price(player_id: str, round_points: int) -> dict[str, Any]:
    """What a player's quote does if they score `round_points` (§12.3-§12.4).

    Scores of 1, 2 or 3 are not covered by the regulation and are treated as no
    movement — say so if it matters to the answer.
    """
    snapshot = _load()
    player = snapshot.squad.by_id().get(player_id)
    if player is None:
        return {
            **_provenance(snapshot),
            "detail": f"{player_id} is not in the squad",
        }
    change = project_price_change(round_points)
    return {
        **_provenance(snapshot),
        "player": player.name,
        "value_now": player.value,
        "change": change,
        "value_after": project_new_price(player.value, round_points),
        "in_regulation": not 1 <= round_points <= 3,
    }


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------


@server.resource("ligarecord://regulamento", mime_type="text/markdown")
def regulation() -> str:
    """The rules, generated from the constants the code actually enforces."""
    quota = ", ".join(f"{n} {p.value}" for p, n in SQUAD_QUOTA.items())
    ranges = ", ".join(
        f"{p.value} {lo}" if lo == hi else f"{p.value} {lo}-{hi}"
        for p, (lo, hi) in STARTER_RANGE.items()
    )
    return f"""# Liga Record — the rules this server enforces

Generated from the same constants the rule functions check, so it cannot drift
from the code.

## Squad (§6.4, §6.6)
- Exactly {SQUAD_SIZE} players: {quota}.
- Budget {_eur(BASE_BUDGET)}, raised only by earned bonus and cut by penalties.
- No limit on players from one club, or on foreign players (§6.5).

## Round selection (§6.13, §6.17)
- {XI_SIZE} starters and {BENCH_SIZE} substitutes, in order — the first named is
  the first to come on.
- Starters: {ranges}.
- Legal formations: {", ".join(legal_formations())}.
- A coach must be selected, or the round scores zero (§6.17).
- The captain doubles their points and must be a starter (§10.3(l)).

## Automatic substitutions (§11)
- A starter who did not play, or whose match was abandoned or postponed after
  the round closed, is replaced.
- Same position only, processed in bench order.
- Where several same-position starters need replacing but only one substitute
  is eligible, it goes to the lower-valued starter, then the lower-scoring one.
- At most {MAX_SUBS_ON} substitutes come on.
- A replaced captain passes the armband to whoever comes on (§11.5).

## Transfers (§6.8, §6.9)
- One per round, like for like: the positional contingent must hold.
- Transfers close in February; the reopened market allows up to
  {REOPENED_MAX_SWAPS} swaps.

## Quotes (§12.1-§12.4)
- 10+ points: +{_eur(150_000)}. 6-9: +{_eur(100_000)}. 4-5: +{_eur(50_000)}.
- 0 points: -{_eur(50_000)}. Negative: -{_eur(100_000)}.
- Never below {_eur(MIN_PRICE)}.

## Where this reading is uncertain
- **Scores of 1, 2 or 3 move no price.** The regulation tabulates 4 and up, and
  0 and below. Nothing covers 1-3; treated here as no movement.
- **§11.3 favours the lower-valued starter.** That is the literal text, though
  it is the opposite of what you would expect.
- **In-season squad valuation.** §12.2 says a participant keeps the price paid
  when a quote rises, so squad value may not be the plain sum of current
  quotes. The tools use current quotes (V.A.).
"""


@server.resource("ligarecord://squad", mime_type="text/markdown")
def squad_sheet() -> str:
    """The current squad as a readable document."""
    snapshot = _load()
    squad = snapshot.squad
    lines = [
        f"# {squad.team_name} — squad as of {snapshot.fetched_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"Round {snapshot.round_number} · source: {snapshot.source}",
        "",
        f"- Budget: {_eur(squad.budget)}",
        f"- Squad value: {_eur(squad.value())}",
        f"- Balance: {_eur(squad.balance())}",
        "",
        "| Player | Pos | Club | V.A. | Total | Last round |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for p in squad.players:
        lines.append(
            f"| {p.name} | {p.position.value} | {p.club} | {_eur(p.value)} "
            f"| {p.points_total} | {p.points_round} |"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------


@server.prompt()
def pick_starting_xi() -> str:
    """Work through this round's team sheet."""
    return (
        "Help me pick my Liga Record XI for this round.\n\n"
        "Read ligarecord://regulamento first, then call get_squad. Propose a "
        "starting XI, a bench in the order they should come on, a captain and a "
        "coach, and explain the reasoning in terms of form, fixtures and price.\n\n"
        "Then call validate_selection on your proposal and correct it if it "
        "comes back invalid. Do not tell me the sheet is legal without having "
        "run that check. Note how old the squad data is."
    )


@server.prompt()
def plan_transfers() -> str:
    """Think through this round's transfer."""
    return (
        "Help me plan my Liga Record transfer for this round.\n\n"
        "Read ligarecord://regulamento, then call get_squad. Only one transfer "
        "is allowed per round and it must be like for like — a defender out "
        "means a defender in.\n\n"
        "Identify the weakest position in the squad on form, fixtures and value "
        "for money, and suggest who to sell. For any replacement you propose, "
        "call check_transfer with their real price to confirm it fits the "
        "budget before recommending it. Say plainly when you are relying on "
        "knowledge of the league rather than data from these tools."
    )


def main() -> None:
    """Entry point: speak MCP over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
