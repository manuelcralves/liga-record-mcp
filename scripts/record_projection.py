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

from liga_record_mcp.advice import valuation  # noqa: E402
from liga_record_mcp.optimise import best_eleven  # noqa: E402
from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.source import (  # noqa: E402
    LigaRecordClient,
    load_appearances,
    load_unavailable,
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
    UNUSED_PENALTY,
)

from liga_record_mcp.source.appearances import current_records  # noqa: E402
from liga_record_mcp.source.last_season import archive_records  # noqa: E402

LOG_PATH = ROOT / "data" / "projections.json"
SQUAD_PATH = ROOT / "data" / "squad.yaml"
COACHES_PATH = ROOT / "data" / "coaches.yaml"
#: Who cannot play, hand-maintained — the site does not publish it.
UNAVAILABLE_PATH = ROOT / "data" / "indisponiveis.yaml"

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

    # THE ESTIMATOR THE PAGES ADVISE WITH, and until now this was not it.
    #
    # This file recorded `project()` — and called it without `appearances`, so
    # it got the FOLDED average, the one that function's own docstring says
    # hides the largest single fact about a fantasy footballer. The pages have
    # always used `valuation()`: two seasons of archive, this season so far,
    # and the split into whether he plays and what he returns when he does.
    #
    # The gap is not academic. Pavlidis, on 30 points from two rounds, recorded
    # at 9.19 by the folded average riding a two-round streak, where the split
    # shrinks it to about five. Every accuracy figure the ledger has produced
    # was measuring a model nobody was being advised by — the same fault
    # 162d930 found in the backtest, still sitting here because nobody checked
    # whether it was true twice.
    view = valuation(
        {p.id: p for p in squad.players},
        archive_records(ROOT / "data"),
        current_records(
            {m.id: m.as_player() for pos in Position for m in market.search(pos)},
            counts,
            load_appearances(ROOT / "data" / "appearances.json"),
        ),
    )

    # Who is known to be out this round, from the one file the site cannot
    # fill. Worth more than the transfer channel: playing a season out from
    # matchday 6, picking the XI blind scores 1246 and knowing who is out 1306.
    unavailable = load_unavailable(UNAVAILABLE_PATH, round_number)
    if unavailable:
        print(f"  {len(unavailable)} fora da jornada {round_number}, por ficheiro")

    rows = {}
    for player in squad.players:
        # The player's own estimate first, because none of it depends on who he
        # plays: `returns`, `playing` and the season rate are properties of the
        # man, and the fixture only scales them afterwards.
        entry = view.get(player.id)
        if entry is None:
            raise SystemExit(f"{player.name} has no valuation — cannot record a round")
        season_rate = float(entry["expected"])
        own_ga, own_gf, known = club_rates(records, player.club, league_ga, league_gf)

        # A CLUB WITH NO FIXTURE IS A ROW, NOT A REFUSAL.
        #
        # This raised SystemExit, so one player from a club missing from the
        # calendar made the WHOLE round unrecordable — and an unrecorded round
        # is gone from the track record for good, because the snapshot has to
        # be taken before kickoff and there is no going back to take it.
        #
        # build_dashboard, looking at the same fact, writes 0.0 under §15.3 and
        # carries on. Two halves of one system answering one question in
        # opposite ways, and the half that refused was the half that lost data.
        #
        # §15.3 is why 0.0 and not -1: a match not played before the next round
        # begins scores nothing, which is worse than a hard fixture and better
        # than the -1 for a man left out. Either way he is not in the eleven.
        #
        # Only the fixture's own fields are null here. Nulling the estimate too
        # would be a second bug: `fixture_grid` scales `season_rate` for future
        # rounds and sorts on it, and the players table prints it.
        if player.club not in opponents:
            rows[player.id] = {
                "name": player.name,
                "position": player.position.value,
                "club": player.club,
                "value": player.value,
                "opponent": None,
                "at_home": None,
                "kickoff": None,
                "club_has_history": known,
                "season_rate": round(season_rate, 2),
                "returns": round(entry["returns"], 2),
                "playing": round(entry["playing"], 3),
                "appearances": entry["appearances"],
                "defensive_multiplier": None,
                "attacking_multiplier": None,
                "projected": 0.0,
                "points_before": player.points_total,
                "actual": None,
                "no_fixture": True,
            }
            print(
                f"  {player.name} ({player.club}) nao tem jogo na jornada "
                f"{round_number} — registado a 0.0 pelo §15.3"
            )
            continue

        opponent, at_home, kickoff = opponents[player.club]
        opp_ga, opp_gf, _ = club_rates(records, opponent, league_ga, league_gf)
        defensive, attacking = fixture_multipliers(
            own_ga, own_gf, opp_ga, opp_gf, league_ga, league_gf, at_home=at_home
        )
        # The fixture scales what he returns WHEN HE PLAYS and never the blend:
        # §10.3(i) pays the same -1 whoever the opponent is, and scaling that
        # would make an easy fixture a reason to own a man who is not in the
        # side. This is the arithmetic build_dashboard does, to the letter.
        adjusted = entry["playing"] * adjust_for_fixture(
            entry["returns"], player.position, defensive, attacking
        ) + (1 - entry["playing"]) * float(UNUSED_PENALTY)

        # KNOWN NOT TO BE PLAYING, which the model cannot see for itself. Cards
        # it counts; injuries the site does not publish — a player's payload
        # carries fifteen fields and none is availability — and this project
        # does not read the press. So the estimate becomes what §10.3(i) pays a
        # man who does not play, minus one, rather than a guess about a game he
        # is not in.
        #
        # Minus one and not something huge: this is an ESTIMATE, scored against
        # what he really collects. Recording -1000 would make the error -999 and
        # poison every accuracy figure the ledger produces. Keeping him out of
        # the eleven is a different job, done where the eleven is chosen.
        why = unavailable.get(player.id)
        if why is not None:
            adjusted = float(UNUSED_PENALTY)

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
            "returns": round(entry["returns"], 2),
            "playing": round(entry["playing"], 3),
            "appearances": entry["appearances"],
            "defensive_multiplier": round(defensive, 3),
            "attacking_multiplier": round(attacking, 3),
            "projected": round(adjusted, 2),
            "points_before": player.points_total,
            "actual": None,
            **({"unavailable": why} if why is not None else {}),
        }

    return rows


