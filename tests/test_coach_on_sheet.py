"""The coach on the ledger is the one on the sheet, and changing him re-records.

Until 18/09/2026 `record_projection` carried the coach as a constant, Farioli's
id, and round 7 went on file with him while the sheet had Rui Borges — a switch
worth about a point and a half that round, because FC Porto hosted Benfica and
Sporting hosted Arouca. The page read its formation from a constant too, and
called a 4-3-3 a 3-4-3.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.advice import ESTIMATOR
from liga_record_mcp.coaches import rank_coaches, round_strengths
from liga_record_mcp.stats import COACH_BEYOND_POINTS
from liga_record_mcp.models import ClubRecord, Fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location(
        "record_projection", ROOT / "scripts" / "record_projection.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stored(coach_id="890", out=()):
    return {
        "players": {
            "a": {"unavailable": "lesionado"} if "a" in out else {},
            "b": {},
        },
        "coach": {"id": coach_id} if coach_id else None,
    }


# --- which coach the sheet names ---------------------------------------------------


def test_the_coach_is_read_off_the_sheet(ledger):
    sheet = SimpleNamespace(selection=SimpleNamespace(coach_id="860"))
    assert ledger.sheet_coach(sheet) == "860"


def test_a_sheet_without_a_coach_names_none(ledger):
    assert ledger.sheet_coach(SimpleNamespace(selection=None)) is None
    assert ledger.sheet_coach(SimpleNamespace(selection=SimpleNamespace(coach_id=""))) is None


def test_no_coach_on_the_sheet_records_no_coach(ledger):
    """§6.17 scores that round zero; inventing a coach would be a false record."""
    assert ledger.coach_snapshot(None, [], 7, None) is None


# --- priced on the round's match (22/09/2026) ----------------------------------


ROUND_7 = [
    Fixture(round_number=7, home="Sporting", away="Arouca"),
    Fixture(round_number=7, home="FC Porto", away="Benfica"),
]


@pytest.fixture
def filed_coaches(ledger, tmp_path, monkeypatch):
    """A coach file of eighteen — the loader refuses a partial copy (§6.15) —
    with round 7's four clubs and one, Estoril, that has no match."""
    entries = [
        ("890", "Farioli", "FC Porto"),
        ("860", "Rui Borges", "Sporting"),
        ("777", "Vasco Seabra", "Arouca"),
        ("800", "Marco Silva", "Benfica"),
        ("900", "Vasco Matos", "Estoril"),
    ]
    entries += [(str(1000 + n), f"Coach {n}", f"Club {n}") for n in range(13)]
    lines = ["coaches:"]
    for coach_id, name, club in entries:
        lines += [
            f'  - id: "{coach_id}"',
            f"    name: {name}",
            f"    club: {club}",
            "    points_total: 5",
            "    points_round: 1",
        ]
    path = tmp_path / "coaches.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(ledger, "COACHES_PATH", path)
    return [{"id": i, "name": n, "club": c} for i, n, c in entries]


def archive():
    """Three strong clubs and a weak one, in goals a match over two seasons."""
    def club(name, scored, conceded):
        return ClubRecord(club=name, matches=68, goals_for=scored, goals_against=conceded)

    records = {
        "Sporting": club("Sporting", 170, 50),
        "FC Porto": club("FC Porto", 140, 55),
        "Benfica": club("Benfica", 150, 50),
        "Arouca": club("Arouca", 75, 110),
    }
    return SimpleNamespace(club_records=lambda: records)


def test_the_ledger_prices_the_coach_with_the_pages_own_function(ledger, filed_coaches):
    """One price, two readers: the ledger files what the page shows."""
    history = archive()
    got = ledger.coach_snapshot(history, ROUND_7, 7, "860")
    page = rank_coaches(
        filed_coaches, ROUND_7, round_strengths(history.club_records(), ROUND_7), 7
    )
    same = next(r for r in page if r["id"] == "860")
    assert (got["id"], got["name"], got["club"]) == ("860", "Rui Borges", "Sporting")
    assert got["projected_rate"] == round(same["expected"], 2)
    assert (got["opponent"], got["at_home"], got["metodo"]) == ("Arouca", True, "jogo")
    # The method did not change on 23/09/2026 but the number did, from the
    # players' 2.59 to the coaches' own, so the round says which it was filed
    # at. Rounds filed before then carry none, and were filed at 2.59.
    assert got["alem_do_resultado"] == COACH_BEYOND_POINTS
    assert got["projected_rate"] > COACH_BEYOND_POINTS - 3, "the mark is inside the price"
    assert got["points_before"] == 5, "the settle's fallback needs the total he started from"


def test_round_7_by_rule_sporting_at_home_to_arouca_over_the_derby(ledger, filed_coaches):
    history = archive()
    sporting = ledger.coach_snapshot(history, ROUND_7, 7, "860")["projected_rate"]
    porto = ledger.coach_snapshot(history, ROUND_7, 7, "890")["projected_rate"]
    assert sporting > porto
    advised = ledger.advised_coach(history, ROUND_7, 7)
    assert advised["club"] == "Sporting"


