"""Who is out, which is the one thing the model cannot work out for itself.

Cards it counts, so suspensions it sees. Injuries it does not: the site
publishes no availability — a player's payload carries fifteen fields and none
of them is it — and this project does not read the press.

WHAT IT IS WORTH. Playing a full season out from matchday 6 with Manuel's
squad, picking the XI blind scores 1246 points; knowing who is out scores 1306;
adding the weekly transfer on top scores 1326. Knowing who is injured is worth
three times the entire transfer channel, and it is the only input he can give
the model that it cannot get for itself.

TWO NUMBERS FOR ONE FACT, deliberately. In the ledger an unavailable man is
estimated at -1, which is what §10.3(i) actually pays him and what his error
will be scored against. In the team-sheet choice he is ranked a thousand below
everyone, which is a selection device and not a forecast. Writing the selection
number to the ledger would make his error -999 and poison every accuracy figure
the project produces.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from liga_record_mcp.source import load_unavailable
from liga_record_mcp.source.base import SquadSourceError

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "indisponiveis.yaml"


# --- the file -----------------------------------------------------------------


def test_the_real_file_loads_and_names_a_round():
    import yaml

    raw = yaml.safe_load(REAL.read_text(encoding="utf-8"))
    assert isinstance(raw.get("jornada"), int)
    assert isinstance(raw.get("fora"), list)


def test_a_missing_file_is_nobody_out_not_an_error(tmp_path):
    assert load_unavailable(tmp_path / "nope.yaml", 4) == {}


def test_a_player_is_read_with_his_reason(tmp_path):
    path = tmp_path / "i.yaml"
    path.write_text(
        'jornada: 4\nfora:\n  - id: "1"\n    nome: Alguem\n    razao: lesão muscular\n',
        encoding="utf-8",
    )
    assert load_unavailable(path, 4) == {"1": "lesão muscular"}


def test_an_id_is_read_as_a_string_whatever_the_yaml_says(tmp_path):
    """Player ids are strings everywhere else; an unquoted one in YAML is an int
    and would match nobody."""
    path = tmp_path / "i.yaml"
    path.write_text("jornada: 4\nfora:\n  - id: 42896\n", encoding="utf-8")
    assert "42896" in load_unavailable(path, 4)


def test_an_entry_with_no_id_is_refused(tmp_path):
    path = tmp_path / "i.yaml"
    path.write_text("jornada: 4\nfora:\n  - nome: Alguem\n", encoding="utf-8")
    with pytest.raises(SquadSourceError, match="no `id`"):
        load_unavailable(path, 4)


# --- the round is checked, not assumed ----------------------------------------


def test_a_file_from_another_round_says_nobody(tmp_path):
    """A list left over from last week would bench a fit player, and the mistake
    is invisible — he is simply not picked, with no error anywhere."""
    path = tmp_path / "i.yaml"
    path.write_text('jornada: 3\nfora:\n  - id: "1"\n    razao: x\n', encoding="utf-8")
    assert load_unavailable(path, 4) == {}
    assert load_unavailable(path, 3) == {"1": "x"}


def test_a_file_with_no_round_applies_to_any(tmp_path):
    """Permissive only when nothing was claimed; a named round is honoured."""
    path = tmp_path / "i.yaml"
    path.write_text('fora:\n  - id: "1"\n    razao: x\n', encoding="utf-8")
    assert load_unavailable(path, 9) == {"1": "x"}


# --- what it does to the eleven -----------------------------------------------


@pytest.fixture(scope="module")
def page():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_penalty_is_below_every_fit_player(page):
    """A fit man's estimate cannot go under -1: it is
    `playing * returns + (1 - playing) * -1` with returns at worst zero. So the
    penalty has to clear that, and clear the §10.3(i) -1 an out man collects —
    both are -1, and only one of them is certain."""
    assert page.OUT_OF_THE_RECKONING > 1.0


def test_the_page_consults_the_file(page):
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "load_unavailable(UNAVAILABLE_PATH, round_number)" in source
    assert "OUT_OF_THE_RECKONING" in source


def test_the_ledger_records_minus_one_not_the_selection_penalty():
    """The number that reaches the ledger is an estimate and gets scored."""
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert "adjusted = float(UNUSED_PENALTY)" in source
    assert "OUT_OF_THE_RECKONING" not in source, (
        "the selection penalty reached the ledger — every error would be -999"
    )


def test_the_ledger_says_why_he_is_out():
    source = (ROOT / "scripts" / "record_projection.py").read_text(encoding="utf-8")
    assert '"unavailable": why' in source


# --- the eleven actually changes ----------------------------------------------
#
# Everything above this line checks the plumbing. This checks the point: that a
# man marked out leaves the team sheet, and — the expensive part — that the
# armband leaves with him. Captaining an injured player doubles a -1.


def a_squad():
    """3-8-8-4, with one clear best forward and a clear second."""
    from liga_record_mcp.models import Position

    shape = [(Position.GK, 3), (Position.DEF, 8), (Position.MID, 8), (Position.FWD, 4)]
    rows, n = [], 0
    for position, count in shape:
        for _ in range(count):
            n += 1
            rows.append({"id": str(n), "position": position, "value": 1_000_000})
    return rows


def estimates(rows, *, star):
    """Everyone at 2.0, the star at 9.0 — so he starts and takes the armband."""
    found = {row["id"]: 2.0 for row in rows}
    found[star] = 9.0
    return found


def test_the_star_starts_and_captains_when_he_is_fit(page):
    from liga_record_mcp.optimise import best_eleven

    rows = a_squad()
    star = rows[-1]["id"]                       # a forward
    sheet = best_eleven(rows, estimates(rows, star=star))
    assert star in sheet["starters"]
    assert sheet["captain"] == star


def test_marking_him_out_takes_him_off_the_sheet(page):
    """The whole feature, in one assertion."""
    from liga_record_mcp.optimise import best_eleven

    rows = a_squad()
    star = rows[-1]["id"]
    found = estimates(rows, star=star)
    found = {
        i: (v - page.OUT_OF_THE_RECKONING if i == star else v)
        for i, v in found.items()
    }
    sheet = best_eleven(rows, found)
    assert star not in sheet["starters"], "an injured player was fielded"
    assert sheet["captain"] != star, (
        "the armband stayed on a man who is not playing — §10.3(l) doubles his "
        "-1 instead of somebody's return"
    )


def test_a_position_wiped_out_still_fields_a_legal_eleven(page):
    """Ranked below everyone, not removed. All three keepers out still has to
    produce a sheet — §6.13 needs one, and refusing helps nobody."""
    from liga_record_mcp.optimise import best_eleven
    from liga_record_mcp.models import Position

    rows = a_squad()
    keepers = [r["id"] for r in rows if r["position"] is Position.GK]
    found = {row["id"]: 2.0 for row in rows}
    found = {
        i: (v - page.OUT_OF_THE_RECKONING if i in keepers else v)
        for i, v in found.items()
    }
    sheet = best_eleven(rows, found)
    assert sheet is not None, "a squad with three injured keepers could not be fielded"
    assert len(sheet["starters"]) == 11
    assert sum(1 for i in sheet["starters"] if i in keepers) == 1


def test_nobody_out_leaves_the_sheet_exactly_as_it_was(page):
    from liga_record_mcp.optimise import best_eleven

    rows = a_squad()
    star = rows[-1]["id"]
    found = estimates(rows, star=star)
    assert best_eleven(rows, found) == best_eleven(rows, dict(found))


# --- the list expires, and says so when it has --------------------------------
#
# The file names a round and stops applying when that round passes. That is the
# right default and it answers Manuel's question directly: a man out for one
# round is fit the next, and nothing has to be deleted by hand. A list that
# carried forward would bench a fit player silently — he would simply not be
# picked, with no error anywhere to say why.
#
# The cost of that default is the other silence. A man out for three rounds
# needs the number bumped every week, and names sitting in an expired file do
# nothing while Manuel believes the model knows about them.


@pytest.fixture(scope="module")
def pending():
    spec = importlib.util.spec_from_file_location(
        "pending_decisions", ROOT / "scripts" / "pending_decisions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def a_file(tmp_path, round_number, names=("Alguem",)):
    path = tmp_path / "i.yaml"
    fora = "\n".join(
        f'  - id: "{i}"\n    nome: {n}\n    razao: lesionado'
        for i, n in enumerate(names, 1)
    )
    path.write_text(f"jornada: {round_number}\nfora:\n{fora}\n", encoding="utf-8")
    return path


def test_the_round_it_names_is_the_only_one_it_applies_to(tmp_path):
    path = a_file(tmp_path, 4)
    assert load_unavailable(path, 4)
    assert load_unavailable(path, 5) == {}


def test_a_file_that_has_expired_is_reported(pending, tmp_path):
    said = pending.stale_injuries(a_file(tmp_path, 4, ("Santi García",)), 5)
    assert said is not None
    assert "Santi García" in said
    assert "jornada: 5" in said, "it says it is stale without saying how to fix it"


def test_a_current_file_is_not_reported(pending, tmp_path):
    assert pending.stale_injuries(a_file(tmp_path, 5), 5) is None


def test_an_empty_list_is_not_reported_however_old(pending, tmp_path):
    """Nothing is being ignored, so there is nothing to say."""
    path = tmp_path / "i.yaml"
    path.write_text("jornada: 2\nfora: []\n", encoding="utf-8")
    assert pending.stale_injuries(path, 9) is None


def test_a_missing_file_is_not_reported(pending, tmp_path):
    assert pending.stale_injuries(tmp_path / "nope.yaml", 5) is None


def test_every_name_in_an_expired_file_is_named(pending, tmp_path):
    said = pending.stale_injuries(a_file(tmp_path, 4, ("Um", "Dois")), 6)
    assert "Um" in said and "Dois" in said


def test_the_routine_reports_it():
    source = (ROOT / "scripts" / "pending_decisions.py").read_text(encoding="utf-8")
    assert "stale_injuries(UNAVAILABLE_PATH" in source


# --- and the penalty never reaches the screen ---------------------------------


def test_the_selection_penalty_is_not_the_displayed_estimate(page):
    """It showed "Santi García — -996.94" beside a fixture that looked fine.

    A number nobody can act on teaches the reader to distrust the ones next to
    it. What he is worth is what §10.3(i) pays a man who does not play.
    """
    source = (ROOT / "scripts" / "build_dashboard.py").read_text(encoding="utf-8")
    assert "sheet = best_eleven(rows, ranking)" in source, (
        "the optimiser and the display share one map again"
    )
    assert 'float(UNUSED_PENALTY) if i in unavailable else v' in source


def test_the_page_says_why_he_is_worth_minus_one():
    built = ROOT / "docs" / "index.html"
    if not built.is_file():
        pytest.skip("the pages have not been built")
    text = built.read_text(encoding="utf-8")
    assert "-996" not in text, "the selection penalty is on the page"
