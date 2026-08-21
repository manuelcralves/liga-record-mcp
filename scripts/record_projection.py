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
    load_coaches,
)
from liga_record_mcp.stats import (  # noqa: E402
    adjust_for_fixture,
    project_coach,
    club_price_index,
    clubs_playing_in,
    fixture_multipliers,
    matches_played,
    position_baselines,
    project,
)

LOG_PATH = ROOT / "data" / "projections.json"
SQUAD_PATH = ROOT / "data" / "squad.yaml"
COACHES_PATH = ROOT / "data" / "coaches.yaml"

#: The coach on the sheet. A coach scores every round (§6.15, §6.17) and the
#: eighteen spanned 14 points to -2 after two, so leaving him out of the record
#: was leaving out roughly seven points a round of spread.
CHOSEN_COACH = "890"  # Farioli, FC Porto


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


def coach_snapshot(history, counts, round_number):
    """The chosen coach, with what is expected of him this round.

    His actual score cannot be computed from the calendar. Fitting the eighteen
    against wins, draws, clean sheets and margins reaches r-squared 0.85 with
    errors up to 3.8 and no integer structure — the residual behaves like the
    editorial rating that dominates a player's score. So this records the
    projection and the total he starts from, and `--settle` reads the new total
    out of the hand-maintained coach file.
    """
    coaches = load_coaches(COACHES_PATH)
    chosen = next((c for c in coaches if c.id == CHOSEN_COACH), None)
    if chosen is None:
        raise SystemExit(f"coach {CHOSEN_COACH} is not in {COACHES_PATH}")

    records = history.club_records()
    league_ga, league_gf = league_rates(records)
    rates = [
        c.points_total / counts[c.club] for c in coaches if counts.get(c.club, 0) > 0
    ]
    baseline = sum(rates) / len(rates) if rates else 0.0

    detail = project_coach(
        chosen.points_total,
        counts.get(chosen.club, 0),
        records.get(chosen.club),
        baseline,
        league_ga,
        league_gf,
    )
    return {
        "id": chosen.id,
        "name": chosen.name,
        "club": chosen.club,
        "points_before": chosen.points_total,
        "league_baseline_rate": round(baseline, 2),
        **detail,
        "actual": None,
    }


