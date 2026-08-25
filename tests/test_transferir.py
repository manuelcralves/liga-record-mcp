"""Recording a transfer, and the file it must not damage doing it.

data/squad.yaml is half comments: the position groups, a projection beside
every player, and the paragraph explaining why the team sheet lives there at
all. A round trip through a YAML writer deletes every one of them and leaves
the data correct — which looks exactly like it worked. So the edits are made in
the text, and these tests are what say the text survived them.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from liga_record_mcp.source.decisions import empty_store

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


# --- the ledger's refusal has to come before the write ------------------------
#
# `SQUAD_PATH.write_text` used to run first, and when the ledger then refused
# the script printed "o squad.yaml foi escrito, mas o ledger recusou" and
# returned — leaving the squad file and the decisions ledger disagreeing about
# what Manuel did, which is the one thing this script exists to keep straight.
#
# Not hypothetical: the scheduled job records rounds through `settle_decision`,
# so a round is often already on file by the time a transfer is entered.
#
# Everything above this line tests a helper. These drive `main()`, because the
# defect was in the ORDER of two statements and no helper can see that.


def legal_squad() -> str:
    """A squad.yaml that passes §6.6 and §6.13, built rather than typed.

    The SQUAD constant above is three players — enough for the text helpers,
    which never look at the counts. `main()` loads through the real validator,
    so driving it needs a legal twenty-three: 3-8-8-4, eleven out in 4-4-2, and
    an armband on somebody who starts.
    """
    shape = [("GK", 3), ("DEF", 8), ("MID", 8), ("FWD", 4)]
    out = [
        "# Cabecalho que tem de sobreviver.",
        "team:",
        "  id: 1",
        "  name: Melro",
        "  bonus: 0",
        "  penalties: 0",
        "",
        "round: 3",
        "",
        "players:",
    ]
    ids: dict[str, list[str]] = {}
    n = 100
    for position, count in shape:
        out.append(f"  # --- {position} ({count}) ---")
        ids[position] = []
        for i in range(count):
            n += 1
            pid = str(n)
            ids[position].append(pid)
            out += [
                f'  - id: "{pid}"          # proj 2.0/jornada',
                f"    name: {position}{i} Silva",
                f"    position: {position}",
                "    club: Arouca",
                "    value: 1000000",
                "    initial_value: 1000000",
                "    points_total: 0",
                "    points_round: 0",
                "",
            ]
    # 4-4-2, and the keeper is not the captain so the armband can move.
    starters = ids["GK"][:1] + ids["DEF"][:4] + ids["MID"][:4] + ids["FWD"][:2]
    # §6.13 wants exactly four substitutes, one per line of the pitch — the rest
    # of the twenty-three sit outside the sheet entirely.
    bench = [ids["GK"][1], ids["DEF"][4], ids["MID"][4], ids["FWD"][2]]
    out.append("selection:")
    out.append("  starters:")
    out += [f'    - "{i}"' for i in starters]
    out.append("  bench:")
    out += [f'    - "{i}"' for i in bench]
    out.append(f'  captain: "{starters[1]}"    # o primeiro defesa')
    out.append('  coach: "1"    # §6.17 — sem treinador a equipa faz zero')
    out.append("")
    return chr(10).join(out)


LEGAL = legal_squad()

#: The ledger's own empty shape, taken from the module rather than typed —
#: it carries a format version and a hand-written literal drifts from it.
EMPTY = json.dumps(empty_store())


def drive(mod, tmp_path, monkeypatch, argv, *, decisions=EMPTY):
    """Run main() against throwaway files, and hand back the squad text after."""
    squad = tmp_path / "squad.yaml"
    squad.write_text(LEGAL, encoding="utf-8")
    ledger = tmp_path / "decisions.json"
    ledger.write_text(decisions, encoding="utf-8")

    monkeypatch.setattr(mod, "SQUAD_PATH", squad)
    monkeypatch.setattr(mod, "DECISIONS_PATH", ledger)
    monkeypatch.setattr("sys.argv", ["transferir.py", *argv])

    caught = None
    try:
        mod.main()
    except SystemExit as exc:  # Refused is a SystemExit
        caught = exc
    return caught, squad.read_text(encoding="utf-8"), ledger.read_text(encoding="utf-8")


ALREADY = json.dumps(
    {**empty_store(), "rounds": {"3": {"captain": "alguem", "note": "ja registada"}}}
)


def test_a_round_already_on_file_leaves_the_squad_untouched(mod, tmp_path, monkeypatch):
    """The defect, stated as the thing that must not happen."""
    caught, after, _ = drive(
        mod, tmp_path, monkeypatch, ["--capitao", "GK0 Silva"], decisions=ALREADY
    )
    assert caught is not None, "the ledger accepted a round it already had"
    assert after == LEGAL, (
        "squad.yaml was rewritten and then the ledger refused — the two files "
        "now disagree about who has the armband"
    )


def test_the_refusal_says_nothing_was_written(mod, tmp_path, monkeypatch):
    caught, _, _ = drive(
        mod, tmp_path, monkeypatch, ["--capitao", "GK0 Silva"], decisions=ALREADY
    )
    assert "nada foi escrito" in str(caught)
    assert "squad.yaml foi escrito" not in str(caught)


def test_the_ledger_is_not_written_either(mod, tmp_path, monkeypatch):
    _, _, ledger = drive(
        mod, tmp_path, monkeypatch, ["--capitao", "GK0 Silva"], decisions=ALREADY
    )
    assert "ja registada" in ledger, "the refused round was overwritten anyway"


def test_a_dry_run_reports_the_refusal_instead_of_promising_success(
    mod, tmp_path, monkeypatch
):
    """--ensaio used to print the whole plan and "nada foi escrito", because the
    ledger was never consulted on a dry run. It said the transfer would work."""
    caught, after, _ = drive(
        mod,
        tmp_path,
        monkeypatch,
        ["--capitao", "GK0 Silva", "--ensaio"],
        decisions=ALREADY,
    )
    assert caught is not None and "o ledger não aceita" in str(caught)
    assert after == LEGAL


def test_a_clean_round_writes_both(mod, tmp_path, monkeypatch):
    """The other side of it: nothing above may have broken the working case."""
    caught, after, ledger = drive(mod, tmp_path, monkeypatch, ["--capitao", "GK0 Silva"])
    assert caught is None, f"a legal armband change was refused: {caught}"
    assert 'captain: "101"' in after, "the squad file did not take the new captain"
    assert "GK0 Silva" in ledger, "the decision never reached the ledger"


def test_a_clean_dry_run_still_writes_nothing(mod, tmp_path, monkeypatch):
    caught, after, ledger = drive(
        mod, tmp_path, monkeypatch, ["--capitao", "GK0 Silva", "--ensaio"]
    )
    assert caught is None
    assert after == LEGAL
    assert ledger == EMPTY, "a dry run reached the ledger"


# --- the last player in the file ----------------------------------------------
#
# `find_block` used to end a player's entry at the next `  - id:`, the next
# `  # ---` group heading, or `selection:`, whichever came first. For every
# player but the last one some marker sits right after his entry and the span
# is correct. For the last one only `selection:` matches — and everything
# between him and it belongs to the span.
#
# In the real data/squad.yaml that is fourteen lines of comment: the paragraph
# saying the team sheet lives in this file at all, that the loader validates it
# before a page is drawn, and that §11.2 works down the bench in the order
# written. Selling him wrote eight lines back over twenty-two, and the result
# loaded, validated and saved — which is the worst way for this to be wrong.
#
# The old tests could not see it: in their fixture the last player is followed
# immediately by `selection:`, so the span was right by accident.

DOCUMENTED = LEGAL.replace(
    "selection:",
    """# A folha como foi entregue no site.
