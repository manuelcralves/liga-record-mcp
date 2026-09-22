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

from liga_record_mcp.advice import (  # noqa: E402
    ESTIMATOR,
    players_to_value,
    round_projection,
    round_weeks,
    valuation,
)
from liga_record_mcp.coaches import (  # noqa: E402
    coach_points_by_club,
    rank_coaches,
    round_strengths,
)
from liga_record_mcp.optimise import best_eleven, left_the_league  # noqa: E402
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY, Position  # noqa: E402
from liga_record_mcp.source import (  # noqa: E402
    consistency_problems,
    load_official_rounds,
    LigaRecordClient,
    ManualSquadSource,
    OpenFootballClient,
    load_coaches,
)
from liga_record_mcp.stats import clubs_playing_in  # noqa: E402

from liga_record_mcp.source.last_season import archive_records  # noqa: E402
from liga_record_mcp.source.season import season_so_far  # noqa: E402
from liga_record_mcp.source.bulletin import known_out  # noqa: E402

LOG_PATH = ROOT / "data" / "projections.json"
SQUAD_PATH = ROOT / "data" / "squad.yaml"
COACHES_PATH = ROOT / "data" / "coaches.yaml"
#: Who cannot play, hand-maintained — the site does not publish it.
UNAVAILABLE_PATH = ROOT / "data" / "indisponiveis.yaml"
#: The Premium bulletin and suspensions board, copied each week; gitignored.
BULLETIN_DIR = ROOT / "data" / "boletim"

# THE COACH COMES FROM THE SHEET, in data/squad.yaml, like the eleven. It was
# a constant here, Farioli's id, until 18/09/2026, and round 7 was recorded
# with him while the sheet had Rui Borges. A coach scores every round (§6.15,
# §6.17) and the eighteen spanned 14 points to -2 after two, so recording the
# wrong one is not a rounding error.


