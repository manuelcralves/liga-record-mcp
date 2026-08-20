"""Build the per-player history the model has never had.

Everything historical in this project is team-level: openfootball carries 306
results a season and not one player name, so the model reads a footballer as
"a forward at this club at this price" and knows nothing else about him. This
fills that in from zerozero — appearances, minutes, goals, assists.

Minutes are the point. Points per match cannot tell a nailed-on starter from a
substitute who scored: Héctor Hernández looked like a seven-point-a-game
forward and turns out to have played fourteen minutes.

Politeness and cost. One request at a time with a real pause, every page kept
on disk, so a second run of this costs nothing. Players whose club has not
played, and those the -1 rule shows have never taken the field, are skipped —
there is nothing for zerozero to tell us about a man who has not played, and
not asking is both faster and better manners.

Resumable by construction: interrupt it and run it again.

    python scripts/build_player_history.py             # everyone who has played
    python scripts/build_player_history.py --squad     # just the 23
    python scripts/build_player_history.py --limit 40  # a taste, for trying it
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, ManualSquadSource  # noqa: E402
from liga_record_mcp.source.zerozero import ZeroZeroClient, ZeroZeroError  # noqa: E402
from liga_record_mcp.stats import matches_played, never_played  # noqa: E402

OUT_PATH = ROOT / "data" / "players.json"
CACHE_DIR = ROOT / "data" / "zerozero-cache"
SQUAD_PATH = ROOT / "data" / "squad.yaml"

#: The competition line we want. A player's European minutes are real but they
#: are not what Liga Record scores, and mixing them would overstate his league
#: workload — Pavlidis has 218 minutes in Europe and 149 in the league.
LEAGUE = "liga portuguesa"


def league_line(seasons):
    return next((s for s in seasons if LEAGUE in s.competition.lower()), None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--squad", action="store_true", help="only the 23 under contract")
    parser.add_argument("--limit", type=int, help="stop after this many players")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between reads")
    args = parser.parse_args()

    market = LigaRecordClient(timeout=60.0)
    players = [p.as_player() for pos in Position for p in market.search(pos)]
    counts = matches_played(market.fixtures())

    if args.squad:
        wanted = {p.id for p in ManualSquadSource(SQUAD_PATH).load().squad.players}
        players = [p for p in players if p.id in wanted]

    # Nothing to learn about a man who has not played, and not asking is both
    # faster and better manners.
    playing = [
        p
        for p in players
        if counts.get(p.club, 0) > 0 and not never_played(p, counts.get(p.club, 0))
    ]
    skipped = len(players) - len(playing)
    if args.limit:
        playing = playing[: args.limit]

    stored = json.loads(OUT_PATH.read_text("utf-8")) if OUT_PATH.exists() else {}
    known = stored.get("players", {})

    zero = ZeroZeroClient(CACHE_DIR, pause=args.pause)
    print(f"{len(playing)} to cover, {skipped} skipped as never used, {len(known)} on file")

    matched, refused, failed = 0, [], []
    for n, player in enumerate(playing, 1):
        if player.id in known:
            matched += 1
            continue
        try:
            found, why = zero.find(player.name, player.club)
            if found is None:
                refused.append((player.name, player.club, why))
                continue
            line = league_line(zero.seasons(found))
        except ZeroZeroError as exc:
            failed.append((player.name, str(exc)))
            continue

        known[player.id] = {
            "name": player.name,
            "club": player.club,
            "position": player.position.value,
            "zerozero_id": found.zid,
            "zerozero_name": found.name,
            "matched_by": why,
            "matches": line.matches if line else 0,
            "minutes": line.minutes if line else 0,
            "minutes_per_match": round(line.minutes_per_match, 1)
            if line and line.minutes_per_match is not None
            else None,
            "goals": line.goals if line else 0,
            "assists": line.assists if line else 0,
            "conceded": line.conceded if line else None,
            "league_line_found": line is not None,
        }
        matched += 1
        if n % 25 == 0 or n == len(playing):
            print(f"  {n}/{len(playing)} — {matched} matched, {len(refused)} refused")
            OUT_PATH.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                        "players": known,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )

    OUT_PATH.write_text(
        json.dumps(
            {
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "players": known,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print()
    print(f"wrote {OUT_PATH} — {len(known)} players, {zero.requests_made} new reads")
    if refused:
        print(f"\nrefused ({len(refused)}) — no guess made:")
        for name, club, why in refused[:20]:
            print(f"  {name[:20]:<20} {club[:13]:<13} {why[:58]}")
        if len(refused) > 20:
            print(f"  ... and {len(refused) - 20} more")
    if failed:
        print(f"\nfailed to read ({len(failed)}):")
        for name, why in failed[:10]:
            print(f"  {name[:20]:<20} {why[:60]}")


if __name__ == "__main__":
    main()