def settle_wrote_anything(
    *,
    settled: int,
    coach_settled: bool,
    pending: list,
    was_fully_settled: bool,
) -> bool:
    """Whether a --settle run has anything worth writing to the ledger.

    A run that settles nothing must leave the file BYTE-IDENTICAL, and the
    reason is not tidiness. The ledger has two writers — the laptop's scheduled
    task and the job on GitHub — and most runs settle nothing, because clubs
    have not played yet. Stamping `settled_at` anyway would dirty the file on
    every run on both sides: the laptop's `git pull --ff-only` would refuse
    forever, each side would go on reporting success, and the two ledgers would
    drift apart in silence. That is the one failure a track record cannot
    survive, and it hid behind a one-line timestamp.

    Completeness is in the test because it can change with nothing settled: a
    player who has left the league stops being pending without ever being
    scored.
    """
    if settled or coach_settled:
        return True
    return (not pending) != was_fully_settled


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

        # A club whose fixture has not been played yet sits at 0, and that 0 is
        # pending rather than scored. Settling it would enter a fabricated
        # error against the projection — the same mistake, in a new place, that
        # this whole module exists to avoid. Only played clubs are settled, and
        # the rest wait for a later run.
        playing = clubs_playing_in(market.fixtures(), int(key))
        live = {m.id: m for pos in Position for m in market.search(pos)}

        settled, pending, already = 0, [], 0
        for player_id, row in stored["players"].items():
            if row.get("actual") is not None:
                already += 1
                continue
            found = live.get(player_id)
            if found is None:
                continue
            if row["club"] not in playing:
                pending.append(row["name"])
                continue
            row["actual"] = found.points_round
            row["error"] = round(found.points_round - row["projected"], 2)
            settled += 1

        coach = stored.get("coach")
        coach_settled = False
        if coach and coach.get("actual") is None:
            if coach["club"] in playing:
                current = next(
                    (c for c in load_coaches(COACHES_PATH) if c.id == coach["id"]), None
                )
                if current is None:
                    print(f"  coach {coach['name']} is no longer in the coach file")
                elif current.points_total == coach["points_before"]:
                    print(
                        f"  coach {coach['name']}: the file still reads "
                        f"{current.points_total} — refresh data/coaches.yaml from the "
                        "site, then run --settle again"
                    )
                else:
                    coach_settled = True
                    coach["actual"] = current.points_total - coach["points_before"]
                    coach["error"] = round(coach["actual"] - coach["projected_rate"], 2)
                    print(
                        f"  coach {coach['name']}: {coach['actual']:+} "
                        f"(projected {coach['projected_rate']}, "
                        f"error {coach['error']:+.2f})"
                    )
            else:
                pending.append(f"{coach['name']} (treinador)")

        if settle_wrote_anything(
            settled=settled,
            coach_settled=coach_settled,
            pending=pending,
            was_fully_settled=stored.get("fully_settled", False),
        ):
            stored["settled_at"] = now
            stored["fully_settled"] = not pending
            LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")

        print(f"round {key}: {settled} newly settled, {already} already on file")
        if pending:
            print(f"  still pending ({len(pending)}): {', '.join(sorted(pending))}")
            print("  their clubs have not played this round — run again afterwards")

        errors = [
            r["error"] for r in stored["players"].values() if r.get("error") is not None
        ]
        if errors:
            bias = sum(errors) / len(errors)
            spread = sum(abs(e) for e in errors) / len(errors)
            print(f"  mean error      {bias:+.2f}  (positive = we were too pessimistic)")
            print(f"  mean size       {spread:.2f}  points off per player, over {len(errors)}")
        return

    if key in log["rounds"]:
        stored = log["rounds"][key]
        # The first version of this script had no coach. Adding one to a round
        # that has not kicked off is still a prediction; adding one afterwards
        # would not be, so the round's own fixtures decide whether it is allowed.
        if "coach" not in stored and not clubs_playing_in(market.fixtures(), int(key)):
            stored["coach"] = coach_snapshot(
                OpenFootballClient(timeout=60.0),
                matches_played(market.fixtures()),
                int(key),
            )
            LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")
            coach = stored["coach"]
            print(
                f"round {key} already had its players; added the coach "
                f"({coach['name']}, {coach['club']}, projected "
                f"{coach['projected_rate']}/round) — no match of this round "
                "has been played, so it is still a prediction"
            )
            return
        raise SystemExit(
            f"round {key} is already on file, recorded {log['rounds'][key]['recorded_at']}.\n"
            "Refusing to overwrite — a projection rewritten after the fact proves nothing.\n"
            "Use --settle to add the results instead."
        )

    # The whole value of this file is that every row was written before anyone
    # kicked a ball. Run by hand that is obvious; run on a schedule it is not,
    # and a job that fires late would quietly file a prediction it already knew
    # the answer to.
    started = clubs_playing_in(market.fixtures(), snapshot_of_squad.round_number)
    if started:
        raise SystemExit(
            f"round {key} has already begun — {len(started)} clubs have played.\n"
            "Refusing to record: a projection written after kickoff is not a "
            "prediction.\nUse --settle to add the results instead."
        )

    history = OpenFootballClient(timeout=60.0)
    rows = snapshot(market, history, squad, snapshot_of_squad.round_number)
    log["rounds"][key] = {
        "recorded_at": now,
        "squad_value": squad.value(),
        "players": rows,
        "coach": coach_snapshot(
            history, matches_played(market.fixtures()), snapshot_of_squad.round_number
        ),
    }
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")

    print(f"round {key} recorded — {len(rows)} players, before kickoff")
    ranked = sorted(rows.items(), key=lambda kv: -kv[1]["projected"])
    for _, row in ranked[:5]:
        print(f"  {row['name']:<20} {row['projected']:>5.1f}  v {row['opponent']}")


if __name__ == "__main__":
    main()
