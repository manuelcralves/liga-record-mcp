"""Settling a round the site has not published yet.

WHAT HAPPENED. On 25 August 2026 the scheduled job settled round 3 from the
live API and wrote twenty scores into the ledger. Every one of them belonged to
round 2. Manuel caught it from his weekly email — it showed Nehuén Pérez on 4
while our ledger had recorded 9 — and the ledger, the error figures and a whole
paragraph of analysis about a defender's goal in FC Porto 2-0 Arouca were built
on round 2's numbers wearing round 3's label.

WHY IT WAS POSSIBLE. `points_round` means "the most recently scored round",
not "the round you asked about", and Liga Record serves it for days before
folding it into `points_total`. Nothing in the settle step ever asked which
round the field described. The coach branch had always asked — it settles by
difference and refuses while the total still reads `points_before` — so the
check existed in the file, one function away from the players who needed it.

THE INVARIANT. Publishing a round moves every player's running total by that
round's points. So `points_total - points_before == points_round` for a
published round, and if not one total in the squad has moved, the round is not
published at all.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location(
        "record_projection", ROOT / "scripts" / "record_projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Live:
    def __init__(self, total, round_points):
        self.points_total = total
        self.points_round = round_points


def rows(*befores):
    return {str(i): {"points_before": b, "name": f"p{i}"} for i, b in enumerate(befores)}


def live(*pairs):
    return {str(i): Live(t, r) for i, (t, r) in enumerate(pairs)}


# --- the round-level gate -----------------------------------------------------


def test_a_round_the_site_has_not_added_is_not_published(ledger):
    """The exact shape of the 25 August failure: totals frozen, round non-zero.

    Every total still reads what it read at the snapshot, while `points_round`
    offers 7, 9 and 5 — the previous round's figures, which is precisely what
    got written into round 3.
    """
    assert not ledger.round_is_published(
        rows(14, 13, 10), live((14, 7), (13, 9), (10, 5))
    )


def test_a_round_that_moved_the_totals_is_published(ledger):
    assert ledger.round_is_published(rows(14, 13), live((21, 7), (17, 4)))


def test_one_club_scoring_late_does_not_hold_up_the_round(ledger):
    """A published round only has to have moved somebody."""
    assert ledger.round_is_published(rows(14, 13), live((21, 7), (13, 9)))


def test_a_squad_that_all_scored_nothing_still_reads_as_unpublished(ledger):
    """Deliberately conservative, and the safe way round.

    Twenty-three players held to zero in one round is somewhere between rare
    and impossible; an unpublished round looks exactly like it every single
    time. Waiting one more run costs nothing, and the ledger has one writer per
    round that can never be corrected once it is wrong.
    """
    assert not ledger.round_is_published(rows(5, 5), live((5, 0), (5, 0)))


def test_a_player_the_market_has_dropped_is_not_evidence_either_way(ledger):
    """No live row, so no total to compare — it must not count as movement."""
    assert not ledger.round_is_published(rows(5, 5), {"0": Live(5, 0)})


def test_the_gate_reads_every_player_not_just_the_first(ledger):
    moved_last = live((5, 0), (5, 0), (11, 6))
    assert ledger.round_is_published(rows(5, 5, 5), moved_last)


# --- the per-player check -----------------------------------------------------
#
# The gate alone would let through a published round in which one player's two
# fields disagree — a club scored after the snapshot was taken, say. That row
# cannot be settled from either field, because neither says which round it is.


def test_the_two_fields_have_to_agree_about_the_round(ledger):
    """`points_before` + this round = the total, or the row waits."""
    found = Live(total=20, round_points=9)
    before = 14
    assert found.points_total - before != found.points_round

    agreeing = Live(total=23, round_points=9)
    assert agreeing.points_total - before == agreeing.points_round


def test_the_settle_step_still_asks_whether_the_club_played(ledger):
    """The older guard, which this one sits beside rather than replaces."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "clubs_playing_in" in source
    assert 'row["club"] not in playing:' in source


