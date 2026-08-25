"""Whether following the model would have paid — and the ways that can lie.

This is the only table on the site reporting what happened rather than arguing
about what should. That makes it the one where a lookahead would be most
convincing and least visible: score the model's eleven by picking it with the
results in hand and it beats him every week, forever, while being worthless.

So these tests are mostly about what the comparison is NOT allowed to know.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def squad_rows(projected: dict[str, float], actual: dict[str, int | None]):
    """Twenty-three under §6.6's quota: 3 GK, 8 DEF, 8 MID, 4 FWD."""
    plan = [("GK", 3), ("DEF", 8), ("MID", 8), ("FWD", 4)]
    rows, n = {}, 0
    for position, count in plan:
        for _ in range(count):
            n += 1
            key = str(n)
            rows[key] = {
                "name": f"p{n}",
                "position": position,
                "club": "c",
                "value": 1_000_000,
                "projected": projected.get(key, 1.0),
                "actual": actual.get(key, 0),
                "error": (
                    None
                    if actual.get(key, 0) is None
                    else actual.get(key, 0) - projected.get(key, 1.0)
                ),
            }
    return rows


def ledger(rows, filed=None) -> dict:
    return {
        "_all": {
            "5": {
                "players": rows,
                "filed": filed,
                "recorded_at": "2026-08-01T00:00:00+00:00",
            }
        },
        "players": rows,
    }


def test_a_partly_settled_round_reports_no_totals(dash):
    """Not even the ceiling, which is the one that would compute anyway.

    It picks from the players who have results, so it produces a number while
    the other two cannot. Printed beside two dashes it reads as the round's
    ceiling, when it is the best eleven among whoever happened to play.
    """
    actual = {str(i): (3 if i <= 15 else None) for i in range(1, 24)}
    rows = squad_rows({}, actual)
    found = dash.track_rounds({"stored": ledger(rows)})
    assert len(found) == 1
    row = found[0]
    assert row["whole"] is False
    assert row["model"] is None and row["mine"] is None and row["ceiling"] is None
    # The per-player figures still mean something and are still reported.
    assert row["error"] is not None
    assert row["settled"] == 15


def test_a_whole_round_scores_all_three(dash):
    rows = squad_rows({}, {str(i): 3 for i in range(1, 24)})
    row = dash.track_rounds({"stored": ledger(rows)})[0]
    assert row["whole"] is True
    assert row["model"] is not None
    assert row["ceiling"] is not None


def test_the_model_eleven_is_picked_on_projections_not_results(dash):
    """The lookahead that would make this table worthless.

    Player 1 is projected badly and scored brilliantly; player 2 the reverse.
    An eleven picked on projections holds player 2 and misses player 1, so the
    model's total must fall SHORT of the ceiling. If they came out equal, the
    picking had seen the results.
    """
    projected = {"1": 0.1, "2": 9.0}   # both goalkeepers
    actual = {str(i): 1 for i in range(1, 24)}
    actual["1"] = 30
    actual["2"] = 1
    rows = squad_rows(projected, actual)
    row = dash.track_rounds({"stored": ledger(rows)})[0]

    assert row["ceiling"] > row["model"], (
        "the model's eleven scored the ceiling — it was picked knowing results"
    )


def test_the_captain_counts_twice(dash):
    """§10.3(l) doubles him rather than replacing him."""
    rows = squad_rows({}, {str(i): 2 for i in range(1, 24)})
    filed = {
        "starters": [str(i) for i in [1, 4, 5, 6, 12, 13, 14, 15, 20, 21, 22]],
        "bench": ["2", "7", "16", "23"],
        "captain": "20",
    }
    row = dash.track_rounds({"stored": ledger(rows, filed)})[0]
    assert row["mine"] == 11 * 2 + 2, "eleven twos plus the captain again"


def test_his_sheet_is_read_from_the_round_it_was_filed_with(dash):
    """Not from today's squad file, which is a different round's team."""
    rows = squad_rows({}, {str(i): 1 for i in range(1, 24)})
    without = dash.track_rounds({"stored": ledger(rows, None)})[0]
    assert without["mine"] is None, "a round with no filed sheet cannot score one"


