"""The Premium bulletin, copied each week, and what it now reaches.

On 18/09/2026 the transfer search proposed Santi García, who was on the Premium
injury bulletin, because only the squad's own absences ever reached the model:
they were typed by hand into data/indisponiveis.yaml. The bulletin lists the
whole league. These pin how a snapshot of it is read, matched and applied.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from liga_record_mcp.advice import transfer_candidates
from liga_record_mcp.source.base import SquadSourceError
from liga_record_mcp.source.bulletin import (
    bulletin_out,
    known_out,
    load_bulletin,
    read_day,
    stale_bulletin,
)
from liga_record_mcp.source.manual import load_back

ROOT = Path(__file__).resolve().parents[1]


def player(pid, name, club):
    return SimpleNamespace(id=pid, name=name, club=club)


def snapshot(round_number=7, injured=(), suspended=(), read="2026-09-18T10:15:00+01:00"):
    return {
        "lido_em": read,
        "jornada": round_number,
        "lesionados": [{"nome": n, "clube": c, "posicao": "Médio"} for n, c in injured],
        "castigados": [{"nome": n, "clube": c, "posicao": "Médio"} for n, c in suspended],
        "treinadores_castigados": [],
    }


def write(folder, name, doc):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


# --- reading the snapshots -------------------------------------------------------


def test_the_newest_snapshot_of_the_round_is_the_one_read(tmp_path):
    write(tmp_path, "2026-09-15.json", snapshot(7, injured=[("Early", "Arouca")]))
    write(tmp_path, "2026-09-18.json", snapshot(7, injured=[("Late", "Arouca")]))
    write(tmp_path, "2026-09-10.json", snapshot(6, injured=[("Old", "Arouca")]))
    got = load_bulletin(tmp_path, 7)
    assert [e["nome"] for e in got["lesionados"]] == ["Late"]


def test_a_snapshot_of_another_round_is_left_alone(tmp_path):
    """A man injured last week may be fit this one."""
    write(tmp_path, "2026-09-10.json", snapshot(6, injured=[("Old", "Arouca")]))
    assert load_bulletin(tmp_path, 7) is None
    assert load_bulletin(tmp_path / "missing", 7) is None


def test_a_file_that_is_not_a_snapshot_fails_loudly(tmp_path):
    write(tmp_path, "2026-09-18.json", {"jornada": 7})
    with pytest.raises(SquadSourceError, match="not a bulletin snapshot"):
        load_bulletin(tmp_path, 7)


def test_a_snapshot_not_named_by_its_date_is_refused(tmp_path):
    """The name is what orders them. Found by the code review of 18/09/2026."""
    write(tmp_path, "boletim (1).json", snapshot(7))
    with pytest.raises(SquadSourceError, match="YYYY-MM-DD"):
        load_bulletin(tmp_path, 7)


def test_the_day_it_was_read():
    assert read_day(snapshot()) == "18/09"
    assert read_day(None) is None


# --- matching it to the market -----------------------------------------------------


def test_matched_on_name_and_club_never_the_name_alone():
    """The bulletin of 18/09 listed the Samu of FC Porto; the squad holds another."""
    out = bulletin_out(
        snapshot(injured=[("Samu", "FC Porto")]),
        [player("ours", "Samu", "V. Guimarães"), player("theirs", "Samu", "FC Porto")],
    )
    assert set(out) == {"theirs"}


def test_accents_and_case_do_not_hide_a_player():
    out = bulletin_out(
        snapshot(injured=[("santi garcia", "gil vicente")]),
        [player("x", "Santi García", "Gil Vicente")],
    )
    assert out == {"x": "lesionado a 18/09"}


def test_a_suspension_reads_as_one():
    out = bulletin_out(
        snapshot(suspended=[("Bah", "Benfica")]), [player("b", "Bah", "Benfica")]
    )
    assert out == {"b": "castigado a 18/09"}


# --- the squad: the bulletin first, the hand file on top ---------------------------


def hand_file(tmp_path, text):
    path = tmp_path / "indisponiveis.yaml"
    path.write_text(text, encoding="utf-8")
    return path


ZAIDU = [player("41670", "Zaidu", "FC Porto")]


def test_the_bulletin_benches_a_squad_player_with_no_line_in_the_file(tmp_path):
    write(tmp_path / "boletim", "2026-09-18.json", snapshot(injured=[("Zaidu", "FC Porto")]))
    out = known_out(tmp_path / "none.yaml", tmp_path / "boletim", 7, ZAIDU)
    assert out == {"41670": "lesionado a 18/09"}


def test_a_line_in_the_file_wins_over_the_bulletin(tmp_path):
    write(tmp_path / "boletim", "2026-09-18.json", snapshot(injured=[("Zaidu", "FC Porto")]))
    path = hand_file(
        tmp_path,
        'jornada: 7\nfora:\n  - id: "41670"\n    nome: Zaidu\n    razao: ALERTA de sabado\n',
    )
    assert known_out(path, tmp_path / "boletim", 7, ZAIDU) == {"41670": "ALERTA de sabado"}


def test_aptos_puts_back_a_man_the_bulletin_still_lists(tmp_path):
    """The bulletin can sit unchanged for days; the lock-day ALERTA is fresher."""
    write(tmp_path / "boletim", "2026-09-18.json", snapshot(injured=[("Zaidu", "FC Porto")]))
    path = hand_file(tmp_path, 'jornada: 7\nfora: []\naptos:\n  - id: "41670"\n    nome: Zaidu\n')
    assert known_out(path, tmp_path / "boletim", 7, ZAIDU) == {}
    assert load_back(path, 7) == {"41670"}
    assert load_back(path, 8) == set()


# --- the transfer search -------------------------------------------------------------


def test_the_transfer_search_leaves_bulletin_players_out():
    view = {i: {"appearances": 40} for i in ("santi", "fit", "mine", "rookie")}
    view["rookie"]["appearances"] = 3
    got = transfer_candidates(
        ["santi", "fit", "mine", "rookie"], view, held={"mine"}, left_out={"santi", "mine"}
    )
    # Santi out for the bulletin, the rookie for his record; a man already held
    # stays whatever his state, because the search prices the squad with him.
    assert got == ["fit", "mine"]


def test_the_public_page_gives_the_count_and_the_private_one_the_names():
    spec = importlib.util.spec_from_file_location(
        "build_dashboard", ROOT / "scripts" / "build_dashboard.py"
    )
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    public = dash.left_out_note(["Santi García", "Bah"], "18/09", public=True)
    private = dash.left_out_note(["Santi García", "Bah"], "18/09", public=False)
    assert "2 jogadores" in public and "Santi" not in public
    assert "Santi García" in private and "Bah" in private
    assert dash.left_out_note([], "18/09", public=True) == ""


# --- the reminder ---------------------------------------------------------------------


def test_a_round_without_its_bulletin_is_reminded(tmp_path):
    assert "jornada 8" in stale_bulletin(tmp_path, 8)
    write(tmp_path, "2026-09-25.json", snapshot(8))
    assert stale_bulletin(tmp_path, 8) is None


# --- what the review of 18/09 added ------------------------------------------------


def test_the_reason_never_names_the_paid_source():
    """It reaches the public pages and the tracked ledger beside our own players."""
    out = bulletin_out(
        snapshot(injured=[("Zaidu", "FC Porto")], suspended=[("Bah", "Benfica")]),
        [player("z", "Zaidu", "FC Porto"), player("b", "Bah", "Benfica")],
    )
    for reason in out.values():
        assert "boletim" not in reason and "quadro" not in reason


def test_a_name_that_matches_nobody_is_reported():
    """Naming drift between the bulletin and the market would otherwise be silent."""
    from liga_record_mcp.source.bulletin import unmatched

    snap = snapshot(injured=[("Zaidu", "FC Porto"), ("Robinho", "Académico")])
    players = [player("z", "Zaidu", "FC Porto"), player("r", "Robinho", "Académico Viseu")]
    assert unmatched(snap, players) == ["Robinho (Académico)"]
    assert unmatched(None, players) == []


def test_the_squad_proposal_filters_the_whole_twenty_three_too():
    """Only the ladder was filtered; the twenty-three built from scratch were not."""
    source = (ROOT / "scripts" / "propose_squad.py").read_text(encoding="utf-8")
    assert "if p.id not in left_out" in source
    assert "candidates=[i for i in market if i not in left_out]" in source