def test_the_settle_step_consults_the_gate_before_writing(ledger):
    """Cheap, but it is the line that was missing."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "round_is_published(" in source, (
        "the settle step writes actuals without asking whether the round is on "
        "the site — the 25 August bug, restored"
    )
    assert "gained != found.points_round" in source


# --- the ledger must not still be carrying the bad round ----------------------


def test_round_three_is_not_settled_with_round_twos_numbers():
    """A regression test against the data, not the code.

    These twenty rows were written by the broken run. If they ever come back —
    a stale copy restored, a merge from the other writer — this fails, because
    the numbers themselves are recognisable: they are round 2's.
    """
    import json

    log = json.loads(
        (ROOT / "data" / "projections.json").read_text(encoding="utf-8")
    )
    round_three = log["rounds"].get("3")
    if round_three is None:
        pytest.skip("round 3 is no longer on file")
    perez = next(
        (r for r in round_three["players"].values() if "Nehuén" in r["name"]), None
    )
    if perez is None:
        pytest.skip("Nehuén Pérez is no longer in the squad")
    assert perez.get("actual") != 9, (
        "Nehuén Pérez is on 9 for round 3 — that is his round 2 score, and the "
        "weekly email says he scored 4"
    )


# --- the email outranks the API ----------------------------------------------
#
# Liga Record mails the full round — every player, every club, under a subject
# that names the round — hours after the last match and days before the API
# folds it into the totals. It is the only source that says which round its
# numbers describe, so when a round has been filed it decides.


def test_a_filed_round_is_read(ledger):
    filed = ledger.official_scores("3")
    assert filed is not None, "data/pontuacoes/3.json is missing"
    assert filed["ronda"] == 3
    assert filed["jogadores"]["Nehuén Pérez|FC Porto"] == 4


def test_a_round_nobody_filed_is_absent_not_an_error(ledger):
    assert ledger.official_scores("99") is None


def test_players_are_keyed_by_club_because_names_repeat(ledger):
    """Two Diogo Costas, two Samus, two Robinhos in one round's email.

    Ours are the FC Porto keeper and the V. Guimarães forward; keying by name
    alone would have paid the Gil Vicente defender's -1 and the FC Porto
    forward's -1 into the ledger instead of 6 and 2.
    """
    filed = ledger.official_scores("3")
    assert "Diogo Costa|FC Porto" in filed["jogadores"]
    assert "Samu|V. Guimarães" in filed["jogadores"]
    assert not any(k in ("Diogo Costa", "Samu") for k in filed["jogadores"])


def test_a_postponed_club_is_named_so_its_zero_is_not_settled(ledger):
    """A 0 in the email means the same as a 0 on the site for these clubs.

    Benfica and Sp. Braga did not play round 3 — their fixtures are 9 and 10
    September — so all thirty and all twenty-eight of their players read 0.
    That is "nothing assigned", not "scored nothing", and Pavlidis entering the
    ledger on 0 would be the 25 August bug wearing a better source.
    """
    filed = ledger.official_scores("3")
    assert set(filed["adiados"]) >= {"Benfica", "Sp. Braga"}
    assert filed["jogadores"]["Pavlidis|Benfica"] == 0


def test_the_settle_step_prefers_the_email_over_the_calendar(ledger):
    """Sporting-Alverca: played and scored, absent from the results feed.

    `clubs_playing_in` reads that feed. Left in charge it would have held two
    players pending against an email that had already paid them.
    """
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert 'if not official and row["club"] not in playing:' in source


# --- what the ledger should now hold -----------------------------------------


def test_nehuen_perez_is_on_four_for_round_three():
    """The number Manuel read in his email, and the reason any of this happened."""
    import json

    log = json.loads(
        (ROOT / "data" / "projections.json").read_text(encoding="utf-8")
    )
    perez = next(
        r for r in log["rounds"]["3"]["players"].values() if "Nehuén" in r["name"]
    )
    assert perez["actual"] == 4, (
        f"round 3 has Nehuén Pérez on {perez['actual']} — the email says 4, and "
        "9 was his round 2 score read from a lagging API"
    )


def test_the_postponed_clubs_are_still_waiting():
    import json

    log = json.loads(
        (ROOT / "data" / "projections.json").read_text(encoding="utf-8")
    )
    for row in log["rounds"]["3"]["players"].values():
        if row["club"] in ("Benfica", "Sp. Braga"):
            assert row["actual"] is None, (
                f"{row['name']} was settled on a round his club has not played"
            )


# --- internal consistency is not the same question ----------------------------
#
# The first guard checked that a player's running total had moved by exactly
# `points_round`, and refused when it had not. That is a real check and it is
# not enough, because it never asks WHICH round the field describes.
#
# What happened on 1 September. Round 4 was snapshotted on 25 August, when the
# site had rounds 1-2 in its totals. On the 26th the site published round 3:
# every total moved by round 3's points, and `points_round` became round 3. So
# `gained == points_round` held perfectly for every player — and twenty-one of
# twenty-three were settled with round 3's scores under round 4's name. Santi
# García entered on 5, which was his round 3; Pavlidis on 0, which was the
# round Benfica did not play. Manuel's weekly email caught it, again.
#
# No arithmetic over fields that all describe the same wrong round can recover
# which round it is. The email says, in its subject line. So the email is now
# the only source allowed to close a round.


def test_a_lagging_round_passes_the_arithmetic_check(ledger):
    """The hole, stated as the thing the old guard could not see.

    Snapshot at 14 (rounds 1-2 in). Then round 3 publishes: the total moves by
    round 3's points and `points_round` is round 3. Consistent, and wrong.
    """
    rows = {"1": {"points_before": 14, "name": "p1"}}
    live = {"1": Live(total=14 + 5, round_points=5)}
    assert ledger.round_is_published(rows, live), (
        "the arithmetic check refuses this, which is not the failure being "
        "described — it passes, and that is the problem"
    )
    gained = live["1"].points_total - rows["1"]["points_before"]
    assert gained == live["1"].points_round


def test_only_a_filed_email_may_close_a_round(ledger):
    """The fix: the API cannot settle at all, however consistent it looks."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "published = official is not None" in source, (
        "the API can settle a round again, and it cannot say which round it is "
        "reporting"
    )
    assert "A API nao diz de que jornada sao os numeros dela" in source


def test_a_round_with_no_email_settles_nothing(ledger):
    assert ledger.official_scores("99") is None


def test_round_four_holds_the_email_figures_not_the_site_ones():
    """A regression test against the data. These twenty-one values are
    recognisable: they are round 3's, and they were in the ledger for an hour."""
    import json

    log = json.loads(
        (ROOT / "data" / "projections.json").read_text(encoding="utf-8")
    )
    four = log["rounds"].get("4")
    if four is None:
        pytest.skip("round 4 is no longer on file")
    by_name = {r["name"]: r for r in four["players"].values()}
    for name, right, wrong in [
        ("Santi García", -1, 5),
        ("Pavlidis", 7, 0),
        ("Héctor Hernández", 2, 7),
    ]:
        row = by_name.get(name)
        if row is None or row.get("actual") is None:
            continue
        assert row["actual"] != wrong, f"{name} carries his round 3 score"
        assert row["actual"] == right
