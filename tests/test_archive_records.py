"""Which reconstructions `archive_records` reads.

A replay of 2025/26 must remember 2024/25 and nothing else, and the folder it
reads from also holds 2025/26 itself.
"""

from __future__ import annotations

import json

from liga_record_mcp.source.last_season import ARCHIVES, archive_records


def write_season(path, player_id, rows):
    path.write_text(
        json.dumps({"players": {player_id: {"matches": rows}}}), encoding="utf-8"
    )


def test_the_live_paths_read_every_reconstruction(tmp_path):
    newest, older = ARCHIVES
    write_season(tmp_path / newest, "x", [{"round": 1, "used": True, "points": 5}])
    write_season(tmp_path / older, "x", [{"round": 1, "used": False, "points": -1}])
    assert archive_records(tmp_path)["x"] == {
        "played": 1,
        "points": 5.0,
        "available": 2,
        "each": [5.0],
    }


def test_names_reads_only_the_seasons_asked_for(tmp_path):
    newest, older = ARCHIVES
    write_season(tmp_path / newest, "x", [{"round": 1, "used": True, "points": 5}])
    write_season(tmp_path / older, "x", [{"round": 1, "used": False, "points": -1}])
    assert archive_records(tmp_path, names=(older,))["x"] == {
        "played": 0,
        "points": 0.0,
        "available": 1,
        "each": [],
    }
