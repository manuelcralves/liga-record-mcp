"""Record what was projected for a round, before the round is played.

The projections in this project have never been validated. Liga Record has
never published past scores, so there was nothing to test them against — every
number the server produces is reasoned, not measured, and says so.

This script is how that changes. Run it before a round kicks off and it stores
what was expected of every squad player; after the round, `--settle` fills in
what actually happened. A few rounds of that and the projections stop being an
argument and become a track record.

The ordering is the whole point, so a round already on file is never silently
overwritten — a "prediction" written after the result is worthless, and the
easiest way to end up with one is a careless re-run.

    python scripts/record_projection.py            # snapshot the current round
    python scripts/record_projection.py --settle   # fill in what happened
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
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    ManualSquadSource,
    OpenFootballClient,
)
from liga_record_mcp.stats import (  # noqa: E402
    adjust_for_fixture,
    club_price_index,
    fixture_multipliers,
    matches_played,
    position_baselines,
    project,
)

LOG_PATH = ROOT / "data" / "projections.json"
SQUAD_PATH = ROOT / "data" / "squad.yaml"


def league_rates(records):
    """Mean goals for and against among clubs with a record to read."""
    known = [r for r in records.values() if r.has_history]
    return (
        sum(r.goals_against_per_match for r in known) / len(known),
        sum(r.goals_for_per_match for r in known) / len(known),
    )


def club_rates(records, club, league_ga, league_gf):
    """A club's scoring rates, or the league's if it was promoted."""
    record = records.get(club)
    if record is None or not record.has_history:
        return league_ga, league_gf, False
    return record.goals_against_per_match, record.goals_for_per_match, True


def snapshot(market, history, squad, round_number):
    """Everything known about the coming round, per player."""
    records = history.club_records()
    fixtures = market.fixtures()
    counts = matches_played(fixtures)
    league_ga, league_gf = league_rates(records)

    everyone = [m.as_player() for pos in Position for m in market.search(pos)]
    baselines = position_baselines(everyone, counts)
    played = [p for p in everyone if counts.get(p.club, 0) > 0]
    mean_value = sum(p.value for p in played) / len(played)
    index = club_price_index(played, mean_value)
    position_mean = {
        pos: sum(p.value for p in played if p.position is pos)
        / max(1, sum(1 for p in played if p.position is pos))
        for pos in Position
    }

    this_round = [f for f in fixtures if f.round_number == round_number]
    if not this_round:
        raise SystemExit(f"the calendar has no round {round_number}")
    opponents = {}
    for f in this_round:
        opponents[f.home] = (f.away, True, f.kickoff)
        opponents[f.away] = (f.home, False, f.kickoff)

    rows = {}
    for player in squad.players:
        if player.club not in opponents:
            raise SystemExit(f"{player.club} has no round {round_number} fixture")
        opponent, at_home, kickoff = opponents[player.club]

        detail = project(
            player,
            counts.get(player.club, 0),
            records.get(player.club),
            baselines,
            position_mean[player.position],
            league_ga,
            league_gf,
            club_index=index.get(player.club, 1.0),
        )
        season_rate = float(detail["projected_rate"])

        own_ga, own_gf, known = club_rates(records, player.club, league_ga, league_gf)
        opp_ga, opp_gf, _ = club_rates(records, opponent, league_ga, league_gf)
        defensive, attacking = fixture_multipliers(
            own_ga, own_gf, opp_ga, opp_gf, league_ga, league_gf, at_home=at_home
        )
        adjusted = adjust_for_fixture(
            season_rate, player.position, defensive, attacking
        )

        rows[player.id] = {
            "name": player.name,
            "position": player.position.value,
            "club": player.club,
            "value": player.value,
            "opponent": opponent,
            "at_home": at_home,
            "kickoff": kickoff,
            "club_has_history": known,
            "season_rate": round(season_rate, 2),
            "observed_rate": detail["observed_rate"],
            "weight_on_form": detail["weight_on_form"],
            "defensive_multiplier": round(defensive, 3),
            "attacking_multiplier": round(attacking, 3),
            "projected": round(adjusted, 2),
            "points_before": player.points_total,
            "actual": None,
        }
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--settle",
        action="store_true",
        help="fill in what actually happened, for a round already on file",
    )
    args = parser.parse_args()

    snapshot_of_squad = ManualSquadSource(SQUAD_PATH).load()
    squad = snapshot_of_squad.squad
    market = LigaRecordClient(timeout=60.0)
    key = str(snapshot_of_squad.round_number)

    log = json.loads(LOG_PATH.read_text("utf-8")) if LOG_PATH.exists() else {"rounds": {}}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if args.settle:
        stored = log["rounds"].get(key)
        if stored is None:
            raise SystemExit(f"round {key} was never recorded — nothing to settle")
        live = {m.id: m for pos in Position for m in market.search(pos)}
        settled = 0
        for player_id, row in stored["players"].items():
            found = live.get(player_id)
            if found is None:
                continue
            row["actual"] = found.points_round
            row["error"] = round(found.points_round - row["projected"], 2)
            settled += 1
        stored["settled_at"] = now
        LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")
        errors = [r["error"] for r in stored["players"].values() if r.get("error") is not None]
        bias = sum(errors) / len(errors)
        spread = sum(abs(e) for e in errors) / len(errors)
        print(f"round {key} settled: {settled} players")
        print(f"  mean error      {bias:+.2f}  (positive = we were too pessimistic)")
        print(f"  mean size       {spread:.2f}  points off per player")
        return

    if key in log["rounds"]:
        raise SystemExit(
            f"round {key} is already on file, recorded {log['rounds'][key]['recorded_at']}.\n"
            "Refusing to overwrite — a projection rewritten after the fact proves nothing.\n"
            "Use --settle to add the results instead."
        )

    rows = snapshot(market, OpenFootballClient(timeout=60.0), squad,
                    snapshot_of_squad.round_number)
    log["rounds"][key] = {
        "recorded_at": now,
        "squad_value": squad.value(),
        "players": rows,
    }
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")

    print(f"round {key} recorded — {len(rows)} players, before kickoff")
    ranked = sorted(rows.items(), key=lambda kv: -kv[1]["projected"])
    for _, row in ranked[:5]:
        print(f"  {row['name']:<20} {row['projected']:>5.1f}  v {row['opponent']}")


if __name__ == "__main__":
    main()
