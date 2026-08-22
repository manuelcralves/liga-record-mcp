"""The team sheet Manuel entered lives in the squad file, not in a script.

It used to be three lists of bare player ids near the top of
build_dashboard.py. Changing a team sheet meant editing Python, and until
somebody remembered, the page went on presenting last week's eleven as this
week's — with every number beside it correct, which is the worst way for a page
to be wrong.

Moving it into data/squad.yaml buys two things these tests defend: the loader
validates it against §6.13 before a page is drawn, so an illegal formation
refuses instead of rendering; and it is data, so it can be changed without
touching code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from liga_record_mcp.rules import validate_selection
from liga_record_mcp.source import ManualSquadSource

ROOT = Path(__file__).resolve().parents[1]
SQUAD = ROOT / "data" / "squad.yaml"
DASHBOARD = ROOT / "scripts" / "build_dashboard.py"


@pytest.fixture(scope="module")
def snapshot():
    return ManualSquadSource(SQUAD).load()


def test_the_squad_file_carries_a_sheet(snapshot):
    """Without it the comparison has nothing to compare against."""
    assert snapshot.selection is not None, (
        "data/squad.yaml has no `selection:` block — the page cannot show what "
        "was entered, only what the model would enter"
    )


def test_the_sheet_is_legal(snapshot):
    """§6.13. The loader checks this too; this says so out loud."""
    sheet = validate_selection(snapshot.squad, snapshot.selection)
    assert sheet.is_valid, [v.rule for v in sheet.violations]


def test_everyone_named_is_actually_in_the_squad(snapshot):
    owned = {p.id for p in snapshot.squad.players}
    picked = set(snapshot.selection.starters) | set(snapshot.selection.bench)
    assert picked <= owned, sorted(picked - owned)
    assert snapshot.selection.captain in owned


def test_the_captain_is_starting(snapshot):
    """§10.3(l) doubles a man who plays. A captain on the bench doubles -1."""
    assert snapshot.selection.captain in set(snapshot.selection.starters)


def test_the_bench_is_ordered_and_whole(snapshot):
    """§11.2 works down the bench in the order given, so it is a sequence."""
    bench = list(snapshot.selection.bench)
    assert len(bench) == 4
    assert len(set(bench)) == 4, "a substitute named twice"
    assert not set(bench) & set(snapshot.selection.starters)


def test_the_sheet_is_not_hardcoded_in_the_dashboard_again():
    """The regression this whole file exists for.

    A run of bare numeric ids assigned at module level is how it looked before.
    If it comes back, the squad file stops being the one place the sheet lives
    and the two can disagree silently.
    """
    source = DASHBOARD.read_text(encoding="utf-8")
    guilty = re.findall(
        r'^(XI|BENCH|CAPTAIN)\s*=\s*["\'][\d\s]+["\']', source, re.MULTILINE
    )
    assert not guilty, (
        f"{guilty} is hardcoded in build_dashboard.py again — read it from "
        "data/squad.yaml, which the loader validates"
    )


def test_the_coach_named_in_the_sheet_exists():
    raw = yaml.safe_load(SQUAD.read_text(encoding="utf-8"))
    named = (raw.get("selection") or {}).get("coach")
    if named is None:
        pytest.skip("no coach on the sheet")
    coaches = yaml.safe_load(
        (ROOT / "data" / "coaches.yaml").read_text(encoding="utf-8")
    )["coaches"]
    assert named in {str(c["id"]) for c in coaches}
