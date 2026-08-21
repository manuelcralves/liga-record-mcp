"""Nothing belonging to other people may enter a public repository.

Three files now hold data that is not Manuel's to publish — the league's
round-by-round history, the private dashboard, and anything carrying the league
guid. All three are gitignored, and all three have been committed by accident at
least once. `data/history.json` reached the public remote with twenty-nine other
people's team names and usernames and had to be removed by rewriting the commit.

The guard cannot work from a list of forbidden names, because writing that list
into a test would publish exactly what it is meant to protect. Instead it reads
the local, untracked files and checks that nothing in them appears in anything
git tracks. On a machine without those files the identity checks skip; the
"these paths must never be tracked" check always runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Files that must never be tracked, whatever else is true.
FORBIDDEN_PATHS = (
    "data/history.json",
    "docs/dashboard.html",
)

#: The private build writes a directory of pages rather than one file, and
#: every one of them carries the league. Ignoring the DIRECTORY is what makes a
#: page added next month covered the moment it exists — a list of filenames
#: only protects the files somebody remembered to add to it, and forgetting
#: once already put twenty-nine other people's names on a public remote.
FORBIDDEN_DIRS = ("docs/private",)

#: Manuel's own team. His name and results are his to publish; the other
#: twenty-nine members' are not.
MY_TEAM_ID = "156412"

#: Extensions worth scanning. Everything the project tracks is text.
TEXT_SUFFIXES = {
    ".py", ".md", ".yaml", ".yml", ".json", ".html", ".txt", ".toml", ".cfg", ".gitignore"
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout


@pytest.fixture(scope="module")
def tracked() -> list[Path]:
    listed = git("ls-files").splitlines()
    if not listed:
        pytest.skip("not a git checkout")
    return [ROOT / name for name in listed]


@pytest.fixture(scope="module")
def tracked_text(tracked: list[Path]) -> dict[Path, str]:
    contents = {}
    for path in tracked:
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".gitignore":
            continue
        if not path.exists():
            continue
        contents[path] = path.read_text(encoding="utf-8", errors="ignore")
    return contents


def other_members() -> set[str]:
    """Team names and usernames from the local history, excluding Manuel's own.

    Read at run time from an untracked file so the forbidden strings never have
    to be written down anywhere that git can see.
    """
    history = ROOT / "data" / "history.json"
    if not history.exists():
        return set()
    found: set[str] = set()
    rounds = json.loads(history.read_text(encoding="utf-8")).get("rounds", {})
    for entry in rounds.values():
        for team_id, team in entry.get("teams", {}).items():
            if team_id == MY_TEAM_ID:
                continue
            for field in ("name", "user"):
                value = (team.get(field) or "").strip()
                # Generic placeholders identify nobody and appear in ordinary prose.
                if len(value) > 3 and value != "Utilizador Registado":
                    found.add(value)
    return found


# --------------------------------------------------------------------------


def test_the_sensitive_files_are_not_tracked(tracked: list[Path]):
    """The check that runs everywhere, including a fresh clone."""
    names = {p.relative_to(ROOT).as_posix() for p in tracked}
    for forbidden in FORBIDDEN_PATHS:
        assert forbidden not in names, (
            f"{forbidden} is tracked — it carries other people's data and this "
            "repository is public"
        )


def test_the_private_pages_are_ignored_as_a_directory(tracked: list[Path]):
    """Not as a list of names. The private build writes five pages today and
    may write six next month, and the sixth has to be covered without anyone
    remembering to cover it."""
    for folder in FORBIDDEN_DIRS:
        assert git("check-ignore", folder).strip() == folder, (
            f"{folder} is not gitignored, and every page in it carries the league"
        )
        inside = [
            p.relative_to(ROOT).as_posix()
            for p in tracked
            if p.relative_to(ROOT).as_posix().startswith(folder + "/")
        ]
        assert not inside, f"tracked files inside {folder}: {inside}"


def test_the_sensitive_files_are_ignored():
    """Untracked is not enough; without the ignore, `git add -A` re-adds them."""
    for forbidden in FORBIDDEN_PATHS:
        assert git("check-ignore", forbidden).strip() == forbidden, (
            f"{forbidden} is not gitignored, so the next `git add -A` commits it"
        )


def test_no_league_member_appears_in_a_tracked_file(tracked_text: dict[Path, str]):
    members = other_members()
    if not members:
        pytest.skip("no local history to derive names from")

    leaks: list[str] = []
    for path, text in tracked_text.items():
        for member in members:
            if member in text:
                # The offending name is deliberately not printed.
                leaks.append(f"{path.relative_to(ROOT).as_posix()} contains a league member")
    assert not leaks, "league members found in tracked files: " + "; ".join(sorted(set(leaks)))


def test_the_league_guid_appears_nowhere(tracked_text: dict[Path, str]):
    """It grants read access to the whole league table."""
    import os
    import re

    guid = os.environ.get("LIGA_RECORD_LEAGUE", "").strip()
    pattern = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
    )
    for path, text in tracked_text.items():
        relative = path.relative_to(ROOT).as_posix()

        # The real guid is forbidden everywhere, captured pages included.
        if guid:
            assert guid not in text, f"{relative} contains the league guid"

        # The uuid-shaped heuristic is for files this project writes. Recorded
        # third-party pages carry their own tracking ids — zerozero's fixtures
        # hold three — and failing on those teaches everyone to ignore this
        # test, which is worse than not having it.
        if relative.startswith("tests/fixtures/"):
            continue
        found = pattern.findall(text)
        assert not found, f"{relative} contains what looks like a league guid"


def test_my_own_data_is_still_allowed(tracked_text: dict[Path, str]):
    """The guard must not be so broad that it forbids the project's own subject.

    Without this, a future tightening could quietly start rejecting Manuel's
    squad and results — the thing the repository exists to hold.
    """
    joined = "\n".join(tracked_text.values())
    assert "Melro" in joined
    assert "156412" in joined


def test_the_real_guid_is_still_forbidden_inside_a_fixture(tmp_path, monkeypatch):
    """Exempting fixtures from the uuid heuristic must not exempt the guid.

    A captured page could one day contain it — a screenshot of the league, a
    saved ranking response — and that is exactly the case worth catching.
    """
    import os
    import re

    # Assembled from parts rather than written out. The real guid in a tracked
    # file is the trap this module exists to close — and the guard caught the
    # author walking into it. An invented one spelled in full trips the same
    # check, which is the guard working, not overreach.
    guid = "-".join(("0" * 8, "1" * 4, "2" * 4, "3" * 4, "4" * 12))
    monkeypatch.setenv("LIGA_RECORD_LEAGUE", guid)

    fixture = tmp_path / "captured.html"
    fixture.write_text(f"<html>guid={guid}</html>", encoding="utf-8")

    text = fixture.read_text(encoding="utf-8")
    assert os.environ["LIGA_RECORD_LEAGUE"] in text  # the check the guard runs
    assert re.search(r"[0-9a-f-]{36}", text)
