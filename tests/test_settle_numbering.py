"""The decision ledger asks the ranking service in the service's own numbers.

WHAT WOULD HAVE HAPPENED. `pending_decisions` said to record matchdays 1 to 7,
and `settle_decision` passed that number straight to the standings service.
That service renumbered when the official phase began: its round 1 IS matchday
6. So `settle_decision(1)` would have filed 63 points — matchday 6's score —
under matchday 1, and `settle_decision(6)` would have filed no score at all and
said nothing about it.

WHY IT MATTERS MORE HERE THAN ANYWHERE. This file is the only record of what
the advice was worth. A projection ledger with a wrong number is a bad
measurement; a decision ledger with another round's score is a story.

The translation already existed twice — `record_history.site_round` and the
holiday rows on the page — and `models.round_of_matchday` is now the one copy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import make_squad, squad_document, write_squad_file

from liga_record_mcp import server as mcp_server
from liga_record_mcp.models import FIRST_SCORING_MATCHDAY, Squad, TeamStanding, round_of_matchday


class Ranking:
    """The site, which only knows its own round numbers.

    Its round 1 is matchday 6 and its round 2 is matchday 7, with the scores
    this team really made. Anything else is the empty answer the service gives
    for a round it does not have.
    """

    BY_SITE_ROUND = {1: (63, 4030), 2: (44, 7504)}

    def __init__(self, team_id: str):
        self.team_id = team_id
        self.asked: list[int | None] = []

    def standings(self, *, round_number=None, team="", page_size=20, order="total"):
        self.asked.append(round_number)
        found = self.BY_SITE_ROUND.get(round_number)
        if found is None:
            return [], 0
        points, rank = found
        row = TeamStanding(
            team_id=self.team_id,
            team_name=team or "Melro",
            user_name="",
            points_total=points,
            points_round=points,
            position=rank,
            position_round=rank,
        )
        return [row], 5

    def fixtures(self):
        return []

    def search(self, position, **kwargs):
        return []


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, squad: Squad):
    """The server pointed at a throwaway squad and an empty decision file."""
    path = write_squad_file(
        tmp_path / "squad.yaml", squad_document(make_squad(squad.players), round=8)
    )
    decisions = tmp_path / "decisions.json"
    monkeypatch.setattr(mcp_server, "SQUAD_PATH", path)
    monkeypatch.setattr(mcp_server, "DECISIONS_PATH", decisions)
    site = Ranking(mcp_server._load().squad.team_id)
    monkeypatch.setattr(mcp_server, "_market", site)
    return site, decisions


def filed(decisions: Path, round_number: int) -> dict:
    return json.loads(decisions.read_text(encoding="utf-8"))["rounds"][str(round_number)]


def test_the_matchday_is_translated_before_the_site_is_asked(ledger):
    site, decisions = ledger
    answer = mcp_server.settle_decision(
        6, transfer_out="Diogo Calila", transfer_in="Weverson"
    )

    assert site.asked == [1, 1], "matchday 6 is the service's round 1, asked twice"
    assert answer["recorded"]["our_points"] == 63
    assert filed(decisions, 6)["our_points"] == 63


def test_the_other_scored_matchday_lands_on_its_own_score(ledger):
    site, decisions = ledger
    mcp_server.settle_decision(7, transfer_out="Nehuén Pérez", transfer_in="Lagerbielke")
    assert site.asked == [2, 2]
    assert filed(decisions, 7)["our_points"] == 44


def test_a_matchday_before_the_official_phase_is_never_asked_for(ledger):
    """Matchday 5 has no round at the service. Asking anyway is what would have
    fetched matchday 10's score once the season got that far."""
    site, decisions = ledger
    answer = mcp_server.settle_decision(5, note="janela livre")

    assert site.asked == [], "nothing is asked for a matchday the service erased"
    assert answer["recorded"]["our_points"] is None
    assert filed(decisions, 5)["note"] == "janela livre"


def test_a_round_the_site_has_not_scored_yet_is_filed_without_a_score(ledger):
    """The round is played but the service has not published it. The decision
    is still worth writing down — it is the score that can be filled in later,
    not the decision."""
    site, decisions = ledger
    answer = mcp_server.settle_decision(8, transfer_out="Pedro Ferreira")

    assert site.asked == [3, 3], "matchday 8 is the service's round 3"
    assert answer["recorded"]["our_points"] is None
    assert filed(decisions, 8)["transfer"] == {"out": "Pedro Ferreira", "in": None}


def test_the_translation_is_the_one_the_rest_of_the_project_uses():
    """One definition, in `models`. `record_history.site_round` and the page's
    holiday rows both go through it, and this is the pair that was measured
    against the live site on 23/09/2026."""
    assert round_of_matchday(FIRST_SCORING_MATCHDAY) == 1
    assert round_of_matchday(6) == 1 and round_of_matchday(7) == 2
    assert round_of_matchday(5) is None