#
# ESTE É O ÚNICO SÍTIO ONDE ELA VIVE, e este parágrafo é o que se perdia.
#
# A ordem do `bench` conta: o §11.2 percorre-o por ordem quando substitui.

selection:""",
    1,
)


def last_id(text: str) -> str:
    import re

    return re.findall(r'  - id: "(\d+)"', text)[-1]


def test_the_last_players_block_is_his_entry_and_nothing_else(mod):
    """Eight lines: the id line and the seven under it."""
    start, end = mod.find_block(DOCUMENTED, last_id(DOCUMENTED))
    assert len(DOCUMENTED[start:end].splitlines()) == 8


def test_the_last_players_block_does_not_reach_the_comment_paragraph(mod):
    start, end = mod.find_block(DOCUMENTED, last_id(DOCUMENTED))
    swallowed = DOCUMENTED[start:end]
    assert "§11.2" not in swallowed, (
        "the block reaches past the entry into the documentation above "
        "`selection:` — selling this player deletes it"
    )
    assert "selection:" not in swallowed


def test_selling_the_last_player_leaves_the_paragraph_where_it_was(mod):
    """The whole point, stated as a round trip."""
    text = DOCUMENTED
    out = FakePlayer(last_id(text), "irrelevante", "FWD", "Arouca")
    arrival = FakePlayer("999", "Novo Homem", "FWD", "Rio Ave")
    after = mod.swap_in_yaml(text, out, arrival)

    assert "ESTE É O ÚNICO SÍTIO ONDE ELA VIVE" in after
    assert "§11.2 percorre-o por ordem" in after
    assert "Novo Homem" in after
    assert text.count("#") - after.count("#") <= 1, (
        "comments went missing — only the departing player's own `# proj` note "
        "may be replaced"
    )


def test_a_player_in_the_middle_still_ends_at_the_next_entry(mod):
    """The fix must not lengthen the ordinary case."""
    import re

    first = re.findall(r'  - id: "(\d+)"', DOCUMENTED)[0]
    start, end = mod.find_block(DOCUMENTED, first)
    assert len(DOCUMENTED[start:end].splitlines()) == 8


def test_a_group_heading_is_not_eaten_either(mod):
    """The last player of a group is followed by a blank line and a `# ---`."""
    import re

    ids = re.findall(r'  - id: "(\d+)"', DOCUMENTED)
    last_keeper = ids[2]  # 3 GK, so this one is followed by the DEF heading
    start, end = mod.find_block(DOCUMENTED, last_keeper)
    assert "# ---" not in DOCUMENTED[start:end]
    assert len(DOCUMENTED[start:end].splitlines()) == 8


# --- and against the file it will actually run on -----------------------------


def test_every_entry_in_the_real_squad_is_eight_lines(mod):
    """A fixture can be built to pass. This one is the file he owns."""
    import re

    real = (ROOT / "data" / "squad.yaml").read_text(encoding="utf-8")
    ids = re.findall(r'  - id: "(\d+)"', real)
    assert len(ids) == 23, "data/squad.yaml is not a full squad"
    wrong = {}
    for player_id in ids:
        start, end = mod.find_block(real, player_id)
        found = len(real[start:end].splitlines())
        if found != 8:
            wrong[player_id] = found
    assert not wrong, f"blocks of the wrong length in the real file: {wrong}"


def test_a_transfer_does_not_eat_the_blank_line_between_entries(mod):
    """Wider than the finding, and quieter.

    The old span ran to the newline BEFORE the next marker, so it took the
    blank line separating one entry from the next: twenty-two of the real
    twenty-three came back nine lines long against a `player_block` that writes
    eight. Every transfer deleted one blank line — 264 lines in, 263 out, the
    arrival butted straight up against the man below him — and over a season of
    transfers the squad file loses its spacing a line at a time, with the data
    correct throughout.

    Only the last player lost a paragraph. All the others were losing the shape
    of the file.
    """
    real = (ROOT / "data" / "squad.yaml").read_text(encoding="utf-8")
    import re

    middle = re.findall(r'  - id: "(\d+)"', real)[5]
    out = FakePlayer(middle, "irrelevante", "DEF", "Arouca")
    arrival = FakePlayer("999", "Novo Homem", "DEF", "Rio Ave")
    after = mod.swap_in_yaml(real, out, arrival)

    assert len(after.splitlines()) == len(real.splitlines()), (
        "the file changed length on a like-for-like swap — a blank line went "
        "missing"
    )
    entry = after[after.index('  - id: "999"') :]
    assert entry.splitlines()[8].strip() == "", (
        "the arrival is touching the next entry; the separating blank line was "
        "eaten with his block"
    )
