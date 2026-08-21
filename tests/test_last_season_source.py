"""Reading the reconstructed season off disk, and refusing to fake it.

The file this reads is not published by anyone: it is what this project worked
out last season must have been worth. That makes two failures worth guarding.
Serving it when it is not there would invent a season, and serving a stale copy
after a fresh sweep would quietly answer with the old one.
"""

from __future__ import annotations

import json
import os

import pytest

from liga_record_mcp.source import LastSeasonError, LastSeasonSource


def write(path, players):
    path.write_text(
        json.dumps({"season": 155, "players": players}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_a_missing_reconstruction_says_how_to_build_one(tmp_path):
    source = LastSeasonSource(tmp_path / "absent.json")
    assert source.available is False
    with pytest.raises(LastSeasonError) as raised:
        source.load()
    assert "build_last_season" in str(raised.value)


def test_a_file_that_is_not_a_reconstruction_is_refused(tmp_path):
    """Pointed at the wrong JSON it would otherwise return an empty season,
    which reads as "nobody played" rather than as a mistake."""
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"teams": {}}), encoding="utf-8")
    with pytest.raises(LastSeasonError):
        LastSeasonSource(wrong).load()


def test_unreadable_json_is_reported_not_swallowed(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(LastSeasonError):
        LastSeasonSource(broken).load()


def test_a_fresh_sweep_is_picked_up_without_a_restart(tmp_path):
    """A sweep takes ten minutes and finishes while the server is up. Caching
    for the life of the process would serve the half-built season until
    somebody thought to restart, and nothing would look wrong."""
    path = write(tmp_path / "season.json", {"1": {"name": "Pavlidis", "matches": []}})
    source = LastSeasonSource(path)
    assert set(source.players()) == {"1"}

    write(path, {"1": {"name": "Pavlidis", "matches": []}, "2": {"name": "Aursnes", "matches": []}})
    os.utime(path, (0, 0))  # a different stamp, whatever the clock's resolution
    assert set(source.players()) == {"1", "2"}


def test_a_name_is_found_through_its_accents(tmp_path):
    """Typing "Bernardo Fernandes" for "Bernardo Fernándes" is the normal case,
    not the exception."""
    path = write(
        tmp_path / "season.json",
        {"1": {"name": "Vangelis Pavlidís", "matches": []}},
    )
    found = LastSeasonSource(path).find("pavlidis")
    assert [p["name"] for p in found] == ["Vangelis Pavlidís"]


def test_a_shared_surname_returns_both_rather_than_choosing(tmp_path):
    """Picking one would be picking the wrong one about half the time, and the
    caller is the only one who knows which was meant."""
    path = write(
        tmp_path / "season.json",
        {
            "1": {"name": "Vangelis Pavlidis", "matches": []},
            "2": {"name": "Giorgos Pavlidis", "matches": []},
        },
    )
    assert len(LastSeasonSource(path).find("Pavlidis")) == 2


def test_an_empty_query_matches_nobody_rather_than_everybody(tmp_path):
    path = write(tmp_path / "season.json", {"1": {"name": "Pavlidis", "matches": []}})
    assert LastSeasonSource(path).find("  ") == []