def test_a_round_with_nothing_settled_is_left_out(dash):
    rows = squad_rows({}, {str(i): None for i in range(1, 24)})
    assert dash.track_rounds({"stored": ledger(rows)}) == []


def test_the_recorder_stores_the_filed_sheet(dash):
    """Without it the ledger can never answer the question this table asks."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert '"filed"' in source, (
        "record_projection.py no longer snapshots the team sheet — the track "
        "record stops being reconstructible from that round on"
    )


# --- judging a decision by what was known when it was made ---------------------
#
# §6.13 shuts the team sheet fifteen minutes before a round's first match. From
# that moment the comparison on the front page stops being advice and becomes a
# verdict on a choice already made — and computing it fresh judges that choice
# with constants that have since moved. APPEARANCE_FLOOR went from 2.0 to 1.0
# after round 3 was filed, tripling the fixture adjustment, and the page began
# telling Manuel he should have captained Begraoui. The projections on record,
# written before kickoff, had Pavlidis at 9.19 against Begraoui's 5.39: the
# model of the day agreed with him.


def test_an_open_round_is_judged_on_todays_estimates(dash):
    """While the sheet is open this is advice, and advice uses what is known."""
    fresh = {"1": 5.0, "2": 3.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 1.0}}}
    assert dash.judged_on(fresh, stored, kicked_off=False) == fresh


def test_a_closed_round_is_judged_on_what_was_on_record(dash):
    fresh = {"1": 5.0, "2": 3.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 1.0}}}
    assert dash.judged_on(fresh, stored, kicked_off=True) == {"1": 9.0, "2": 1.0}


def test_an_incomplete_record_falls_back_rather_than_mixing(dash):
    """Half on one model and half on another is worse than either."""
    fresh = {"1": 5.0, "2": 3.0}
    stored = {"players": {"1": {"projected": 9.0}}}
    assert dash.judged_on(fresh, stored, kicked_off=True) == fresh


def test_a_round_with_no_record_at_all_falls_back(dash):
    fresh = {"1": 5.0}
    assert dash.judged_on(fresh, {}, kicked_off=True) == fresh


# --- the same size is not the same squad --------------------------------------
#
# The check was `len(on_record) == len(fresh)`, and a squad is always
# twenty-three — so after a transfer the two maps matched on count while one id
# differed, and the stored map was used for a squad that no longer existed.
#
# The sequence is ordinary, not contrived. Round N kicks off. Manuel runs
# `transferir.py` for round N+1, which ends by telling him to run the routine.
# The routine rebuilds round N, which has already kicked off, against a squad
# that now contains someone who was never in it. He is scored at
# UNUSED_PENALTY, so he can never be picked; and if he reaches `described()`
# the unguarded lookup raises KeyError and no page is written at all.
#
# Everything above this line has the stored map strictly SMALLER, which the
# count check happened to catch. None of it could see this.


def test_a_transfer_makes_the_record_the_wrong_squad(dash):
    """Same count, one id different — the case the old check waved through."""
    fresh = {"1": 5.0, "9": 4.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 1.0}}}
    assert dash.judged_on(fresh, stored, kicked_off=True) == fresh, (
        "the round was judged on a squad that has since changed — the arrival "
        "has no estimate and can never be picked"
    )


def test_a_record_larger_than_the_squad_also_falls_back(dash):
    """The other direction, which the count check also missed on its own terms."""
    fresh = {"1": 5.0}
    stored = {
        "players": {"1": {"projected": 9.0}, "2": {"projected": 1.0}},
    }
    assert dash.judged_on(fresh, stored, kicked_off=True) == fresh


def test_the_whole_squad_on_record_is_still_used(dash):
    """The fix must not throw away the case the function exists for."""
    fresh = {"1": 5.0, "2": 3.0, "3": 2.0}
    stored = {
        "players": {
            "1": {"projected": 9.0},
            "2": {"projected": 1.0},
            "3": {"projected": 4.0},
        }
    }
    assert dash.judged_on(fresh, stored, kicked_off=True) == {
        "1": 9.0,
        "2": 1.0,
        "3": 4.0,
    }


def test_a_player_on_record_without_a_projection_is_a_gap_not_a_match(dash):
    """A null `projected` drops the id, so the keys stop matching and it falls
    back — rather than silently judging a squad on a map missing one man."""
    fresh = {"1": 5.0, "2": 3.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": None}}}
    assert dash.judged_on(fresh, stored, kicked_off=True) == fresh


def test_rounds_from_a_different_estimator_are_marked_and_excluded(dash):
    """A total spanning a change of model measures the change, not the model.

    Rounds 1-3 were written by the folded `project()`, which is not what the
    pages advise with — it recorded Pavlidis at 9.19 on a two-round streak the
    split shrinks to about five. Their error is still worth seeing; averaging
    it in with later rounds is not.
    """
    rows = squad_rows({}, {str(i): 3 for i in range(1, 24)})
    old = dash.track_rounds({"stored": ledger(rows)})[0]
    assert old["estimator"] is None

    marked = ledger(rows)
    marked["_all"]["5"]["estimator"] = "valuation+fixture"
    new = dash.track_rounds({"stored": marked})[0]
    assert new["estimator"] == "valuation+fixture"


def test_the_recorder_marks_which_estimator_wrote_the_round(dash):
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert '"estimator"' in source, (
        "record_projection.py no longer marks the estimator — a later change of "
        "model would be averaged into the accuracy figures silently"
    )
    assert "valuation(" in source, (
        "the ledger has stopped recording the estimator the pages advise with"
    )


# --- the verdict, which had no test at all ------------------------------------
#
# `whole` filtered on `model is not None` and then the total for `mine` carried
# its own `if r["mine"] is not None`. So the two sums ran over different sets of
# rounds while `n` reported the model's count, and the page printed "sobre 4
# jornadas o modelo somou X, o teu somou Y" with Y drawn from three of them —
# and a difference computed between them.
#
# `record_projection` writes `filed: None` whenever squad.yaml has no selection
# block, which is exactly how a round ends up with a model eleven and no filed
# one. Nothing exotic: it is the state of any round recorded before the sheet
# was entered.


def track_row(number, *, model=50.0, mine=45.0, estimator="valuation+fixture", whole=True):
    return {
        "round": number,
        "settled": 23 if whole else 20,
        "of": 23,
        "whole": whole,
        "model": model,
        "mine": mine,
        "ceiling": 70.0,
        "error": 2.0,
        "bias": -0.5,
        "estimator": estimator,
    }


def verdict_of(dash, rounds):
    """The section, with whitespace flattened — the HTML wraps mid-sentence."""
    return " ".join(dash.track_section({"track": rounds}).split())


def test_both_totals_span_the_same_rounds(dash):
    """The defect: one round has no filed eleven, and only one side notices."""
    said = verdict_of(
        dash,
        [
            track_row(4, model=50.0, mine=45.0),
            track_row(5, model=60.0, mine=None),
            track_row(6, model=40.0, mine=38.0),
        ],
    )
    assert "Sobre 2 jornadas" in said, (
        "the verdict counted a round it could only score one side of"
    )
    assert "<strong>90</strong>" in said, "the model total included the unpaired round"
    assert "<strong>83</strong>" in said


def test_the_unpaired_round_is_named_rather_than_dropped_quietly(dash):
    said = verdict_of(
        dash, [track_row(4), track_row(5, mine=None), track_row(6)]
    )
    assert "Fora desta conta" in said
    assert "jornada 5" in said


def test_nothing_is_said_about_unpaired_rounds_when_there_are_none(dash):
    said = verdict_of(dash, [track_row(4), track_row(6)])
    assert "Fora desta conta" not in said


def test_the_difference_is_between_the_two_totals_shown(dash):
    said = verdict_of(dash, [track_row(4, model=50.0, mine=45.0)])
    assert "<strong>50</strong>" in said and "<strong>45</strong>" in said
    assert "<strong>+5</strong>" in said


def test_a_partly_settled_round_is_not_in_the_verdict(dash):
    """Its own comment says three dashes mean "not yet"; the total must agree."""
    said = verdict_of(
        dash, [track_row(4), track_row(5, whole=True), track_row(6, whole=False)]
    )
    assert "Sobre 2 jornadas" in said


def test_a_round_from_the_old_estimator_is_excluded(dash):
    """A total spanning a change of model measures the change, not the model."""
    said = verdict_of(dash, [track_row(4), track_row(3, estimator=None)])
    assert "Sobre 1 jornada" in said
    assert "modelo antigo" in said


def test_no_usable_round_says_so_instead_of_summing_nothing(dash):
    """The HTML wraps, so compare on normalised whitespace rather than raw."""
    said = " ".join(verdict_of(dash, [track_row(4, whole=False)]).split())
    assert "Nenhuma jornada está liquidada por inteiro" in said
    assert "o teu somou" not in said, "a verdict was printed with nothing in it"


def test_an_empty_track_is_not_an_error(dash):
    said = dash.track_section({"track": []})
    assert "Nenhuma jornada liquidada ainda" in said


def test_a_round_with_a_filed_eleven_but_no_model_one_is_also_unpaired(dash):
    """The other direction — symmetric, and it was not handled either."""
    said = verdict_of(dash, [track_row(4), track_row(5, model=None)])
    assert "Sobre 1 jornada" in said
    assert "Fora desta conta" in said


# --- §15.3 has to survive the swap --------------------------------------------
#
# `model_sheet` writes 0.0 for a man whose club has no fixture at all this
# round — it is reading today's calendar, and §15.3 scores those players
# nothing. `judged_on` then replaced the WHOLE map with the stored one, which
# was written before the game moved and still holds the estimate he had when he
# had an opponent. So the zero was undone for exactly the players it applies to.
#
# The round it fires on: eight of nine games played, so `kicked_off` is true,
# and the "model eleven" comes back full of men who cannot score, priced as
# though they play, printed beside Manuel's sheet as what he should have done.
#
# Latent while Record keeps a postponed game in its original round without a
# date — the club stays in the calendar and is never zeroed. It fires when a
# game is moved OUT of a round, which is the same trigger as the ledger's
# no-fixture case.


def test_a_zeroed_player_stays_zero_after_the_swap(dash):
    """The defect, stated as the thing that must not come back."""
    fresh = {"1": 5.0, "2": 0.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 7.5}}}
    got = dash.judged_on(fresh, stored, kicked_off=True, no_fixture={"2"})
    assert got == {"1": 9.0, "2": 0.0}, (
        "the record put an opponent back for a player whose game is not being "
        "played — he would be picked for the model eleven"
    )


def test_the_others_still_get_what_was_on_record(dash):
    """The zero must not cost everyone else the pre-kickoff estimate."""
    fresh = {"1": 5.0, "2": 0.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 7.5}}}
    got = dash.judged_on(fresh, stored, kicked_off=True, no_fixture={"2"})
    assert got["1"] == 9.0


def test_nobody_zeroed_leaves_the_record_untouched(dash):
    fresh = {"1": 5.0, "2": 3.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 1.0}}}
    assert dash.judged_on(fresh, stored, kicked_off=True) == {"1": 9.0, "2": 1.0}


def test_an_open_sheet_is_unaffected_by_the_zero_set(dash):
    """While the sheet is open the fresh map already carries the zero."""
    fresh = {"1": 5.0, "2": 0.0}
    stored = {"players": {"1": {"projected": 9.0}, "2": {"projected": 7.5}}}
    assert dash.judged_on(fresh, stored, kicked_off=False, no_fixture={"2"}) == fresh


def test_a_zeroed_player_cannot_be_picked_for_the_model_eleven(dash):
    """What the zero is FOR, checked through the thing that consumes it."""
    fresh = {str(i): 5.0 for i in range(1, 4)}
    fresh["2"] = 0.0
    stored = {"players": {i: {"projected": 20.0} for i in fresh}}
    got = dash.judged_on(fresh, stored, kicked_off=True, no_fixture={"2"})
    assert got["2"] < min(v for i, v in got.items() if i != "2"), (
        "the zeroed player is not the cheapest option, so the optimiser can "
        "still field him"
    )


def test_the_page_passes_the_zeroes_in(dash):
    """The wiring, which is where this kind of fix usually fails to land."""
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "no_fixture={i for i, week in fixture_of.items() if week is None}" in source
