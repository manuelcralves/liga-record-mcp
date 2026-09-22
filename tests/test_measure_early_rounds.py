"""The check that lets the rebuilt rounds 1-5 into the model, or keeps them out.

What it compares has to be right before its verdict means anything: a filled-in
absence is not an appearance, a club without a match is no row, and the error
is only taken where both sides say he played.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.models import Position

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location(
        "measure_early_rounds", ROOT / "scripts" / "measure_early_rounds.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def man(name, club="Benfica"):
    return SimpleNamespace(name=name, club=club, position=Position.MID)


REBUILT = {
    "a": {"matches": [{"round": 6, "used": True, "points": 6.0}]},
    "b": {"matches": [{"round": 6, "used": False, "absent": True, "points": -1.0}]},
    "c": {"matches": [{"round": 6, "used": True, "points": 2.0}]},
}
EMAIL = {
    6: {
        "ronda": 6,
        "jogadores": {"A|Benfica": 5, "B|Benfica": -1, "C|Arouca": -1, "D|Benfica": 3},
        "adiados": ["Arouca"],
    }
}


def test_rows_agree_disagree_and_carry_the_error_only_where_both_played(check):
    market = {"a": man("A"), "b": man("B"), "c": man("C", "Arouca"), "d": man("D")}
    rows = {r["name"]: r for r in check.against_emails(REBUILT, market, EMAIL)}
    # C's club had no match: no row.
    assert set(rows) == {"A", "B", "D"}
    assert rows["A"]["agree"] and rows["A"]["error"] == 1.0
    # A filled-in absence agrees with the email's -1 and carries no error.
    assert rows["B"]["agree"] and rows["B"]["error"] is None
    # D was never linked: a row that cannot agree, so no bar shrinks around him.
    assert not rows["D"]["linked"] and not rows["D"]["agree"]


def test_an_email_row_is_never_read_off_another_man_of_the_same_name(check):
    """Rounds 3-5 emailed the squad alone, and its only "Samu" was V.
    Guimarães's. Matched from the market's side, the FC Porto Samu took his
    three rounds; matched from the email's side, each row is its own man."""
    market = {"porto": man("Samu", "FC Porto"), "vitoria": man("Samu", "V. Guimarães")}
    assert check.owner("Samu|V. Guimarães", market) == "vitoria"
    # A man who moved keeps his old club in the older emails: a name the
    # market holds once is still him.
    assert check.owner("A|Arouca", {"a": man("A", "Benfica")}) == "a"
    # Two of the name and neither at that club: nobody is guessed.
    assert check.owner("Samu|Arouca", market) is None
    # Nor two of the name at the one club.
    twins = {"x": man("Pedro", "Benfica"), "y": man("Pedro", "Benfica")}
    assert check.owner("Pedro|Benfica", twins) is None


def test_a_row_of_a_man_who_left_the_market_is_kept_and_marked(check):
    doc = {6: {"ronda": 6, "jogadores": {"Gone|Benfica": 3}, "adiados": []}}
    (row,) = check.against_emails(REBUILT, {"a": man("A")}, doc)
    assert not row["on_market"] and not row["agree"]


def test_on_the_pitch_for_minus_one_is_marked_as_the_email_cannot_tell(check):
    """Alfonso Pastor, round 4: ninety minutes, -1 on the rebuild and -1 from
    Record, which the email reading takes for a man not used."""
    rebuilt = {"g": {"matches": [{"round": 6, "used": True, "points": -1.0}]}}
    doc = {6: {"ronda": 6, "jogadores": {"G|Benfica": -1}, "adiados": []}}
    (row,) = check.against_emails(rebuilt, {"g": man("G")}, doc)
    assert not row["agree"], "the bar as written counts it against the rebuild"
    assert row["blind"]
    assert check.summary([row])["blind"] == 1


def test_the_summary_counts_what_it_says(check):
    rows = [
        {"agree": True, "error": 1.0},
        {"agree": True, "error": -0.5},
        {"agree": False, "error": None},
    ]
    s = check.summary(rows)
    assert (s["rows"], s["agree"], s["scored"]) == (3, 2, 2)
    assert s["mean_error"] == pytest.approx(0.25)
    assert s["mae"] == pytest.approx(0.75)


def test_the_appearance_record_leaves_out_clubs_without_a_match_and_departures(check):
    statuses = {
        "a": "played",
        "b": "unused",
        "c": "no_match",
        "u": "played",  # on the market, never linked
        "z": "played",  # has left the market
    }
    rebuilt = {
        "a": {"matches": [{"round": 2, "used": True, "points": 3.0}]},
        "b": {"matches": [{"round": 2, "used": True, "points": 3.0}]},
        "c": {"matches": []},
    }
    market = {i: man(i.upper()) for i in ("a", "b", "c", "u")}
    rows = {
        r["id"]: r["agree"]
        for r in check.against_appearances(rebuilt, market, statuses, 2)
    }
    assert rows == {"a": True, "b": False, "u": False}
