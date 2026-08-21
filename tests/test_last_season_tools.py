"""The two tools that answer from a season nobody published.

Liga Record keeps no history, so every number these return was worked out here
rather than looked up. The risk is not that the arithmetic is wrong — it is
tested elsewhere — but that an answer built from it gets repeated as though
Record had said it. Most of what follows is about the tools being honest when
they cannot answer, and labelled when they can.
"""

from __future__ import annotations

import json

import pytest

from liga_record_mcp import server as mcp_server
from liga_record_mcp.source import LastSeasonSource


def season_file(tmp_path, players):
    path = tmp_path / "last-season.json"
    path.write_text(
        json.dumps({"season": 155, "players": players}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def match(round_number, points, **extra):
    base = dict(
        round=round_number,
        points=points,
        used=True,
        minutes=90,
        goals=0,
        mark_source="rating",
    )
    return {**base, **extra}


def player(name, position="FWD", club="Benfica", matches=None):
    return {
        "name": name,
        "club": club,
        "position": position,
        "matches": matches if matches is not None else [match(n, 4) for n in range(1, 35)],
    }


@pytest.fixture
def loaded(tmp_path, monkeypatch):
    def install(players):
        path = season_file(tmp_path, players)
        monkeypatch.setattr(mcp_server, "_last_season", LastSeasonSource(path))
        return path

    return install


# --------------------------------------------------------------------------


def test_without_a_sweep_the_tool_says_how_to_run_one(tmp_path, monkeypatch):
    """The reconstruction takes ten minutes and is not in the repository. An
    empty answer would read as "nobody played last season"."""
    monkeypatch.setattr(
        mcp_server, "_last_season", LastSeasonSource(tmp_path / "absent.json")
    )
    answer = mcp_server.last_season("Pavlidis")
    assert "build_last_season" in answer["detail"]
    assert "points" not in answer

    leaders = mcp_server.last_season_leaders()
    assert "build_last_season" in leaders["detail"]


def test_every_answer_carries_what_it_is_built_from(loaded):
    """Without this the numbers look like Liga Record's own, and they are not.
    The mark is estimated and §10.2 says Record will not discuss it."""
    loaded({"1": player("Vangelis Pavlidís")})
    answer = mcp_server.last_season("Pavlidis")
    assert "Reconstructed, not published" in answer["basis"]
    assert "estimated" in answer["basis"]
    assert "Reconstructed" in mcp_server.last_season_leaders(minimum_matches=1)["basis"]


def test_a_shared_surname_asks_rather_than_picks(loaded):
    """Choosing would be wrong about half the time, and silently."""
    loaded({"1": player("Vangelis Pavlidis"), "2": player("Giorgos Pavlidis")})
    answer = mcp_server.last_season("Pavlidis")
    assert answer["found"] == 2
    assert len(answer["candidates"]) == 2
    assert "points" not in answer


def test_a_name_nobody_has_is_said_plainly(loaded):
    loaded({"1": player("Pavlidis")})
    answer = mcp_server.last_season("Cristiano Ronaldo")
    assert answer["found"] == 0


def test_a_season_comes_back_with_its_shape_and_not_just_its_total(loaded):
    """The total is the least useful thing a season can say."""
    collapse = [match(n, 12) for n in range(1, 18)] + [match(n, 0) for n in range(18, 35)]
    loaded({"1": player("Faded", matches=collapse)})
    answer = mcp_server.last_season("Faded")
    assert answer["per_match"] == 6.0
    form = {row["round"]: row["form"] for row in answer["form"]}
    assert form[17] == 12.0
    assert form[34] == 0.0


def test_rounds_without_a_rating_are_counted_and_reported(loaded):
    """zerozero leaves some appearances unrated, and those matches lean on the
    average mark. How many is how much of the season is soft."""
    mixed = [match(n, 4) for n in range(1, 34)] + [
        match(34, 4, mark_source="assumed average")
    ]
    loaded({"1": player("Unrated", matches=mixed)})
    assert mcp_server.last_season("Unrated")["rounds_without_a_rating"] == 1


def test_the_matches_come_back_in_the_order_the_season_was_played(loaded):
    """zerozero lists a season newest first, and a form line running backwards
    through the year would never look wrong."""
    loaded({"1": player("Ordered", matches=[match(n, n) for n in range(34, 0, -1)])})
    rounds = [m["round"] for m in mcp_server.last_season("Ordered")["matches"]]
    assert rounds == sorted(rounds)


# --------------------------------------------------------------------------


def test_leaders_are_ranked_by_the_rate_a_manager_actually_pays_for(loaded):
    """Per round in the squad, not per appearance: a manager carries a player
    every round whether or not he takes the field."""
    loaded(
        {
            "1": player("Ever Present", matches=[match(n, 5) for n in range(1, 35)]),
            "2": player(
                "Half A Season",
                matches=[match(n, 12) for n in range(1, 18)]
                + [match(n, -1, used=False, minutes=0) for n in range(18, 35)],
            ),
        }
    )
    names = [row["name"] for row in mcp_server.last_season_leaders()["players"]]
    assert names[0] == "Half A Season"  # 5.5 a round beats 5.0
    assert set(names) == {"Ever Present", "Half A Season"}


def test_four_brilliant_afternoons_do_not_top_the_table(loaded):
    """Without a floor, a substitute with a good month outranks everyone."""
    loaded(
        {
            "1": player("Cameo", matches=[match(n, 20) for n in range(1, 5)]),
            "2": player("Ever Present", matches=[match(n, 5) for n in range(1, 35)]),
        }
    )
    names = [row["name"] for row in mcp_server.last_season_leaders()["players"]]
    assert names == ["Ever Present"]
    assert "Cameo" in [
        row["name"] for row in mcp_server.last_season_leaders(minimum_matches=4)["players"]
    ]


def test_a_position_that_does_not_exist_is_refused(loaded):
    """`AVA` is the obvious guess — it is what Liga Record shows on the site —
    and internally the position is `FWD`. Filtering to nothing would answer
    "no forwards were any good"."""
    loaded({"1": player("Pavlidis")})
    assert "unknown position" in mcp_server.last_season_leaders(position="AVA")["detail"]


def test_a_position_filter_keeps_only_that_position(loaded):
    loaded(
        {
            "1": player("A Forward", position="FWD"),
            "2": player("A Keeper", position="GK"),
        }
    )
    rows = mcp_server.last_season_leaders(position="gk")["players"]
    assert [row["name"] for row in rows] == ["A Keeper"]