def snapshot(market, history, squad, round_number):
    """Everything known about the coming round, per player, and what it rests on.

    The projection itself is `advice.round_projection`, the one the page and
    the server read too; what is here is the ledger's own: the guard that the
    site's totals and the emails agree, and the row it files.

    Returns the rows and the round's evidence: how many players the archive
    covered, which rounds of this season came from the emails and which were
    rebuilt from zerozero. The job on GitHub has neither the archive nor the
    rebuild, and a round it records first says so here.
    """
    records = history.club_records()
    fixtures = market.fixtures()
    everyone = [m.as_player() for pos in Position for m in market.search(pos)]

    weeks = round_weeks(records, fixtures, round_number)
    if not weeks:
        raise SystemExit(f"the calendar has no round {round_number}")

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
    # THE SEASON COMES FROM THE EMAILS, AND IS CHECKED BEFORE ANYTHING IS KEPT.
    #
    # On 15 September 2026 the site put every player's total back to zero for
    # the official phase, and the reading that stood here saw Pavlidis on nine
    # points in six matches. This file also runs unattended twice a day, from
    # registar-previsoes.yml, which commits whatever it records — so a
    # projection built on totals and emails that disagree must never reach the
    # ledger. It stops here and says why, and the scheduled job fails where
    # someone will see it.
    official = load_official_rounds(OFFICIAL_DIR, first_round=FIRST_SCORING_MATCHDAY)
    whole = {p.id: p for p in everyone}
    problems = consistency_problems(
        whole, official, fixtures, first_round=FIRST_SCORING_MATCHDAY
    )
    if problems:
        raise SystemExit(
            f"NAO REGISTO a jornada {round_number}: o total do site e os emails "
            "nao batem certo.\n  " + "\n  ".join(problems)
        )
    # POOLED OVER THE MARKET, as the page's transfer always was. Handed only the
    # twenty-three, `valuation` shrank each man toward his squad-mates.
    #
    # The season is `season_so_far`'s, the reading the page and the server take:
    # rounds 1-5 rebuilt from zerozero where the file exists, the emails from 6.
    # It is handed the emails read above, so the guard and the valuation read
    # the same files and the guard never sees a rebuilt round.
    archive = archive_records(ROOT / "data")
    season, sources = season_so_far(whole, ROOT / "data", emails=official)
    view = valuation(players_to_value(squad.players, whole), archive, season)

    # Who is known to be out this round, from the one file the site cannot
    # fill. Worth more than the transfer channel: playing a season out from
    # matchday 6, picking the XI blind scores 1246 and knowing who is out 1306.
    unavailable = known_out(UNAVAILABLE_PATH, BULLETIN_DIR, round_number, squad.players)
    if unavailable:
        print(f"  {len(unavailable)} fora da jornada {round_number}, pelo boletim e pelo ficheiro")

    missing = [p.name for p in squad.players if p.id not in view]
    if missing:
        raise SystemExit(f"{', '.join(missing)} has no valuation — cannot record a round")
    projected = round_projection(
        squad.players,
        view,
        weeks,
        unavailable=unavailable,
        gone=left_the_league([p.id for p in squad.players], whole),
    )

    rows = {}
    for player in squad.players:
        found = projected[player.id]
        record = records.get(player.club)

        # A CLUB WITH NO FIXTURE IS A ROW, NOT A REFUSAL — §15.3 scores the
        # match at nothing, and `round_projection` writes 0.0. Refusing made one
        # player from a club missing from the calendar cost the WHOLE round,
        # and an unrecorded round is gone from the track record for good.
        # Only the fixture's own fields are null; `fixture_grid` scales
        # `season_rate` for future rounds and the players table prints it.
        #
        # KNOWN NOT TO BE PLAYING reads -1, what §10.3(i) pays him: this is an
        # ESTIMATE scored against what he collects, and recording -1000 would
        # poison every accuracy figure the ledger produces. Keeping him out of
        # the eleven is a different job, done where the eleven is chosen.
        no_fixture = found["no_fixture"]
        row = {
            "name": player.name,
            "position": player.position.value,
            "club": player.club,
            "value": player.value,
            "opponent": found["opponent"],
            "at_home": found["at_home"],
            "kickoff": found["kickoff"],
            "club_has_history": bool(record is not None and record.has_history),
            "season_rate": round(float(found["season"]), 2),
            "returns": round(found["returns"], 2),
            "playing": round(found["playing"], 3),
            "appearances": found["appearances"],
            "defensive_multiplier": None if no_fixture else round(found["defensive"], 3),
            "attacking_multiplier": None if no_fixture else round(found["attacking"], 3),
            "projected": round(found["expected"], 2),
            "points_before": player.points_total,
            "actual": None,
        }
        if no_fixture:
            row["no_fixture"] = True
            print(
                f"  {player.name} ({player.club}) nao tem jogo na jornada "
                f"{round_number} — registado a 0.0 pelo §15.3"
            )
        if found["unavailable"] is not None:
            row["unavailable"] = found["unavailable"]
        if found["gone"]:
            row["gone"] = True
        rows[player.id] = row

    return rows, {"archive_players": len(archive), **sources}


