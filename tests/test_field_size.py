"""The national field is counted in the page size it was asked in.

WHAT HAPPENED. `settle_decision` and `holiday_round` asked the ranking for
pages of five and multiplied the page count by twenty. The site honours
`pagesize` — on 22/11/2026 its 40 589 teams came back as 8118 pages of 5 and
2030 of 20 — so from 21/08 both filed a field four times its size: 162 360
teams where there were 40 589, a best round in the top 1.5% that was the top
6.2%, and a page saying the holiday pays half of a maximum over 162 360 teams.

WHY NO TEST CAUGHT IT. Every fake ranking in the suite returned the same page
count whatever page size it was asked for, which is the one thing the real
site does not do. This one pages the way the site does, and remembers the page
size it was asked for, so the check holds for any page size the server picks.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from helpers import make_squad, squad_document, write_squad_file

from liga_record_mcp import server as mcp_server
from liga_record_mcp.models import Squad, TeamStanding

#: The national ranking's size on 22/11/2026, read off its last page.
FIELD = 40_589


class Ranking:
    """A ranking that pages like the site: ceil(teams / page size) pages."""

    def __init__(self, team_id: int):
        self.team_id = team_id
        #: The page size of every query that filters nothing — the only kind
        #: whose page count says how big the field is.
        self.unfiltered_page_sizes: list[int] = []

    def standings(
        self,
        *,
        round_number=None,
        team="",
        page_size=20,
        order="total",
        page=1,
        league_guid=None,
    ):
        if team:
            row = TeamStanding(
                team_id=self.team_id,
                team_name=team,
                user_name="",
                points_total=71,
                points_round=71,
                position=2497,
                position_round=2497,
            )
            return [row], 1
        self.unfiltered_page_sizes.append(page_size)
        leaders = [
            TeamStanding(
                team_id=900_000 + i,
                team_name=f"Leader {i}",
                user_name="",
                points_total=90 - i,
                points_round=90 - i,
                position=i + 1,
                position_round=i + 1,
            )
            for i in range(page_size)
        ]
        return leaders, math.ceil(FIELD / page_size)

    def fixtures(self):
        return []

    def search(self, position, **kwargs):
        return []


def counted(size: int) -> int:
    """What pages of `size` say about the field: an upper bound within a page."""
    return math.ceil(FIELD / size) * size


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, squad: Squad):
    """The server pointed at a throwaway squad, an empty ledger and the ranking."""
    path = write_squad_file(
        tmp_path / "squad.yaml", squad_document(make_squad(squad.players), round=8)
    )
    decisions = tmp_path / "decisions.json"
    monkeypatch.setattr(mcp_server, "SQUAD_PATH", path)
    monkeypatch.setattr(mcp_server, "DECISIONS_PATH", decisions)
    ranking = Ranking(mcp_server._load().squad.team_id)
    monkeypatch.setattr(mcp_server, "_market", ranking)
    return ranking, decisions


def test_the_decision_ledger_files_the_field_in_the_page_size_it_asked_for(site):
    ranking, decisions = site
    mcp_server.settle_decision(6, transfer_out="Diogo Calila", transfer_in="Weverson")

    filed = json.loads(decisions.read_text(encoding="utf-8"))["rounds"]["6"]
    [size] = ranking.unfiltered_page_sizes
    assert filed["field_size"] == counted(size), "the page count was read in another size"
    assert FIELD <= filed["field_size"] < FIELD + size
    assert filed["our_rank"] == 2497


def test_the_holiday_counts_the_field_in_the_page_size_it_asked_for(site):
    ranking, _ = site
    answer = mcp_server.holiday_round(1)

    [size] = ranking.unfiltered_page_sizes
    assert answer["teams_ranked"] == counted(size), "the page count was read in another size"
    assert FIELD <= answer["teams_ranked"] < FIELD + size
    assert answer["round_winner"] == 90