def test_a_coach_whose_club_has_no_match_is_projected_at_nothing(ledger, filed_coaches):
    got = ledger.coach_snapshot(archive(), ROUND_7, 7, "900")
    assert got["projected_rate"] == 0.0 and got["opponent"] is None


def test_the_models_coach_is_settled_from_the_email_by_his_club(ledger):
    stored = {"advised": {"coach": {"name": "Marco Silva", "club": "Benfica",
                                    "projected_rate": 3.9, "actual": None}}}
    official = {"treinadores": {"Marco Silva|Benfica": 6, "Farioli|FC Porto": 1}}
    played = {"Benfica", "FC Porto"}
    assert ledger.settle_advised_coach(stored, official, played) is True
    coach = stored["advised"]["coach"]
    assert coach["actual"] == 6 and coach["error"] == pytest.approx(2.1)
    # Settled once; a second pass writes nothing.
    assert ledger.settle_advised_coach(stored, official, played) is False


def test_no_email_yet_leaves_the_models_coach_open(ledger):
    stored = {"advised": {"coach": {"name": "X", "club": "Benfica",
                                    "projected_rate": 3.0, "actual": None}}}
    assert ledger.settle_advised_coach(stored, None, {"Benfica"}) is False
    assert stored["advised"]["coach"]["actual"] is None


def test_a_postponed_clubs_coach_is_not_closed_at_the_emails_zero(ledger):
    """The email lists a postponed club's coach at 0, which means nothing
    assigned yet — as its players' zeros do. Found by the code review."""
    stored = {"advised": {"coach": {"name": "Marco Silva", "club": "Benfica",
                                    "projected_rate": 3.9, "actual": None}}}
    official = {"treinadores": {"Marco Silva|Benfica": 0}, "adiados": ["Benfica"]}
    assert ledger.settle_advised_coach(stored, official, {"FC Porto"}) is False
    assert stored["advised"]["coach"]["actual"] is None


def test_the_players_estimator_did_not_move_with_the_coach():
    """The coach's method is filed on the coach (`metodo`). The estimator names
    the players' projections, and the track record sums only its own rounds —
    moving it for a coach would have thrown away that record for nothing."""
    assert ESTIMATOR == "valuation+fixture+recency"


# --- what makes the round on file stale ---------------------------------------------


def test_nothing_moved_is_not_a_reason_to_record_again(ledger):
    assert not ledger.sheet_moved(stored(), {"a", "b"}, set(), "890")


def test_a_new_coach_records_the_round_again(ledger):
    assert ledger.sheet_moved(stored("890"), {"a", "b"}, set(), "860")


def test_a_round_on_file_without_a_coach_takes_one(ledger):
    assert ledger.sheet_moved(stored(None), {"a", "b"}, set(), "860")


def test_the_squad_and_the_injury_list_still_count(ledger):
    assert ledger.sheet_moved(stored(), {"a", "c"}, set(), "890")
    assert ledger.sheet_moved(stored(), {"a", "b"}, {"a"}, "890")
    assert not ledger.sheet_moved(stored(out=("a",)), {"a", "b"}, {"a"}, "890")


# --- the constants that are gone ------------------------------------------------------


def test_no_coach_is_hard_coded_any_more(ledger):
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "CHOSEN_COACH" not in source


@pytest.fixture(scope="module")
def dash():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def eleven(shape):
    positions = ["GK"] + ["DEF"] * shape[0] + ["MID"] * shape[1] + ["FWD"] * shape[2]
    return {str(n): {"position": pos} for n, pos in enumerate(positions)}


def test_the_track_record_judges_the_coaches_apart_from_the_elevens(dash):
    rounds = [
        {"coach_model": 6, "coach_mine": 1},
        {"coach_model": 7, "coach_mine": 7},
        # Filed before the model's coach was: out of the sum, not zero.
        {"coach_model": None, "coach_mine": 10},
    ]
    said = dash.coach_verdict(rounds)
    assert "2 jornadas" in said and "13" in said and "8" in said and "+5" in said
    assert dash.coach_verdict([{"coach_model": None, "coach_mine": 4}]) == ""


def test_the_formation_is_counted_from_the_eleven(dash):
    """Found by the code review: the first test only read the source text."""
    four_three_three = eleven((4, 3, 3))
    assert dash.formation_of(list(four_three_three), four_three_three) == "4-3-3"
    three_four_three = eleven((3, 4, 3))
    assert dash.formation_of(list(three_four_three), three_four_three) == "3-4-3"


def test_the_page_prints_the_counted_formation():
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "<dd>3-4-3</dd>" not in source
    assert "<dd>{formation}</dd>" in source
    assert "formation = formation_of(XI, rows)" in source
    assert "o Benfica a 9" not in source
