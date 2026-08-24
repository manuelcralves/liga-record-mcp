"""Recording a transfer, and the file it must not damage doing it.

data/squad.yaml is half comments: the position groups, a projection beside
every player, and the paragraph explaining why the team sheet lives there at
all. A round trip through a YAML writer deletes every one of them and leaves
the data correct — which looks exactly like it worked. So the edits are made in
the text, and these tests are what say the text survived them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SQUAD = '''# Manuel's squad. This header must survive every edit.
team:
  id: 1
  name: Melro

round: 3

players:
  # --- Guarda-redes (3) ---
  - id: "100"          # proj 3.9/jornada
    name: Diogo Costa
    position: GK
    club: FC Porto
    value: 5500000
    initial_value: 5500000
    points_total: 14
    points_round: 7

  # --- Defesas (8) ---
  - id: "200"          # proj 2.4/jornada
    name: Tiago Esgaio
    position: DEF
    club: Arouca
    value: 1000000
    initial_value: 1000000
    points_total: 4
    points_round: 6

  - id: "201"          # proj 3.7/jornada
    name: Nehuen Perez
    position: DEF
    club: FC Porto
    value: 2000000
    initial_value: 2000000
    points_total: 13
    points_round: 9

selection:
  starters:
    - "100"    # Diogo Costa      GR   FC Porto
    - "201"    # Nehuen Perez     DEF  FC Porto
  bench:
    - "200"    # Tiago Esgaio     DEF  Arouca
  captain: "201"    # Nehuen Perez
'''


class FakePlayer:
    def __init__(self, pid, name, position, club, value=1_000_000):
        self.id = pid
        self.name = name
        self.position = type("P", (), {"value": position})()
        self.club = club
        self.value = value
        self.initial_value = value
        self.points_total = 0
        self.points_round = 0


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "transferir", ROOT / "scripts" / "transferir.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- finding the right man ----------------------------------------------------


def test_a_name_is_matched_without_accents(mod):
    assert mod.fold("Vagiannídis") == mod.fold("vagiannidis")
    assert mod.fold("  Luis   Suárez ") == "luis suarez"


def test_a_surname_finds_the_player(mod):
    people = [FakePlayer("1", "Tiago Esgaio", "DEF", "Arouca")]
    assert mod.one_of(people, "Esgaio", "no plantel").id == "1"


def test_an_exact_name_beats_a_substring(mod):
    """"Silva" is half the league; typing it in full has to mean it."""
    people = [
        FakePlayer("1", "Silva", "DEF", "a"),
        FakePlayer("2", "Gabriel Silva", "MID", "b"),
    ]
    assert mod.one_of(people, "Silva", "no plantel").id == "1"


def test_an_ambiguous_name_refuses_rather_than_picks(mod):
    """Buying the wrong man is not undoable at one transfer a round."""
    people = [
        FakePlayer("1", "Gabriel Silva", "MID", "a"),
        FakePlayer("2", "Gustavo Silva", "FWD", "b"),
    ]
    with pytest.raises(SystemExit) as caught:
        mod.one_of(people, "Silva", "no mercado")
    assert "mais que um" in str(caught.value)
    assert "Gabriel Silva" in str(caught.value)


def test_an_unknown_name_refuses(mod):
    with pytest.raises(SystemExit):
        mod.one_of([FakePlayer("1", "Zaidu", "DEF", "a")], "Fulano", "no plantel")


# --- editing the file ---------------------------------------------------------


def test_the_header_and_group_comments_survive(mod):
    """The point of editing text instead of re-dumping YAML."""
    out = FakePlayer("200", "Tiago Esgaio", "DEF", "Arouca", 1_000_000)
    into = FakePlayer("300", "Vagiannidis", "DEF", "Sporting", 2_000_000)
    after = mod.swap_in_yaml(SQUAD, out, into)

    assert "# Manuel's squad. This header must survive every edit." in after
    assert "# --- Guarda-redes (3) ---" in after
    assert "# --- Defesas (8) ---" in after
    assert "# proj 3.9/jornada" in after, "an untouched player lost his projection"


def test_the_departing_player_is_gone_and_the_arrival_is_there(mod):
    out = FakePlayer("200", "Tiago Esgaio", "DEF", "Arouca")
    into = FakePlayer("300", "Vagiannidis", "DEF", "Sporting", 2_000_000)
    after = mod.swap_in_yaml(SQUAD, out, into)

    assert "Tiago Esgaio" not in after.split("selection:")[0]
    assert 'id: "300"' in after
    assert "value: 2000000" in after


def test_the_neighbours_are_untouched(mod):
    """A block that ends at the wrong place eats the player after it."""
    out = FakePlayer("200", "Tiago Esgaio", "DEF", "Arouca")
    into = FakePlayer("300", "Vagiannidis", "DEF", "Sporting")
    after = mod.swap_in_yaml(SQUAD, out, into)

    assert "Nehuen Perez" in after
    assert "Diogo Costa" in after
    assert after.count('- id: "') == SQUAD.count('- id: "')


def test_selling_someone_not_in_the_file_refuses(mod):
    with pytest.raises(SystemExit):
        mod.find_block(SQUAD, "999")


# --- the team sheet -----------------------------------------------------------


def test_the_arrival_takes_the_bench_place(mod):
    into = FakePlayer("300", "Vagiannidis", "DEF", "Sporting")
    after, where = mod.swap_in_selection(SQUAD, "200", into)
    assert where == "banco"
    assert '- "300"' in after
    assert '- "200"' not in after
    assert "Vagiannidis" in after


def test_the_arrival_takes_the_starting_place(mod):
    into = FakePlayer("300", "Bednarek", "DEF", "FC Porto")
    after, where = mod.swap_in_selection(SQUAD, "201", into)
    assert where == "titulares"


def test_a_player_who_was_not_named_leaves_the_sheet_alone(mod):
    """Legal, and needs no edit — the sheet holds fifteen of twenty-three."""
    text = SQUAD.replace('    - "200"    # Tiago Esgaio     DEF  Arouca\n', "")
    into = FakePlayer("300", "Vagiannidis", "DEF", "Sporting")
    after, where = mod.swap_in_selection(text, "200", into)
    assert where is None
    assert after == text


def test_the_captain_line_is_not_touched_by_a_squad_swap(mod):
    """Selling the captain is caught in main() with an instruction, not here."""
    into = FakePlayer("300", "Vagiannidis", "DEF", "Sporting")
    after, _ = mod.swap_in_selection(SQUAD, "200", into)
    assert 'captain: "201"' in after
