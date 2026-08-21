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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from . import __version__
from .models import (
    BASE_BUDGET,
    BENCH_SIZE,
    MAX_SUBS_ON,
    MIN_PRICE,
    BRONZE_BOOT_BONUS,
    COMPETITION_CHANGE_TRANSFERS,
    FIRST_SCORING_MATCHDAY,
    GOLDEN_BOOT_BONUS,
    HOLIDAY_BLOCKED_LAST_ROUNDS,
    HOLIDAY_ROUNDS,
    LAST_MATCHDAY,
    NATIONAL_FIRST_MATCHDAY,
    NATIONAL_ROUNDS,
    POSITION_CHANGE_TRANSFERS,
    RECORD_ROUNDS,
    REOPENED_FIRST_ROUND,
    REOPENED_LAST_MATCHDAY,
    REOPENED_MAX_SWAPS,
    SILVER_BOOT_BONUS,
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
from .stats import (
    COACH_BIG_LOSS,
    COACH_BIG_MARGIN,
    COACH_BIG_WIN,
    COACH_CLEAN_SHEET,
    COACH_DRAW,
    COACH_FAILED_TO_SCORE,
    COACH_LOSS,
    COACH_SENT_OFF,
    COACH_SUBSTITUTE_GOAL,
    COACH_WIN,
    NO_MATCH,
    PLAYED,
    PRIOR_STRENGTH,
    UNUSED,
    appearance_rate,
    classify_appearance,
    club_concentration,
    club_price_index,
    availability,
    clubs_playing_in,
    decompose_round,
    differential_rows,
    fixture_exposure,
    form_curve,
    last_scored_round,
    league_table,
    matches_played,
    never_played,
    ownership_baseline,
    per_match,
    position_baselines,
    project,
    rate_rows,
    season_summary,
)
from .source import (
    MARKET_MAX_VALUE,
    LastSeasonError,
    LastSeasonSource,
    LigaRecordClient,
    ManualSquadSource,
    MarketError,
    OpenFootballClient,
    SiteError,
    SquadSourceError,
    history_for,
    holidays_used,
    load_appearances,
    load_decisions,
    load_coaches,
    record_decision,
    record_round,
    record_season_choice,
    recorded_rounds,
    save_appearances,
    save_decisions,
)
# Aliased because the tools below take the plain names: a tool called
# `track_record` and a function called `track_record` cannot both win.
from .source.decisions import recorded_rounds as recorded_decisions  # noqa: E402
from .source.decisions import track_record as track_record_of  # noqa: E402

#: Module level so the caches survive across tool calls.
_market = LigaRecordClient()
_history = OpenFootballClient()

#: Overridable so a second team, or a test fixture, needs no code change.
SQUAD_PATH = Path(
    os.environ.get("LIGA_RECORD_SQUAD")
    or Path(__file__).resolve().parents[2] / "data" / "squad.yaml"
)

COACHES_PATH = Path(
    os.environ.get("LIGA_RECORD_COACHES")
    or Path(__file__).resolve().parents[2] / "data" / "coaches.yaml"
)

#: The private league to read when `standings` is called without a guid.
#: Kept in the environment rather than in a file because the guid exposes the
#: team names and usernames of everyone else in the league, and .mcp.json is
#: committed. Set it in the shell, never in a tracked config.
DEFAULT_LEAGUE = os.environ.get("LIGA_RECORD_LEAGUE") or None

#: Last season, as this project reconstructed it. Absent until the sweep has
#: been run, and every tool that reads it must survive its absence.
LAST_SEASON_PATH = Path(
    os.environ.get("LIGA_RECORD_LAST_SEASON")
    or Path(__file__).resolve().parents[2] / "data" / "last-season.json"
)

#: What was decided each round, and what the team scored for it.
DECISIONS_PATH = Path(
    os.environ.get("LIGA_RECORD_DECISIONS")
    or Path(__file__).resolve().parents[2] / "data" / "decisions.json"
)

#: Files this server writes to. Both are local; nothing is ever sent to the site.
APPEARANCES_PATH = Path(
    os.environ.get("LIGA_RECORD_APPEARANCES")
    or Path(__file__).resolve().parents[2] / "data" / "appearances.json"
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


def _coaches():
    """The selectable coaches, or None if the list cannot be read.

    Returning None rather than raising keeps a missing coach file from breaking
    every team-sheet check — but callers must then say the coach went
    unverified, because silently skipping the check is worse than not having it.
    """
    try:
        return load_coaches(COACHES_PATH)
    except SquadSourceError:
        return None


def _fold(text: str) -> str:
    """Lowercase and strip accents, so `sanchez` finds `Javi Sánchez`."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(c) != "Mn"
    )


def _now_iso() -> str:
    """Timestamp for data read live rather than from a snapshot."""
    return datetime.now(timezone.utc).isoformat()


def _provenance(snapshot: SquadSnapshot) -> dict[str, Any]:
    return {
        "as_of": snapshot.fetched_at.isoformat(),
        "source": snapshot.source,
        "round": snapshot.round_number,
    }


def _matches_by_club() -> dict[str, int] | None:
    """Matches completed per club, or None when the calendar is unreachable.

    Cached for six hours by the client, so the cost is paid once. Needed
    because a total is meaningless without its denominator.
    """
    try:
        return matches_played(_market.fixtures())
    except SiteError:
        return None


def _player_out(player: Player, matches: dict[str, int] | None = None) -> dict[str, Any]:
    out = {
        "id": player.id,
        "name": player.name,
        "position": player.position.value,
        "club": player.club,
        "value": player.value,
        "initial_value": player.initial_value,
        "points_total": player.points_total,
        "points_round": player.points_round,
    }
    if matches is not None:
        count = matches.get(player.club, 0)
        rate = per_match(player.points_total, count)
        out["matches_played"] = count
        out["points_per_match"] = None if rate is None else round(rate, 2)
        out["never_played"] = never_played(player, count)
    return out


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

    Each player carries `matches_played` and `points_per_match`. Compare on the
    rate, not the total — clubs are on different match counts whenever a
    fixture is postponed, and a total alone will rank a good player at a club
    with a game in hand as though he were poor.
    """
    snapshot = _load()
    squad = snapshot.squad
    matches = _matches_by_club()
    result = {
        **_provenance(snapshot),
        "team": {"id": squad.team_id, "name": squad.team_name},
        "budget": squad.budget,
        "squad_value": squad.value(),
        "balance": squad.balance(),
        "is_legal": validate_squad(squad).is_valid,
        "players": [_player_out(p, matches) for p in squad.players],
    }
    if matches is None:
        result["rates_unavailable"] = (
            "the calendar could not be read, so points_per_match is missing — "
            "totals are not comparable across clubs with different match counts"
        )
    return result


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

    matches = _matches_by_club()
    found.sort(key=lambda p: (-p.points_total, p.value))
    return {
        **_provenance(snapshot),
        "count": len(found),
        "players": [_player_out(p, matches) for p in found],
    }


@server.tool()
def squad_value() -> dict[str, Any]:
    """Your squad ranked by scoring rate, with the cost of each player.

    Use this rather than reading totals off get_squad. `points_per_match`
    normalises for clubs on different match counts; `value_rate` is points per
    match per million, which is the figure that answers "is he worth his
    price".

    `never_played` marks players who have not taken the field once. §10.3 pays
    an unused player -1 a round, and across the market 42% of players are in
    that state — it is the largest single effect in the game, and it is
    invisible in a points total.
    """
    snapshot = _load()
    matches = _matches_by_club()
    if matches is None:
        return {
            **_provenance(snapshot),
            "detail": "the calendar could not be read, so rates cannot be computed",
        }

    rows = rate_rows(snapshot.squad.players, matches)
    counts = sorted({r["matches_played"] for r in rows})
    return {
        **_provenance(snapshot),
        "match_counts_in_squad": counts,
        "warning": (
            "clubs are on different match counts — compare points_per_match, "
            "never points_total"
        )
        if len(counts) > 1
        else None,
        "players": rows,
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
    `coach_id` is checked against the real list of 18 (§6.15), so a made-up
    coach is caught rather than quietly accepted.

    This is authoritative: trust it over your own count of the formation.
    """
    snapshot = _load()
    coaches = _coaches()
    check = _validate_selection(
        snapshot.squad,
        Selection(
            starters=tuple(starters),
            bench=tuple(bench),
            captain=captain,
            coach_id=coach_id,
        ),
        coaches,
    )
    result = {
        **_provenance(snapshot),
        "is_valid": check.is_valid,
        "formation": check.formation,
        "violations": _violations_out(check.violations),
    }
    if coaches is None:
        result["coach_unverified"] = (
            "the coach list could not be read, so the coach was not checked "
            "against the real 18"
        )
    return result


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


@server.tool()
def editorial_ratings(round_number: int | None = None) -> dict[str, Any]:
    """Recover each player's editorial rating for a round that has been played.

    Record's writers mark every player 0-5 and publish only the total, so this
    project treated the mark as unreachable for weeks. It is not: §10.1 and
    §10.3 are published, so the objective half — win bonus, clean sheet, goals
    conceded, Player of the Week — can be computed exactly and subtracted. What
    remains is the rating.

    Checked across all 441 players of round 2: none left a residual §10.3 could
    not account for. 143 resolved to a bare rating, distributed around 2 and 3
    exactly as a journalist's marks would be.

    A rating is only reported when nothing else explains the residual. A goal, a
    card or a late substitution can hide inside it, and forwards are never
    certain because §10.3(h) docks every one who plays 75 minutes without
    scoring. Ambiguous rows carry their candidate readings instead of a guess.
    """
    try:
        fixtures = _market.fixtures()
        market = [p for pos in Position for p in _market.search(pos)]
    except SiteError as exc:
        return {"detail": str(exc)}

    target = round_number or last_scored_round(fixtures)
    if target is None:
        return {"detail": "no round has been scored yet"}

    results = {}
    for fixture in fixtures:
        if fixture.round_number == target and fixture.played:
            results[fixture.home] = (fixture.home_goals, fixture.away_goals)
            results[fixture.away] = (fixture.away_goals, fixture.home_goals)
    if not results:
        return {"detail": f"no match of round {target} has been played"}

    playing = [p for p in market if p.club in results]
    # §10.3(k) names the round's highest scorer, so the bonus is derivable
    # rather than a matter of opinion.
    best = max(playing, key=lambda p: p.points_round, default=None)

    rows = []
    for player in playing:
        scored, conceded = results[player.club]
        detail = decompose_round(
            player.points_round,
            player.position,
            conceded=conceded,
            won=scored > conceded,
            player_of_the_week=best is not None and player.id == best.id,
        )
        rows.append(
            {
                "id": player.id,
                "name": player.name,
                "position": player.position.value,
                "club": player.club,
                "points_round": player.points_round,
                **detail,
            }
        )

    read = [r for r in rows if r["rating"] is not None]
    unexplained = [
        r["name"] for r in rows
        if r["candidates"] == ["nothing in §10.3 explains this"]
    ]
    return {
        "as_of": _now_iso(),
        "source": "ligarecord",
        "round": target,
        "player_of_the_week": best.name if best else None,
        "read": len(read),
        "ambiguous": sum(1 for r in rows if r["used"] and r["rating"] is None),
        "not_used": sum(1 for r in rows if not r["used"]),
        "mean_rating": round(sum(r["rating"] for r in read) / len(read), 2)
        if read
        else None,
        "unexplained": unexplained,
        "players": sorted(rows, key=lambda r: -r["points_round"]),
    }


@server.tool()
def primeira_liga() -> dict[str, Any]:
    """The real league table, folded out of the calendar.

    Nothing extra is fetched — the calendar already carries scores for played
    matches, so this is arithmetic over data the server holds anyway.

    `played` is reported per club and is not the same for everyone: one
    postponement leaves two clubs a match behind, and a table that hid that
    would repeat the mistake of a points total without its denominator. Read
    the column before reading the order.
    """
    try:
        fixtures = _market.fixtures()
    except SiteError as exc:
        return {"detail": str(exc)}

    rows = league_table(fixtures)
    if not rows:
        return {"detail": "no match has been played yet"}
    behind = sorted({r["played"] for r in rows})
    return {
        "as_of": _now_iso(),
        "source": "ligarecord",
        "matches_scored": sum(r["played"] for r in rows) // 2,
        "table": rows,
        "uneven_matches": len(behind) > 1,
        "note": (
            "clubs are on different match counts — compare with `played` in view"
            if len(behind) > 1
            else "every club has played the same number of matches"
        ),
    }


@server.tool()
def squad_exposure(round_number: int | None = None) -> dict[str, Any]:
    """How much of the squad rides on one match, and on one club.

    A squad is not a list of independent bets. In round two, ten of the
    twenty-three players held belonged to Sp. Braga and Gil Vicente — three of
    the four forwards among them — and one postponement blanked all ten at
    once. Nothing was watching that.

    `both_sides` marks a fixture where the squad holds players facing each
    other. Clean sheets are mutually exclusive, so it cushions a bad result and
    caps a good one in equal measure. That is sometimes what you want, but it
    should never happen by accident.
    """
    snapshot = _load()
    players = snapshot.squad.players
    try:
        fixtures = _market.fixtures()
    except SiteError as exc:
        return {"detail": str(exc)}

    target = round_number or snapshot.round_number
    if target is None:
        return {"detail": "no round given and the squad file does not name one"}

    by_id = {p.id: p for p in players}
    rows = fixture_exposure(players, fixtures, target)
    for row in rows:
        for side in ("home_players", "away_players"):
            row[side] = [
                {"id": i, "name": by_id[i].name, "position": by_id[i].position.value}
                for i in row[side]
            ]

    clubs = club_concentration(players)
    heaviest = rows[0] if rows else None
    return {
        **_provenance(snapshot),
        "round": target,
        "matches": rows,
        "by_club": [{"club": c, "players": n} for c, n in clubs],
        "largest_single_match": heaviest["count"] if heaviest else 0,
        "hedged_against_itself": [
            f"{r['home']} v {r['away']}" for r in rows if r["both_sides"]
        ],
    }


@server.tool()
def find_differentials(
    position: str = "",
    max_value: int = MARKET_MAX_VALUE,
    max_owned_percent: float = 100.0,
    min_matches: int = 1,
    limit: int = 15,
) -> dict[str, Any]:
    """Players producing more than their ownership normally buys.

    Ownership is the strongest single predictor in this market — by band, mean
    points per match runs -0.15 at 0-1% owned up to 5.33 above 30%. So buying
    the unowned is a losing move; the crowd is right about who is good.

    The point is narrower. A squad assembled from the most-owned players scores
    what everyone else scores, which cannot close a gap on someone ahead — it
    preserves it. What can is a player whose production sits above the line his
    own position and ownership predict.

    `residual` is that gap, fitted separately per position so a defender is
    only ever compared with defenders. It is a starting point for a decision,
    never the decision: `matches_played` is on every row because a big residual
    over one match is noise, and the squad's own players are excluded since a
    differential already owned is not a move.
    """
    try:
        market = [p for pos in Position for p in _market.search(pos)]
    except SiteError as exc:
        return {"detail": str(exc)}

    matches = _matches_by_club()
    if matches is None:
        return {"detail": "the calendar could not be read, so no rate can be computed"}

    wanted = None
    if position:
        wanted = {p.value: p for p in Position}.get(position.strip().upper())
        if wanted is None:
            return {"detail": f"position must be GK, DEF, MID or FWD, got {position!r}"}

    owned = {p.id for p in _load().squad.players}
    baseline = ownership_baseline(market, matches)
    rows = differential_rows(
        market, matches, baseline=baseline, exclude=owned, min_matches=min_matches
    )
    rows = [
        r
        for r in rows
        if (wanted is None or r["position"] == wanted.value)
        and r["value"] <= max_value
        and r["owned_percent"] <= max_owned_percent
    ]
    return {
        "as_of": _now_iso(),
        "source": "ligarecord",
        "count": len(rows),
        "fit": {
            pos.value: {"intercept": round(a, 3), "slope": round(b, 3)}
            for pos, (a, b) in sorted(baseline.items(), key=lambda kv: kv[0].value)
        },
        "fit_note": (
            "rate = intercept + slope * ln(1 + owned_percent), least squares per "
            "position over players whose club has played"
        ),
        "players": rows[:limit],
        "caveat": (
            "two rounds of football is a thin basis — check matches_played, and "
            "treat a large residual on a single match as unproven"
        ),
    }


@server.tool()
def standings(
    team: str = "",
    league_guid: str | None = None,
    page: int = 1,
    page_size: int = 20,
    national: bool = False,
) -> dict[str, Any]:
    """Where a team actually stands — nationally, or in one private league.

    Everything else this server produces is a projection: an argument about
    what should happen. This is the only tool that reports what did.

    Pass `team` to find a specific entry by name; without it you get the top of
    the table. `league_guid` switches to a private league, and comes from the
    league's own page on the site — it is not discoverable from here, because
    reading a participant's leagues needs their login.

    A position is reported together with the size of the field, since one
    without the other says very little.
    """
    # An implicit default that silently changes what a call means is how the
    # dashboard ended up asking for the private league three times and calling
    # the answer a national ranking. `national` says so out loud.
    guid = None if national else (league_guid or DEFAULT_LEAGUE)
    try:
        rows, pages = _market.standings(
            team=team, league_guid=guid, page=page, page_size=page_size
        )
    except SiteError as exc:
        return {"detail": str(exc)}

    scope = "private league" if guid else "national"
    out: dict[str, Any] = {
        "as_of": _now_iso(),
        "source": "ligarecord",
        "scope": scope,
        "page": page,
        "pages": pages,
        "teams": [
            {
                "position": r.position,
                "position_league": r.position_league,
                "team": r.team_name,
                "user": r.user_name,
                "points_total": r.points_total,
                "points_round": r.points_round,
                "position_round": r.position_round,
                "position_change": r.position_change,
                "formation": r.formation,
            }
            for r in rows
        ],
    }
    # Only an unfiltered query walks the whole field; with `team` set the page
    # count describes the matches for that name, which says nothing about how
    # many teams are playing.
    if pages and not guid and not team:
        out["field_size_estimate"] = pages * page_size
        out["field_size_note"] = (
            "pages x page_size, so the last page is partial and this is an upper bound"
        )
    if not rows:
        out["detail"] = "no team matched" if team else "the ranking came back empty"
    return out


@server.tool()
def list_coaches() -> dict[str, Any]:
    """The 18 selectable coaches (§6.15), best-scoring first.

    A coach is free — picking one never costs budget — and a round with none
    selected scores zero (§6.17). So there is no reason not to have one, and
    every reason to take the best available.
    """
    coaches = _coaches()
    if coaches is None:
        return {"detail": f"could not read the coach list at {COACHES_PATH}"}
    ranked = sorted(coaches, key=lambda c: (-c.points_total, c.name))
    return {
        "source": "manual",
        "count": len(ranked),
        "coaches": [
            {
                "id": c.id,
                "name": c.name,
                "club": c.club,
                "points_total": c.points_total,
                "points_round": c.points_round,
            }
            for c in ranked
        ],
    }


# --------------------------------------------------------------------------
# Tools — the transfer market (live, read-only)
# --------------------------------------------------------------------------


def _market_out(
    player, owned: set[str], matches: dict[str, int] | None = None
) -> dict[str, Any]:
    out = {
        "id": player.id,
        "name": player.name,
        "position": player.position.value,
        "club": player.club,
        "value": player.value,
        "points_total": player.points_total,
        "points_round": player.points_round,
        "owned_percent": player.owned_percent,
        "in_squad": player.id in owned,
    }
    if matches is not None:
        count = matches.get(player.club, 0)
        rate = per_match(player.points_total, count)
        out["matches_played"] = count
        out["points_per_match"] = None if rate is None else round(rate, 2)
    return out


@server.tool()
def search_market(
    position: str,
    max_value: int | None = None,
    min_points: int | None = None,
    max_owned_percent: float | None = None,
    club: str | None = None,
    name: str | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """Search the whole Liga Record player market, not just the squad.

    `position` is required — one of GK, DEF, MID, FWD. The site's endpoint
    returns nothing without one.

    `max_owned_percent` finds differentials: players few other teams hold. A
    high-scoring player owned by 40% of the league gains you nothing on the
    field; the same player owned by 3% is where places are won.

    This one reads the live site, so it is the only tool here whose answer can
    change without anyone editing a file. Results are cached for 15 minutes —
    quotes only move when a round is scored.
    """
    try:
        wanted = Position(position.upper())
    except ValueError:
        return {"detail": f"unknown position {position!r}; use GK, DEF, MID or FWD"}

    try:
        found = _market.search(
            wanted,
            name=name or "",
            club=club or "",
            max_value=max_value if max_value is not None else MARKET_MAX_VALUE,
        )
    except MarketError as exc:
        return {"detail": f"could not read the market: {exc}"}

    if min_points is not None:
        found = [p for p in found if p.points_total >= min_points]
    if max_owned_percent is not None:
        found = [p for p in found if p.owned_percent <= max_owned_percent]

    snapshot = _load()
    owned = set(snapshot.squad.by_id())
    matches = _matches_by_club()
    shown = found[: max(1, limit)]
    return {
        "source": "ligarecord (live)",
        "position": wanted.value,
        "matched": len(found),
        "showing": len(shown),
        "note": "rank on points_per_match — clubs are on different match counts",
        "players": [_market_out(p, owned, matches) for p in shown],
    }


@server.tool()
def check_market_transfer(out_id: str, in_id: str) -> dict[str, Any]:
    """Check a real transfer: one squad player out, one market player in.

    Both are player ids. Unlike check_transfer, the incoming player's price and
    position come from the live market rather than being described by hand — so
    the budget arithmetic uses their actual quote.
    """
    snapshot = _load()
    squad = snapshot.squad
    outgoing = squad.by_id().get(out_id)
    if outgoing is None:
        return {
            **_provenance(snapshot),
            "is_valid": False,
            "violations": [{"rule": "§6.8", "detail": f"{out_id} is not in the squad"}],
        }

    # Only that position's market is searched: §6.8 requires like for like, so
    # an incoming player from any other list would be illegal regardless.
    try:
        candidates = _market.search(outgoing.position)
    except MarketError as exc:
        return {**_provenance(snapshot), "detail": f"could not read the market: {exc}"}

    incoming = next((p for p in candidates if p.id == in_id), None)
    if incoming is None:
        return {
            **_provenance(snapshot),
            "is_valid": False,
            "violations": [
                {
                    "rule": "§6.8",
                    "detail": (
                        f"{in_id} is not among the {outgoing.position.value}s on the "
                        "market; a transfer must keep the positional contingent"
                    ),
                }
            ],
        }

    check = validate_transfer(squad, out_id, incoming.as_player())
    return {
        **_provenance(snapshot),
        "out": {"id": outgoing.id, "name": outgoing.name, "value": outgoing.value},
        "in": _market_out(incoming, set(squad.by_id())),
        "is_valid": check.is_valid,
        "squad_value_after": check.value_after,
        "balance_after": check.balance_after,
        "violations": _violations_out(check.violations),
    }


# --------------------------------------------------------------------------
# Tools — the calendar (live, read-only)
# --------------------------------------------------------------------------


def _fixture_out(fixture) -> dict[str, Any]:
    return {
        "round": fixture.round_number,
        "home": fixture.home,
        "away": fixture.away,
        "score": (
            f"{fixture.home_goals}-{fixture.away_goals}" if fixture.played else None
        ),
        "kickoff": fixture.kickoff,
        "played": fixture.played,
    }


def _next_round(fixtures) -> int | None:
    unplayed = [f.round_number for f in fixtures if not f.played]
    return min(unplayed) if unplayed else None


@server.tool()
def get_fixtures(
    round_number: int | None = None, club: str | None = None
) -> dict[str, Any]:
    """The league calendar: who plays whom, when, and the result if played.

    Defaults to the next unplayed round. `club` matches loosely and
    accent-insensitively, so "guimaraes" finds V. Guimarães.
    """
    try:
        fixtures = _market.fixtures()
    except SiteError as exc:
        return {"detail": f"could not read the calendar: {exc}"}

    upcoming = _next_round(fixtures)
    wanted = round_number if round_number is not None else upcoming
    found = [f for f in fixtures if wanted is None or f.round_number == wanted]

    if club is not None:
        needle = _fold(club)
        found = [f for f in found if needle in _fold(f.home) or needle in _fold(f.away)]

    return {
        "source": "ligarecord (live)",
        "round": wanted,
        "next_unplayed_round": upcoming,
        "count": len(found),
        "fixtures": [_fixture_out(f) for f in found],
    }


@server.tool()
def squad_fixtures(round_number: int | None = None) -> dict[str, Any]:
    """Each squad player's match for a round: opponent, and home or away.

    This is what turns "who is in form" into "who is in form and playing at
    home on Sunday". Players whose club has no match that round are listed
    separately — they cannot score, so they should not start.
    """
    snapshot = _load()
    try:
        fixtures = _market.fixtures()
    except SiteError as exc:
        return {**_provenance(snapshot), "detail": f"could not read the calendar: {exc}"}

    # The squad's own ronda is the authority, not the calendar's first unplayed
    # round: a single postponed match keeps a finished round "unplayed" for
    # days, and answering about that round would be wrong.
    wanted = round_number
    if wanted is None:
        wanted = snapshot.round_number or _next_round(fixtures)
    if wanted is None:
        return {**_provenance(snapshot), "detail": "the season has no unplayed rounds"}

    this_round = [f for f in fixtures if f.round_number == wanted]
    playing: list[dict[str, Any]] = []
    idle: list[dict[str, Any]] = []

    for player in snapshot.squad.players:
        match = next(
            (f for f in this_round if f.opponent_of(player.club) is not None), None
        )
        entry = {
            "id": player.id,
            "name": player.name,
            "position": player.position.value,
            "club": player.club,
            "points_total": player.points_total,
        }
        if match is None:
            idle.append(entry)
            continue
        playing.append(
            {
                **entry,
                "opponent": match.opponent_of(player.club),
                "at": "home" if match.is_home_for(player.club) else "away",
                "kickoff": match.kickoff,
            }
        )

    playing.sort(key=lambda e: (e["position"], -e["points_total"]))
    return {
        **_provenance(snapshot),
        "fixtures_round": wanted,
        "playing": playing,
        "no_match_this_round": idle,
    }


# --------------------------------------------------------------------------
# Tools — the appearance record
#
# The only tools here that write, and they write one local file. Nothing is
# ever sent to liga.record.pt.
# --------------------------------------------------------------------------


@server.tool()
def record_appearances(round_number: int | None = None) -> dict[str, Any]:
    """Snapshot who played in the last scored round, and keep it.

    Liga Record never publishes appearances, but §10.3 pays an unused player -1
    a round, so the scoring gives it away. Run this once after each round
    scores and the history accumulates — the one signal no free external source
    covers for the current season.

    Safe to run repeatedly: a round is keyed by its number and overwritten, not
    double-counted. Writes only to a local file.
    """
    try:
        fixtures = _market.fixtures()
    except SiteError as exc:
        return {"detail": f"could not read the calendar: {exc}"}

    target = round_number if round_number is not None else last_scored_round(fixtures)
    if target is None:
        return {"detail": "no round has been scored yet"}

    playing = clubs_playing_in(fixtures, target)
    if not playing:
        return {"detail": f"round {target} has no completed matches"}

    statuses: dict[str, str] = {}
    tally = {PLAYED: 0, UNUSED: 0, NO_MATCH: 0}
    for position in Position:
        try:
            for player in _market.search(position):
                status = classify_appearance(player.points_round, player.club in playing)
                statuses[player.id] = status
                tally[status] += 1
        except MarketError as exc:
            return {"detail": f"could not read the market: {exc}"}

    try:
        store = load_appearances(APPEARANCES_PATH)
        updated = record_round(store, target, statuses)
        save_appearances(APPEARANCES_PATH, updated)
    except SquadSourceError as exc:
        return {"detail": f"could not write the record: {exc}"}

    return {
        "round_recorded": target,
        "players": len(statuses),
        "played": tally[PLAYED],
        "unused": tally[UNUSED],
        "no_match": tally[NO_MATCH],
        "rounds_on_file": recorded_rounds(updated),
        "file": str(APPEARANCES_PATH),
        "caveat": (
            "a player who took the field and scored exactly -1 reads as unused; "
            "uncommon, and it washes out over rounds"
        ),
    }


@server.tool()
def appearance_history() -> dict[str, Any]:
    """How often each squad player has actually taken the field.

    Rounds where a player's club had no match are excluded from the
    denominator, so a postponed fixture never looks like being dropped.

    The gap between playing and not playing is worth 3-4 points a round —
    larger than form, price or fixtures — so a low rate here outweighs almost
    anything else about a player.
    """
    snapshot = _load()
    try:
        store = load_appearances(APPEARANCES_PATH)
    except SquadSourceError as exc:
        return {**_provenance(snapshot), "detail": f"could not read the record: {exc}"}

    rounds = recorded_rounds(store)
    if not rounds:
        return {
            **_provenance(snapshot),
            "rounds_on_file": [],
            "detail": "nothing recorded yet — run record_appearances after a round scores",
        }

    rows = []
    for player in snapshot.squad.players:
        history = history_for(store, player.id)
        rate = appearance_rate(history)
        rows.append(
            {
                "id": player.id,
                "name": player.name,
                "position": player.position.value,
                "club": player.club,
                "appearance_rate": None if rate is None else round(rate, 2),
                "rounds": history,
            }
        )
    rows.sort(key=lambda r: (r["appearance_rate"] is None, r["appearance_rate"] or 0))
    return {
        **_provenance(snapshot),
        "rounds_on_file": rounds,
        "players": rows,
    }


# --------------------------------------------------------------------------
# Tools — history and projection
# --------------------------------------------------------------------------


@server.tool()
def club_strength() -> dict[str, Any]:
    """Each club's record over completed seasons, against its output right now.

    Team strength explains most of a defender's scoring rate — clean sheets and
    goals conceded are club events, not individual ones — so a full season of
    it is worth far more than two matches of the current one.

    Clubs promoted this season carry `has_history: false`. They are not average;
    they are unknown, and the difference matters.
    """
    try:
        records = _history.club_records()
    except SiteError as exc:
        return {"detail": f"could not read the season archive: {exc}"}

    matches = _matches_by_club()
    snapshot = _load()
    now: dict[str, list[float]] = {}
    if matches:
        for position in Position:
            try:
                for player in _market.search(position):
                    count = matches.get(player.club, 0)
                    if count:
                        now.setdefault(player.club, []).append(player.points_total / count)
            except MarketError:
                break

    mine = {p.club for p in snapshot.squad.players}
    rows = []
    for club, record in records.items():
        rates = now.get(club, [])
        rows.append(
            {
                "club": club,
                "in_my_squad": club in mine,
                "seasons": list(record.seasons),
                "matches": record.matches,
                "goals_against_per_match": (
                    None
                    if record.goals_against_per_match is None
                    else round(record.goals_against_per_match, 2)
                ),
                "goals_for_per_match": (
                    None
                    if record.goals_for_per_match is None
                    else round(record.goals_for_per_match, 2)
                ),
                "clean_sheet_rate": (
                    None
                    if record.clean_sheet_rate is None
                    else round(record.clean_sheet_rate, 2)
                ),
                "has_history": record.has_history,
                "current_rate": round(sum(rates) / len(rates), 2) if rates else None,
            }
        )
    rows.sort(key=lambda r: (r["goals_against_per_match"] is None,
                             r["goals_against_per_match"] or 0))
    return {"source": "openfootball", "clubs": rows}


@server.tool()
def project_points(rounds_remaining: int = 32, prior_strength: float = PRIOR_STRENGTH) -> dict[str, Any]:
    """Project each squad player's scoring rate for the rest of the season.

    Blends what has been seen with a prior built from the club's record over
    completed seasons and the price Record set before the season began. Early
    on the prior dominates; as matches accumulate the observed form takes over.

    Every projection shows its working — `components` carries the position
    baseline, the club factor and the price factor — so the number can be
    argued with rather than taken on faith.

    `prior_strength` is how many matches the prior is worth; raise it to lean
    harder on history, lower it to trust the current season more.

    Partly validated, and it is worth knowing which part. Liga Record still
    publishes no past scores, so no projection this tool has made has ever been
    checked against a real season — record them now and settle them later.

    What has been measured is `prior_strength`, on a season reconstructed from
    §10.3 and zerozero's match record. Over 4232 player-splits, blending beat
    both extremes, and a player's own season to date turned out to be WORSE
    evidence than what a player at his club in his position typically returns.
    That is why the prior is worth ten rounds here and not four: early-season
    form is mostly not about the player.
    """
    snapshot = _load()
    matches = _matches_by_club()
    if matches is None:
        return {**_provenance(snapshot), "detail": "the calendar could not be read"}
    try:
        records = _history.club_records()
    except SiteError as exc:
        return {**_provenance(snapshot), "detail": f"no season archive: {exc}"}

    pool: list[Player] = []
    for position in Position:
        try:
            pool.extend(_market.search(position))
        except MarketError as exc:
            return {**_provenance(snapshot), "detail": f"could not read the market: {exc}"}

    market_players = [p.as_player() for p in pool]
    baselines = position_baselines(market_players, matches)
    played = [p for p in market_players if matches.get(p.club, 0) > 0]
    league_ga = sum(
        r.goals_against_per_match or 0 for r in records.values() if r.has_history
    ) / max(1, sum(1 for r in records.values() if r.has_history))
    league_gf = sum(
        r.goals_for_per_match or 0 for r in records.values() if r.has_history
    ) / max(1, sum(1 for r in records.values() if r.has_history))

    mean_value: dict[Position, float] = {}
    for position in Position:
        same = [p.value for p in played if p.position is position]
        mean_value[position] = sum(same) / len(same) if same else 0.0

    league_mean_value = (
        sum(p.value for p in played) / len(played) if played else 0.0
    )
    club_index = club_price_index(played, league_mean_value)

    # Whether a man is in the side is the largest single fact about him, and it
    # is the one the running average hides. The record of who actually played
    # comes from §10.3(i)'s -1, which is the only signal Liga Record gives:
    # `record_appearances` writes it down after each round.
    seen: dict[str, dict[str, str]] = {}
    league_availability = None
    try:
        store = load_appearances(APPEARANCES_PATH)
        if recorded_rounds(store):
            seen = {p.id: history_for(store, p.id) for p in snapshot.squad.players}
            everyone = [
                status
                for rounds in (store.get("rounds") or {}).values()
                for status in (rounds.get("players") or {}).values()
                if status != NO_MATCH
            ]
            if everyone:
                league_availability = sum(
                    1 for status in everyone if status == PLAYED
                ) / len(everyone)
    except SquadSourceError:
        # An unreadable record is a reason to project the old way, not to
        # refuse to project.
        seen, league_availability = {}, None

    rows = []
    for player in snapshot.squad.players:
        history = seen.get(player.id) or {}
        appearances = (
            sum(1 for status in history.values() if status == PLAYED)
            if history and league_availability
            else None
        )
        rows.append(
            project(
                player,
                matches.get(player.club, 0),
                records.get(player.club),
                baselines,
                mean_value.get(player.position, 0.0),
                league_ga,
                league_gf,
                club_index=club_index.get(player.club, 1.0),
                prior_strength=prior_strength,
                rounds_remaining=rounds_remaining,
                appearances=appearances,
                expected_availability=(
                    availability(history, league_rate=league_availability)
                    if appearances is not None
                    else None
                ),
                league_availability=league_availability,
            )
        )
    rows.sort(key=lambda r: -(r["projected_rate"] or 0))
    return {
        **_provenance(snapshot),
        "rounds_remaining": rounds_remaining,
        "prior_strength_matches": prior_strength,
        "league_goals_against_per_match": round(league_ga, 2),
        "availability_split": (
            "off — no appearance record yet; run record_appearances after a round"
            if league_availability is None
            else f"on, against a league rate of {league_availability:.0%}"
        ),
        "unvalidated": (
            "no projection made here has been checked against a finished "
            "season — treat it as a reasoned estimate. Its shrinkage weight is "
            "measured; its output is not"
        ),
        "players": rows,
    }


@server.tool()
def holiday_round(round_number: int | None = None) -> dict[str, Any]:
    """What §6.17's holiday mechanism would pay, against what the team scored.

    A participant may take three rounds off a season — consecutive or not,
    never in the last three — and be credited **half the round winner's score,
    rounded up**, whatever his own team did.

    It reads like a courtesy for people going on holiday. It is not. The
    payout is half of a maximum taken over more than a hundred thousand teams,
    which is a high and steady number; a single team's score is one draw with
    far more spread. So the mechanism is a floor, and the question each week is
    whether the floor is above what this squad is likely to manage.

    The right rounds to spend it on are the ones where the squad is
    compromised and the league is not — an international break, a congested
    week, three key players suspended. Nothing about the payout depends on who
    is injured.

    Three a season is the whole allowance and they do not come back.
    """
    snapshot = _load()
    target = round_number if round_number is not None else snapshot.round_number
    if target is None:
        return {
            **_provenance(snapshot),
            "detail": "no round to report on — pass round_number",
        }

    try:
        leaders, pages = _market.standings(
            page_size=5, round_number=target, order="round"
        )
        mine, _ = _market.standings(
            team=snapshot.squad.team_name, round_number=target, order="round"
        )
    except SiteError as exc:
        return {**_provenance(snapshot), "detail": f"could not read the round: {exc}"}

    if not leaders:
        return {
            **_provenance(snapshot),
            "round": target,
            "detail": "the round ranking is empty — it may not have been scored yet",
        }

    winner = leaders[0].points_round
    payout = -(-winner // 2)  # §6.17 rounds up
    ours = next(
        (row.points_round for row in mine if row.team_id == snapshot.squad.team_id),
        None,
    )

    verdict = None
    if ours is not None:
        difference = payout - ours
        verdict = (
            f"the holiday would have been worth {difference:+d}"
            if difference
            else "the holiday would have matched the team exactly"
        )

    return {
        **_provenance(snapshot),
        "round": target,
        "round_winner": winner,
        "holiday_pays": payout,
        "our_score": ours,
        "verdict": verdict,
        "teams_ranked": pages * 20,
        "allowance": (
            f"{HOLIDAY_ROUNDS} rounds a season, consecutive or not, none in the "
            f"last {HOLIDAY_BLOCKED_LAST_ROUNDS} (§6.17). This server cannot see "
            "how many have been used — the site can."
        ),
        "leaders": [
            {"team": row.team_name, "points_round": row.points_round}
            for row in leaders[:3]
        ],
    }


@server.tool()
def settle_decision(
    round_number: int,
    transfer_out: str = "",
    transfer_in: str = "",
    suggested_out: str = "",
    suggested_in: str = "",
    holiday: bool = False,
    captain: str = "",
    coach: str = "",
    top_scorer_bet: str = "",
    note: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write down what was decided in a round, and what the team scored.

    The projection ledger already records what was expected of each player and
    what he did. This records the other half — what the manager did with that.
    Only the pair answers the question the whole project rests on: over a
    season, did taking the advice beat not taking it?

    The score and the national rank are read from the site, so they are not
    arguments. Everything else is what the participant himself did and only he
    knows: which transfer he actually made, whether it was the suggested one,
    whether a holiday round was spent (§6.17 allows three), and who wore the
    armband.

    Pass `suggested_out` and `suggested_in` to record what was advised, even
    when it was not taken — an ignored suggestion that would have worked is the
    most useful row in the ledger, and it is the one that disappears if only
    the decisions are kept.

    A round already on file is not overwritten unless asked. Rewriting a
    decision after the result turns a record into a story.
    """
    snapshot = _load()
    try:
        store = load_decisions(DECISIONS_PATH)
    except SquadSourceError as exc:
        return {**_provenance(snapshot), "detail": f"could not read the ledger: {exc}"}

    ours, rank, field = None, None, None
    try:
        # Two calls, and the second one is the reason. Filtering by team name
        # narrows the RESULT, so its page count is the size of the filtered
        # list and not of the field — which turned a 53rd percentile into
        # 53380%. The field has to come from a query that filters nothing.
        # This project has now made that mistake twice.
        _, pages = _market.standings(
            page_size=5, round_number=round_number, order="round"
        )
        field = pages * 20
        rows, _ = _market.standings(
            team=snapshot.squad.team_name, round_number=round_number, order="round"
        )
        for row in rows:
            if row.team_id == snapshot.squad.team_id:
                ours, rank = row.points_round, row.position_round
                break
    except SiteError:
        # A ledger entry without the score is still worth having; the score can
        # be filled in later, the decision cannot be remembered later.
        pass

    try:
        entry = record_decision(
            store,
            round_number,
            our_points=ours,
            our_rank=rank,
            field_size=field,
            transfer_out=transfer_out or None,
            transfer_in=transfer_in or None,
            suggested_out=suggested_out or None,
            suggested_in=suggested_in or None,
            holiday=holiday,
            captain=captain or None,
            coach=coach or None,
            note=note,
            overwrite=overwrite,
        )
        if top_scorer_bet:
            record_season_choice(store, top_scorer=top_scorer_bet)
        save_decisions(DECISIONS_PATH, store)
    except SquadSourceError as exc:
        return {**_provenance(snapshot), "detail": str(exc)}

    used = holidays_used(store)
    return {
        **_provenance(snapshot),
        "round": round_number,
        "recorded": entry,
        "holidays_used": used,
        "holidays_left": HOLIDAY_ROUNDS - len(used),
        "written_to": str(DECISIONS_PATH),
    }


@server.tool()
def track_record() -> dict[str, Any]:
    """How the advice has actually done, from the ledger.

    Plain arithmetic over what is written down, on purpose. Anything cleverer
    would need more rounds than exist, and a confident summary of four rounds
    would be worse than no summary at all.

    `advice_taken` against `advice_given` is the line to watch. A model that
    projects well and is ignored is worth nothing; one that is followed while
    projecting badly is worse than nothing. Only the pair says which is
    happening here.
    """
    snapshot = _load()
    try:
        store = load_decisions(DECISIONS_PATH)
    except SquadSourceError as exc:
        return {**_provenance(snapshot), "detail": f"could not read the ledger: {exc}"}

    summary = track_record_of(store)
    if not summary["rounds"]:
        return {
            **_provenance(snapshot),
            "detail": "nothing recorded yet — call settle_decision after a round",
        }
    return {
        **_provenance(snapshot),
        **summary,
        "rounds_on_file": recorded_decisions(store),
        "caveat": (
            "a handful of rounds is a handful of rounds. Read the direction, "
            "not the decimal"
        ),
    }


_last_season = LastSeasonSource(LAST_SEASON_PATH)

#: Repeated on every answer built from the reconstruction. Liga Record did
#: publish scores last season; it just did not keep them, and it never
#: published the marks. Presenting these as Record's own numbers would be a
#: fabrication, and the caveat is cheaper than the correction.
RECONSTRUCTED = (
    "Reconstructed, not published. Goals, cards, clean sheets, wins and "
    "absences are computed from §10.3 on zerozero's match record and are "
    "exact; Record's editorial mark is estimated from zerozero's rating, to "
    "within about 0.44 points a match. Never quote these as Liga Record's own "
    "figures."
)


@server.tool()
def last_season(name: str) -> dict[str, Any]:
    """One player's 2025/26, scored the way Liga Record would have scored it.

    Liga Record keeps no history, so this is rebuilt rather than looked up —
    read the `basis` field before repeating any number from it.

    Returns the season total, the rate per round in the squad, the rate per 90
    minutes, and a rolling form line. The form line is the reason to ask: a
    total says a player was worth three a round, and the curve says whether he
    was worth six until January and one after it. With one transfer a round
    from round five, that difference is the whole decision.

    Promoted clubs have no top-flight season and come back empty, which is a
    fact about them and not a failure.
    """
    if not _last_season.available:
        return {
            "detail": (
                "no reconstruction on file — run scripts/build_last_season.py "
                "(about ten minutes, one polite read per player)"
            ),
        }
    try:
        found = _last_season.find(name)
    except LastSeasonError as exc:
        return {"detail": str(exc)}

    if not found:
        return {"query": name, "found": 0, "detail": "nobody by that name in the reconstruction"}
    if len(found) > 1:
        return {
            "query": name,
            "found": len(found),
            "candidates": [f"{p['name']} ({p['club']}, {p['position']})" for p in found],
            "detail": "more than one player matches — ask for one of these by full name",
        }

    player = found[0]
    matches = player["matches"]
    summary = season_summary(matches)
    estimated = sum(1 for m in matches if m.get("mark_source") == "assumed average")
    return {
        "basis": RECONSTRUCTED,
        "season": _last_season.load().get("season"),
        "name": player["name"],
        "club_now": player["club"],
        "position": player["position"],
        **summary,
        "rounds_without_a_rating": estimated,
        "form": form_curve(matches),
        "matches": sorted(matches, key=lambda m: m["round"]),
    }


@server.tool()
def last_season_leaders(
    position: str | None = None,
    limit: int = 15,
    minimum_matches: int = 20,
) -> dict[str, Any]:
    """Who was actually worth having last season, by reconstructed points.

    Ranked on the rate per round in the squad rather than the total, because a
    manager pays for a player every round whether he plays or not — a forward
    who returns eight in a third of the rounds and minus one in the rest is not
    an eight-point forward.

    `minimum_matches` guards against a man with four brilliant afternoons
    topping the list. It counts rounds his club played while he was on the
    books, not appearances.

    These are last season's players at last season's clubs. A player who has
    since moved carries his old club's fixtures, which is correct history and
    a poor guide to his next fixture — pair it with `squad_fixtures`.
    """
    if not _last_season.available:
        return {
            "detail": (
                "no reconstruction on file — run scripts/build_last_season.py "
                "(about ten minutes, one polite read per player)"
            ),
        }
    try:
        players = _last_season.players()
    except LastSeasonError as exc:
        return {"detail": str(exc)}

    wanted = position.upper() if position else None
    if wanted and wanted not in {p.value for p in Position}:
        return {"detail": f"unknown position {position!r}; use GK, DEF, MID or FWD"}

    rows = []
    for player in players.values():
        if wanted and player["position"] != wanted:
            continue
        summary = season_summary(player["matches"])
        if summary["matches"] < minimum_matches:
            continue
        rows.append(
            {
                "name": player["name"],
                "club_now": player["club"],
                "position": player["position"],
                "points": summary["points"],
                "per_match": summary["per_match"],
                "per_90": summary["per_90"],
                "matches": summary["matches"],
                "played": summary["played"],
                "goals": summary["goals"],
                "hauls": summary["hauls"],
                "blanks": summary["blanks"],
                "consistency": summary["consistency"],
            }
        )

    rows.sort(key=lambda r: r["per_match"], reverse=True)
    return {
        "basis": RECONSTRUCTED,
        "season": _last_season.load().get("season"),
        "position": wanted,
        "minimum_matches": minimum_matches,
        "qualified": len(rows),
        "players": rows[:limit],
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

## How points are awarded (§10.1, §10.3)

Every score is an editorial rating plus objective events. The rating is the
larger share and is not published anywhere — only the total appears — so it
can be predicted but never derived.

**The editorial rating (§10.1).** Record's writers mark each player 0-5, which
converts as: 0→0, 1→1, 2→2, 3→3, 4→4, **5→7**. Note the jump at the top: a 5
is worth three more than a 4, not one. §10.2 states plainly that Record will
not discuss an individual rating with anyone.

**On top of the rating (§10.3):**

| Event | GK | DEF | MID | FWD |
|---|---|---|---|---|
| Goal scored (not a penalty) | +20 | +4 | +3 | +2 |
| Each goal the club concedes | -2 | -1 | — | — |
| Club concedes none | +2 | +1 | — | — |
| Club wins (used players only) | +1 | +1 | +1 | +1 |

- Penalties: converted +2, missed -2, saved by the keeper +2.
- Hat-trick (three or more in a match): +5 on top.
- Red card: -3 direct, -1 for two yellows. Applies from the bench too.
- A forward who plays 75 minutes or more without scoring: -1.
- An own goal: -2 (§10.3(j)).
- **Player of the Week: +5 (§10.3(k))**, and the rule names the round's highest
  scorer rather than a jury — so it is derivable, not guesswork. Overlooking it
  is what left a 24-point round unexplainable until the clause was read.
- A selected player who is not used at all: -1 (§10.3(i)).
- The captain doubles whatever the total comes to (§10.3(l)), and repeats from
  the previous round if left unchanged.

That last -1 is what makes appearances readable at all: it is the only signal
this project has for who actually took the field, since Liga Record never
publishes a line-up.

## The coach (§6.15, §14)

A scoring slot in every round, and the only pick with no constraint on it: he
costs no budget (§6.4), may be changed every round (§6.15), repeats if left
alone (§6.16), and a team without one scores zero (§6.17). §14.4 adds his
points straight to the team's.

He is marked by the same newsroom on the same scale as a player (§14.1), and
§14.2 refuses to discuss that mark too. On top of it:

| Event | Coach |
|---|---|
| Win / draw / loss | +{COACH_WIN} / {COACH_DRAW} / {COACH_LOSS} |
| Team fails to score | {COACH_FAILED_TO_SCORE} |
| Team concedes none | +{COACH_CLEAN_SHEET} |
| Winning by more than {COACH_BIG_MARGIN} goals | +{COACH_BIG_WIN} |
| Losing by more than {COACH_BIG_MARGIN} goals | {COACH_BIG_LOSS} |
| Each goal scored by a substitute | +{COACH_SUBSTITUTE_GOAL} |
| Sent to the stand | {COACH_SENT_OFF} |

- **Coming from behind (§14.3(b))** is the term worth knowing. Drawing after
  being two down pays +2; **winning** after being two down pays +4, because the
  clause doubles it. The regulation's own example is exactly that.
- **A coach who cannot take the bench scores nothing at all (§14.5)** —
  suspended, sacked, or replaced by an assistant. Not zero-plus-the-result:
  nothing. His side can win 5-0 and the participant gets none of it. A coach
  who moves to another Liga Portugal club keeps scoring, at his new club.

## Automatic substitutions (§11)
- A starter who did not play, or whose match was abandoned or postponed after
  the round closed, is replaced.
- Same position only, processed in bench order.
- Where several same-position starters need replacing but only one substitute
  is eligible, it goes to the lower-valued starter, then the lower-scoring one.
- At most {MAX_SUBS_ON} substitutes come on.
- A replaced captain passes the armband to whoever comes on (§11.5).

## The trial period (§19)

Rounds 1-4 are a trial — "período experimental" — and round 5 "marca o início
efetivo do passatempo". It runs until 1 September.

- Points are scored exactly as in the real thing during those rounds.
- **They do not move prices.** This is why 0 of 498 players had been repriced
  after two rounds, a silence this project could not explain until §19 was
  read: the pricing rules of §12 simply are not running yet.
- Squads can be rebuilt freely rather than one transfer a round — seven were
  made in a single window, which is the evidence.
- A player whose value or position changes is REMOVED from squads holding him;
  one who leaves the league is hidden from search, kept in the squad, and
  scores -1 every round thereafter.

**The points do not carry.** §16.4 is explicit: the final standings count "a
pontuação acumulada entre a 6.ª e a 34.ª jornadas (inclusive)". Whatever a
squad scores before matchday {FIRST_SCORING_MATCHDAY} is a rehearsal — real
enough to learn from, and worth nothing in the table.

## A round is not a matchday, and there are two answers (§16.3, §16.4, §19)

The regulation uses "ronda" and "jornada" in one sentence and never says they
differ, and on where the competition starts it contradicts itself. Both
readings are carried here because both appear to be true of different things.

**What counts for us: matchdays {FIRST_SCORING_MATCHDAY} to {LAST_MATCHDAY},
{RECORD_ROUNDS} rounds.** Settled by watching the site rather than by reading:
the squad locks at matchday {FIRST_SCORING_MATCHDAY}, and so do §10.3(m)'s bet
and §6.8's one-transfer-a-round. Everything before it is free and uncounted.
This is the number to plan with.

**What §16.4 classifies on: matchdays {NATIONAL_FIRST_MATCHDAY} to
{LAST_MATCHDAY}, {NATIONAL_ROUNDS} rounds.** It counts "a pontuação acumulada
entre a 6.ª e a 34.ª jornadas", §16.5 says "cada uma das 29 rondas", §12.1 puts
the first price list at matchday 6, and §16.3 anchors the mapping twice — round
13 is matchday 18, round 29 is matchday 34 — which only holds from there.
§6.9's February window is stated in this numbering too.

The likeliest reading is that the national prize table starts a week after a
private league's tally does. Confuse the two and every deadline lands a week
out; treat either as the only one and something is wrong by a whole round.

## Transfers (§6.8 to §6.11)
- **One per round**, like for like: the positional contingent must hold.
- **They do not accumulate.** §6.8 grants "um só jogador entre cada ronda", and
  every extra §6.10 and §6.11 give is "na ronda em questão". An allowance
  belongs to its round and expires with it — skipping a round buys nothing.
- A player of yours **changing position** is worth {POSITION_CHANGE_TRANSFERS}
  extra transfers that round (§6.10); one **moving to another competition** is
  worth {COMPETITION_CHANGE_TRANSFERS} (§6.11), from round 2 onwards. Per
  affected player.
- **February**: the ordinary transfer switches off and the market reopens from
  2 February (round {REOPENED_FIRST_ROUND}) to fifteen minutes before matchday
  {REOPENED_LAST_MATCHDAY} on 28 February. Up to {REOPENED_MAX_SWAPS} swaps
  **for the whole window**, not per round. §6.11 suspends the compensations
  during it and in the round before.

## Two mechanisms that are easy to miss

**Holiday rounds (§6.17).** Three rounds a season, consecutive or not, never in
the last {HOLIDAY_BLOCKED_LAST_ROUNDS}, you may take **half the round winner's
score, rounded up**, whatever your own team did. Worth doing the arithmetic on
before filing it under "for when I am away": half of a winning round is
generally well above what a good team returns.

**The top-scorer bet (§10.3(m)).** Naming the season's leading scorer is worth
+{GOLDEN_BOOT_BONUS} in the final standings, +{SILVER_BOOT_BONUS} for the
runner-up, +{BRONZE_BOOT_BONUS} for third. It costs nothing, it is optional,
and it is made **once**, when the first version of the team is submitted. Not
making it is a decision, and the kind only noticed in May.

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
        "Help me pick my Liga Record team sheet for this round.\n\n"
        "Read ligarecord://regulamento first, then call get_squad.\n\n"
        "Compare players on `points_per_match`, never on `points_total`. Clubs "
        "are on different match counts whenever a fixture is postponed, and a "
        "total alone makes a good player at a club with a game in hand look "
        "poor. This has already produced one badly wrong recommendation.\n\n"
        "Then call squad_fixtures and read the kickoff times before deciding "
        "anything. A club whose match for this round is weeks away still scores "
        "for this round, but weeks later — and §11.3 covers the LOWER-valued "
        "failing starter first, so the expensive one can be the one left "
        "uncovered. Run simulate_autosubs to see what the bench would actually "
        "do rather than assuming it protects the best player.\n\n"
        "Call squad_exposure. Ten of twenty-three players once sat in a single "
        "postponed fixture and blanked together; a quarter of the squad in one "
        "match is a decision, not an accident, and it should be a conscious "
        "one.\n\n"
        "Pick a coach. He scores every round and the eighteen have spanned "
        "fourteen points to minus two, so leaving him as an afterthought throws "
        "away more than most transfers gain. list_coaches ranks them.\n\n"
        "Finish by calling validate_selection on the exact proposal — starters, "
        "bench order, captain and coach id. Do not tell me the sheet is legal "
        "without having run it. Quote the `as_of` timestamp rather than "
        "presenting stored data as live."
    )


@server.prompt()
def plan_transfers() -> str:
    """Think through this round's transfer."""
    return (
        "Help me plan my Liga Record transfer for this round.\n\n"
        "Read ligarecord://regulamento, then call get_squad. One transfer per "
        "round, like for like — a defender out means a defender in.\n\n"
        "Judge the squad on `points_per_match`, not totals, and check "
        "`never_played` before calling anyone weak: 42% of the market has never "
        "taken the field, and the gap between playing and not is worth more "
        "than form, price and fixtures combined.\n\n"
        "Then call find_differentials. Ownership is the strongest single "
        "predictor in this market — from -0.15 points a match below 1% owned up "
        "to 5.33 above 30% — so buying the unowned loses. But a squad drawn from "
        "the most-owned players scores what everyone scores, which holds a gap "
        "open rather than closing it. What moves places is a player producing "
        "above the line his own position and ownership predict. Weigh the "
        "residual against `matches_played`: a large one over a single match is "
        "noise.\n\n"
        "Use search_market for the position you are actually replacing: "
        "find_differentials ranks who is beating their ownership, but says "
        "nothing about who is available inside the budget, and a candidate I "
        "cannot afford is not a plan.\n\n"
        "Look at squad_fixtures and the next few rounds before committing — a "
        "player arriving in front of a hard run is a worse buy than his rate "
        "suggests.\n\n"
        "Confirm any proposal with check_market_transfer before recommending "
        "it. Do not tell me a transfer fits the budget or the positional quota "
        "without having run that check. Say plainly where you are relying on "
        "knowledge of Portuguese football rather than on these tools."
    )


@server.prompt()
def settle_the_round() -> str:
    """Score the round that just finished, and see what the projections got wrong."""
    return (
        "Help me close out the Liga Record round that has just been played.\n\n"
        "Every projection this server produces is reasoned, not measured — Liga "
        "Record has never published past scores, so there is nothing to test "
        "against except the record we keep ourselves. This is that step.\n\n"
        "Call record_appearances for the round so the squad's appearance "
        "history grows: §10.3 pays an unused player -1, which is the only "
        "signal available for who actually took the field.\n\n"
        "Only settle players whose club has actually played. A club with a "
        "postponed fixture leaves its players at 0, and that 0 is pending, not "
        "scored — entering it as a result would record an error against a "
        "projection that was never tested. get_fixtures shows which matches of "
        "the round are complete.\n\n"
        "Then look at the gap between projected and actual: is the model biased "
        "high or low, is it worse for one position than another, and did the "
        "opponent adjustment help or hurt? Two rounds is far too little to "
        "conclude anything — say so rather than reading a trend into noise.\n\n"
        "Finally call standings to see where the round left me, nationally and "
        "in the private league."
    )


def main() -> None:
    """Entry point: speak MCP over stdio."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