def advised_sheet(rows: dict) -> dict | None:
    """The eleven the model would field, recorded rather than reconstructed.

    THE LEDGER KEPT MANUEL'S SHEET AND NOT ITS OWN ADVICE. His `filed` eleven
    was on record from the start; the model's was derived on demand from the
    projections beside it, which sounds equivalent and is not. The derivation
    runs today's `model_sheet`, and that reads today's §15.3 zeros and today's
    injury file — so the "advice for round 3" could quietly change months after
    round 3, and there would be no way to tell that it had.

    Measured, and this is why it matters: the eleven the estimator of 19 August
    proposed for round 3 differs from the one today's estimator proposes for
    the same round by two players. The armband held. Nothing recorded that.

    Built from this round's own projections, which are frozen the moment they
    are written — so this is the advice as it stood, permanently.
    """
    shaped = [
        {"id": i, "position": Position(row["position"]), "value": row["value"]}
        for i, row in rows.items()
    ]
    sheet = best_eleven(shaped, {i: row["projected"] for i, row in rows.items()})
    if sheet is None:
        return None
    return {
        "starters": list(sheet["starters"]),
        "bench": list(sheet["bench"]),
        "captain": sheet["captain"],
        "formation": sheet.get("formation"),
    }

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


#: Where the weekly email's figures are kept, one file per round. The email is
#: the only source that says which round its numbers belong to, so when a file
#: is here it outranks the API.
OFFICIAL_DIR = ROOT / "data" / "pontuacoes"