def advised_sheet(rows: dict, coach: dict | None = None) -> dict | None:
    """The eleven the model would field, recorded rather than reconstructed.

    THE LEDGER KEPT THE MANAGER'S SHEET AND NOT ITS OWN ADVICE. The `filed`
    eleven was on record from the start; the model's was derived on demand from the
    projections beside it, which sounds equivalent and is not. The derivation
    runs today's `model_sheet`, and that reads today's §15.3 zeros and today's
    injury file — so the "advice for round 3" could quietly change months after
    round 3, and there would be no way to tell that it had.

    Measured, and this is why it matters: the eleven the estimator of 19 August
    proposed for round 3 differs from the one today's estimator proposes for
    the same round by two players. The armband held. Nothing recorded that.

    Built from this round's own projections, which are frozen the moment they
    are written — so this is the advice as it stood, permanently. `coach` is the
    model's coach of the round (`advised_coach`), filed with it for the same
    reason.
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
        "coach": coach,
    }

def ranked_coaches(history, fixtures, round_number):
    """The coach file, and the eighteen priced on this round's match.

    `coaches.rank_coaches` on the inputs the page ranks them on — the archive's
    club records and the calendar — so a coach on the ledger and the same coach
    on the page carry one number.
    """
    coaches = load_coaches(COACHES_PATH)
    strength = round_strengths(history.club_records(), fixtures)
    return coaches, rank_coaches(
        [c.model_dump() for c in coaches], fixtures, strength, round_number
    )


def coach_entry(row, *, points_before=None):
    """One coach as the ledger files him: who, against whom, and his expectation."""
    return {
        "id": row["id"],
        "name": row["name"],
        "club": row["club"],
        "points_before": points_before,
        # How the projection was made. Rounds filed before 22/09/2026 carry
        # none, and were projected by `stats.project_coach`: form and club
        # strength, and no opponent at all.
        "metodo": "jogo",
        "opponent": row["opponent"],
        "at_home": row["at_home"],
        "projected_rate": round(row["expected"], 2),
        "actual": None,
    }


def coach_snapshot(history, fixtures, round_number, coach_id):
    """The coach on the sheet, with what is expected of him this round.

    PRICED ON THE ROUND'S MATCH, since 22/09/2026: §14.3 over every scoreline
    of his club's fixture, from the Final Table's goal model, plus the average
    editorial mark (`coaches.rank_coaches`). It replaced `stats.project_coach`,
    which blended his form with his club's strength and never looked at who he
    played — and measured worse at it, on three seasons of openfootball: a
    correlation of 0.46-0.55 with his real round against 0.29-0.38.

    `--settle` still reads what he actually scored out of the round's email,
    or failing that the hand-maintained coach file, which is why the total he
    starts from is kept.

    `coach_id` is the coach on the filed sheet. None when the sheet names
    none: §6.17 scores that round zero, and inventing one would record a
    projection for a coach nobody picked.
    """
    if coach_id is None:
        return None
    coaches, ranked = ranked_coaches(history, fixtures, round_number)
    chosen = next((c for c in coaches if c.id == coach_id), None)
    if chosen is None:
        raise SystemExit(f"coach {coach_id} is not in {COACHES_PATH}")
    row = next(r for r in ranked if r["id"] == coach_id)
    return coach_entry(row, points_before=chosen.points_total)


def advised_coach(history, fixtures, round_number):
    """The coach the model would put on the sheet: the best of the round.

    Filed with the model's eleven, so the track record can ask of the coach
    what it asks of the players — whether taking the advice would have paid.
    """
    _, ranked = ranked_coaches(history, fixtures, round_number)
    if not ranked or ranked[0]["opponent"] is None:
        return None
    return coach_entry(ranked[0])


def settle_advised_coach(stored, official, playing) -> bool:
    """Close the model's coach from the round's email, by his club.

    Returns whether anything was written. The email carries all eighteen
    coaches; the model picked a club's coach, so the club is the key. Only once
    his club has played, as for the coach on the sheet: a postponed club's
    coach reads 0 in the email, and that 0 means nothing assigned yet, not a
    round that scored nothing — found by the code review of 22/09/2026.
    """
    coach = (stored.get("advised") or {}).get("coach")
    if not coach or coach.get("actual") is not None or not official:
        return False
    if coach["club"] not in playing:
        return False
    scored = coach_points_by_club(official, coach["club"])
    if scored is None:
        return False
    coach["actual"] = scored
    coach["error"] = round(scored - coach["projected_rate"], 2)
    print(
        f"  treinador do modelo {coach['name']}: {scored:+} "
        f"(projetado {coach['projected_rate']}, erro {coach['error']:+.2f})"
    )
    return True


def sheet_coach(snapshot_of_squad) -> str | None:
    """The coach id on the filed sheet, or None if the sheet names none."""
    picked = snapshot_of_squad.selection
    return picked.coach_id if picked is not None and picked.coach_id else None


def sheet_moved(stored: dict, held: set, out_now: set, coach_now: str | None) -> bool:
    """Whether the round on file was recorded for a team that has since changed.

    Three things make it so, and each re-records the round before kickoff:
    the twenty-three changed, the list of who is out changed, or the coach
    changed. The coach joined on 18/09/2026, when round 7 was on file with
    Farioli, then a constant here, while the sheet had Rui Borges.
    """
    out_then = {i for i, r in stored["players"].items() if r.get("unavailable")}
    coach_then = (stored.get("coach") or {}).get("id")
    return (
        held != set(stored["players"])
        or out_now != out_then
        or coach_now != coach_then
    )


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

    `anulados_15_3` is the way out, and it is written by hand, not taken from
    the email. §15.3 scores a postponed match 0 when it is played after the next
    round has begun, so for a club listed there the 0 IS the score. The field
    carries the match and the date it was resolved. Removing a club from it
    reopens the row, which is what to do if the site is ever seen paying the
    points anyway.
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
    the owner's weekly email. The settle step read that field and wrote round 2's
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
    parser.add_argument(
        "--round",
        type=int,
        help="with --settle: close a PAST round instead of the squad's current "
        "one, such as a round held open by a postponed match",
    )
    parser.add_argument(
        "--no-rerecord",
        action="store_true",
        help="record a round nobody has recorded, but never record one on file "
        "again. For a writer that cannot see data/boletim/ or the archive, "
        "such as the job on GitHub",
    )
    args = parser.parse_args()
    # Only a settle may look backwards. Recording a round is a prediction, and a
    # prediction for a round that has started proves nothing. The refusal
    # further down exists for exactly that, and a --round on the recording path
    # would walk straight past it.
    if args.round is not None and not args.settle:
        raise SystemExit(
            "--round so serve com --settle: registar uma jornada que nao e a "
            "atual e precisamente o que este ficheiro existe para impedir"
        )

    snapshot_of_squad = ManualSquadSource(SQUAD_PATH).load()
    squad = snapshot_of_squad.squad
    market = LigaRecordClient(timeout=60.0)
    key = str(args.round if args.round is not None else snapshot_of_squad.round_number)

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
                # Postponed stays pending, UNLESS §15.3 has already decided what
                # it is worth. A postponed match played after the next round has
                # begun scores 0 by rule, so the email's 0 is the score rather
                # than an unassigned one. Kept as its own list so `adiados`
                # stays what the email said.
                if row["club"] in official.get("adiados", ()) and row[
                    "club"
                ] not in official.get("anulados_15_3", {}):
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

        # And the model's coach, filed with its eleven since 22/09/2026. Still
        # open means the round is not whole yet, as for the coach on the sheet.
        advised_coach_filed = (stored.get("advised") or {}).get("coach")
        if settle_advised_coach(stored, official, playing):
            coach_settled = True
        elif advised_coach_filed and advised_coach_filed.get("actual") is None:
            pending.append(f"{advised_coach_filed['name']} (treinador do modelo)")

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
        chosen_coach = sheet_coach(snapshot_of_squad)
        if (
            "coach" not in stored
            and chosen_coach is not None
            and not clubs_playing_in(market.fixtures(), int(key))
        ):
            stored["coach"] = coach_snapshot(
                OpenFootballClient(timeout=60.0),
                market.fixtures(),
                int(key),
                chosen_coach,
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
        # `filed` records the eleven actually entered, and that keeps changing:
        # round 4 was snapshotted on 25 August with Santi García starting, the
        # Record's injury bulletin landed on the 27th, and Samu was swapped in
        # before the deadline. The ledger kept the old sheet, so it scored the
        # round at 53 against the site's 57 — Santi García's -1 where Samu's 3
        # belonged, and the difference is exactly four.
        #
        # The projections stay frozen because they ARE predictions and one
        # rewritten afterwards proves nothing. The sheet is a fact about what he
        # did, and the true one is whatever stood at kickoff. So it is refreshed
        # on every run until the round starts, and the guard below — which
        # refuses once a club has played — is what keeps that honest.
        # AND THE SQUAD ITSELF, for the same reason and with more force. The
        # sheet moving between snapshot and kickoff is a nuisance; the
        # TWENTY-THREE moving makes the whole round a prediction about players
        # he no longer owns. It happened on 2 September: the round was recorded
        # on the 1st, he swapped four men on the 2nd, and the ledger held a
        # forecast for a squad that had ceased to exist.
        #
        # Re-recording before kickoff is not a rewritten prediction — it is the
        # prediction, made about the team that will actually play. The guard
        # below still refuses the moment a club takes the field, which is the
        # line that matters.
        held = {p.id for p in squad.players}
        # AND WHEN THE INJURY LIST MOVES, for the same reason. The bulletin for
        # round 5 arrived hours after the snapshot: Zaidu had been recorded at
        # 3.23 and was going to collect §10.3(i)'s -1, which the ledger would
        # have charged to the model as four points of error for a fact it knew
        # before kickoff. Knowing it and not writing it down is the one thing
        # this file is for.
        fora_agora = set(known_out(UNAVAILABLE_PATH, BULLETIN_DIR, int(key), squad.players))
        mexeu = sheet_moved(stored, held, fora_agora, sheet_coach(snapshot_of_squad))
        # BUT NEVER BY A WRITER THAT CANNOT SEE WHAT THE ROUND WAS RECORDED WITH.
        #
        # The ledger has two writers: the laptop, and the job on GitHub, which
        # runs this on a fresh checkout. data/boletim/ is gitignored, and so is
        # the archive, so to the job the list of who is out is the hand file
        # alone. A snapshot naming a squad player the hand file does not makes
        # the round look moved to the job and to nobody else. It would record
        # the round again without the bulletin or the archive, under the same
        # estimator; the laptop would record it back; and the two would take
        # turns until kickoff, whichever wrote last being the prediction on
        # file. Round 7 escaped on 18/09 only because the six squad players on
        # that snapshot were the six in the hand file.
        #
        # So the job passes --no-rerecord. It still records a round nobody has,
        # which is why it exists, and still files the sheet below, which comes
        # from data/squad.yaml and reads the same on both. What it gives up is
        # re-recording a team changed from another machine while the laptop
        # is off, and that re-record was always the poorer model.
        if mexeu and args.no_rerecord:
            print(
                f"round {key}: a equipa mudou desde o instantaneo, mas com "
                "--no-rerecord uma jornada no ficheiro nao se regista de novo — "
                "fica para o portatil, que ve o boletim e o arquivo."
            )
        if mexeu and not args.no_rerecord and not clubs_playing_in(
            market.fixtures(), int(key)
        ):
            print(
                f"round {key}: o plantel mudou desde o instantaneo e a jornada "
                "ainda nao comecou — a registar de novo."
            )
            del log["rounds"][key]
        else:

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
            # Under --no-rerecord this also runs after a transfer, and the sheet
            # entered can then name a player the round holds no row for. It is
            # filed all the same: `filed` is the eleven actually entered, and
            # the track record leaves that round's own total blank rather than
            # score an older sheet nobody entered.
            if fresh != stored.get("filed") and not clubs_playing_in(
                market.fixtures(), int(key)
            ):
                stored["filed"] = fresh
                # The projections stand, so the model's coach filed with them
                # stands too.
                stored["advised"] = advised_sheet(
                    stored["players"], (stored.get("advised") or {}).get("coach")
                )
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
    fixtures = market.fixtures()
    rows, evidence = snapshot(market, history, squad, snapshot_of_squad.round_number)
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
        # weather. The name lives in `advice`, beside the estimate it names, so
        # it cannot be left behind when the estimate moves.
        "estimator": ESTIMATOR,
        # AND WHAT IT RAN ON. The same estimator on a fresh checkout has no
        # archive and no rebuilt rounds, and wrote rounds under this same name;
        # this is how one of those reads apart from the laptop's.
        "evidence": evidence,
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
        "advised": advised_sheet(
            rows, advised_coach(history, fixtures, snapshot_of_squad.round_number)
        ),
        "players": rows,
        "coach": coach_snapshot(
            history,
            fixtures,
            snapshot_of_squad.round_number,
            sheet_coach(snapshot_of_squad),
        ),
    }
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), "utf-8")

    print(f"round {key} recorded — {len(rows)} players, before kickoff")
    ranked = sorted(rows.items(), key=lambda kv: -kv[1]["projected"])
    for _, row in ranked[:5]:
        print(f"  {row['name']:<20} {row['projected']:>5.1f}  v {row['opponent']}")


if __name__ == "__main__":
    main()