def official_scores(round_number: str) -> dict | None:
    """The round's points as Liga Record emailed them, if they have been filed.

    WHY THIS OUTRANKS THE LIVE API. `points_round` from the site means "the most
    recently scored round", and the site serves it for days before folding it
    into `points_total` — so between two rounds it is the PREVIOUS round's
    figures under the current round's name. Nothing in the payload says which.
    The email says, in its subject line, and it arrives before the API updates.

    A club in `adiados` has not played the round at all. Its players sit at 0 in
    the email for the same reason they sit at 0 on the site — nothing has been
    assigned yet — and settling that 0 would enter a fabricated score, so those
    clubs stay pending here exactly as they do on the API path.
    """
    path = OFFICIAL_DIR / f"{round_number}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def round_is_published(rows: dict, live: dict) -> bool:
    """Whether the site has actually added this round to the running totals.

    THE BUG THIS EXISTS FOR. `points_round` is "the latest scored round", not
    "the round you asked about", and the site publishes it days before it folds
    it into `points_total`. On 25 August the API served round 2's figures under
    `points_round` while round 3 had already been played and was sitting in
    Manuel's weekly email. The settle step read that field and wrote round 2's
    points into round 3's ledger for twenty players — Nehuén Pérez entered as 9
    when he had scored 4 — and then reported a mean error against them as if it
    measured anything.

    HOW IT IS CAUGHT. Every row carries `points_before`, the running total at
    the moment the projection was filed. When a round is published every
    player's total moves by that round's points. So if NOT ONE total has moved
    since the snapshot, the round is not on the site yet, whatever
    `points_round` claims. That is a property of the round, not of a player: a
    player can genuinely score 0, but a whole squad cannot leave every total
    untouched through a round that was actually scored.

    The coach has always been settled this way — by difference, refusing when
    the total still reads `points_before`. The players simply never were.
    """
    return any(
        found.points_total != row["points_before"]
        for player_id, row in rows.items()
        if (found := live.get(player_id)) is not None
    )


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

        official = official_scores(key)
        if official:
            print(
                f"  a usar as pontuacoes oficiais do email "
                f"({official['fonte']}, {official['recebido'][:16]})"
            )

        # THE API IS NOT ALLOWED TO SETTLE A ROUND. Only the email may.
        #
        # The first version of this guard checked that a player's running total
        # had moved by exactly `points_round`, and refused when it had not.
        # That is internal consistency, and it is not the same question.
        #
        # Round 4 was snapshotted on 25 August, when the site had rounds 1-2 in
        # its totals. On the 26th the site published round 3: every total moved
        # by round 3's points, and `points_round` became round 3. So
        # `gained == points_round` held perfectly — and twenty-one of
        # twenty-three players were settled with round 3's scores under round
        # 4's name. Santi García entered on 5, which was his round 3; Pavlidis
        # on 0, which was the round Benfica did not play.
        #
        # The field simply does not say which round it describes, and no amount
        # of arithmetic over fields that all describe the same wrong round can
        # recover it. The email says, in its subject line. So the email is the
        # only source allowed to close a round, and a round with no filed email
        # waits instead of guessing.
        published = official is not None
        if not published:
            print(
                f"  a jornada {key} nao tem pontuacoes em "
                f"data/pontuacoes/{key}.json — nada foi liquidado.\n"
                "  A API nao diz de que jornada sao os numeros dela; o email diz."
            )
            print(
                f"  a jornada {key} ainda nao esta somada aos totais do site — "
                "o points_round que ele serve e da jornada anterior. Nada foi "
                "liquidado."
            )

        settled, pending, already = 0, [], 0
        for player_id, row in stored["players"].items():
            if row.get("actual") is not None:
                already += 1
                continue
            found = live.get(player_id)
            if found is None:
                continue
            # The calendar decides this only when the email does not. Record's
            # results feed had no score for Sporting-Alverca on 25 August, two
            # days after it was played and scored — reading it would have held
            # Zalazar and Doumbia pending against an email that had already
            # paid them. `adiados` below is the email's own list, and it comes
            # from the same source as the points.
            if not official and row["club"] not in playing:
                pending.append(row["name"])
                continue
            if not published:
                pending.append(row["name"])
                continue

            if official:
                if row["club"] in official.get("adiados", ()):
                    pending.append(row["name"])
                    continue
                scored = official["jogadores"].get(f"{row['name']}|{row['club']}")
                if scored is None:
                    print(f"  {row['name']} ({row['club']}) nao vem no email da ronda")
                    pending.append(row["name"])
                    continue
            else:
                # The round is published, so this player's total must have moved
                # by exactly this round's points. When it has not, the two fields
                # disagree about which round they describe and neither can be
                # trusted for this row — a club scored late, or the snapshot was
                # taken after kickoff. Wait rather than pick one.
                gained = found.points_total - row["points_before"]
                if gained != found.points_round:
                    pending.append(row["name"])
                    continue
                scored = found.points_round

            row["actual"] = scored
            row["error"] = round(scored - row["projected"], 2)
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
                elif official and (
                    scored := official.get("treinadores", {}).get(
                        f"{coach['name']}|{coach['club']}"
                    )
                ) is not None:
                    coach_settled = True
                    coach["actual"] = scored
                    coach["error"] = round(scored - coach["projected_rate"], 2)
                    print(
                        f"  coach {coach['name']}: {coach['actual']:+} "
                        f"(projected {coach['projected_rate']}, "
                        f"error {coach['error']:+.2f})"
                    )
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
            if published:
                print("  their clubs have not played this round — run again afterwards")
            else:
                print(
                    "  a espera de que o site publique a jornada — corre outra vez "
                    "quando os totais subirem"
                )

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
        # THE SHEET IS NOT A PREDICTION, and it moves after the snapshot.
        #
        # `filed` records the eleven Manuel entered, and he keeps entering it:
        # round 4 was snapshotted on 25 August with Santi García starting, the
        # Record's injury bulletin landed on the 27th, and he swapped in Samu
        # before the deadline. The ledger kept the old sheet, so it scored his
        # round at 53 against the site's 57 — Santi García's -1 where Samu's 3
        # belonged, and the difference is exactly four.
        #
        # The projections stay frozen because they ARE predictions and one
        # rewritten afterwards proves nothing. The sheet is a fact about what he
        # did, and the true one is whatever stood at kickoff. So it is refreshed
        # on every run until the round starts, and the guard below — which
        # refuses once a club has played — is what keeps that honest.
        picked = snapshot_of_squad.selection
        fresh = (
            {
                "starters": list(picked.starters),
                "bench": list(picked.bench),
                "captain": picked.captain,
            }
            if picked is not None
            else None
        )
        if fresh != stored.get("filed") and not clubs_playing_in(
            market.fixtures(), int(key)
        ):
            stored["filed"] = fresh
            stored["advised"] = advised_sheet(stored["players"])
            LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")
            print(
                f"round {key}: a folha mudou desde o instantaneo e ainda nao ha "
                "jogo — atualizada. As projecoes ficam como estavam."
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
    # THE SHEET HE FILED, alongside what was expected of it. Without this the
    # ledger can say whether the model predicted well, and never whether
    # following it would have paid — which is the question he actually asked.
    # It is read from the same squad file the projections come from, so it is
    # what he had entered at the moment the round was snapshotted, and like
    # everything else here it is written before kickoff.
    picked = snapshot_of_squad.selection
    log["rounds"][key] = {
        "recorded_at": now,
        # Which estimator wrote this round. Rounds recorded before this existed
        # came from the folded `project()`; mixing the two in one accuracy
        # figure would average across a change of model and report it as
        # weather.
        "estimator": "valuation+fixture",
        "squad_value": squad.value(),
        "filed": (
            {
                "starters": list(picked.starters),
                "bench": list(picked.bench),
                "captain": picked.captain,
            }
            if picked is not None
            else None
        ),
        # AND THE ADVICE ITSELF, beside the sheet he entered. Both are now
        # facts on file rather than one fact and one derivation, so the
        # comparison on the track-record page reads the same in May as it did
        # in August.
        "advised": advised_sheet(rows),
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
