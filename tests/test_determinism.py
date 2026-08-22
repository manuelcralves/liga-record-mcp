"""The backtests must give the same answer twice.

This exists because they did not, and nobody noticed until the numbers were
already being quoted. `season_report.py` iterated a set of player ids to merge
two seasons. Python randomises string hashing per process, so the set came out
in a different order every run; that order became the order of every dict built
from it, and the hill climb walks its candidates in dict order and stops at the
first swap that helps. Identical code returned seasons 1372, 1402, 1437 and
1482 on consecutive runs.

A measurement that changes when nothing changed is not a measurement, and the
failure is silent — every individual run looks like a result. The only defence
is to keep set iteration out of the path that builds these dicts.

Sets are fine for membership (`i in seen`, `x not in held`). It is iterating one
in a way that reaches the output that has to stay out.

Ambient environment is the same class of failure with a different cause, and
this file does not check for it. A standings test in test_server.py passed or
failed on whether LIGA_RECORD_LEAGUE happened to be set in the shell: the
server reads it into DEFAULT_LEAGUE at import time, and the field-size estimate
is withheld for a private league. Nothing in the test said so, which made the
result a property of the machine rather than of the code. A precondition a test
depends on gets pinned in the test — monkeypatch the module attribute — instead
of being inherited from whatever the shell is carrying.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The scripts whose output is a number somebody will quote.
MEASURING = [
    "season_report.py",
    "measure_projection_accuracy.py",
    "tune.py",
    "backtest_transfers.py",
    "simulate_last_season.py",
]


def iterated_sets(tree: ast.AST) -> list[int]:
    """Lines where a `for` walks something built as a set literal or call."""
    lines = []
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.For):
            target = node.iter
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for generator in node.generators:
                if _is_set(generator.iter):
                    lines.append(generator.iter.lineno)
            continue
        if target is not None and _is_set(target):
            lines.append(target.lineno)
    return lines


def _is_set(node: ast.AST) -> bool:
    """A set literal, a set() call, or a `|`/`-`/`&` of them."""
    if isinstance(node, ast.SetComp) or isinstance(node, ast.Set):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in {"set", "frozenset"}
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitOr, ast.BitAnd, ast.Sub)):
        return _is_set(node.left) or _is_set(node.right)
    return False


@pytest.mark.parametrize("name", MEASURING)
def test_no_measurement_iterates_a_set(name: str) -> None:
    path = ROOT / "scripts" / name
    if not path.exists():
        pytest.skip(f"{name} is not in this checkout")
    found = iterated_sets(ast.parse(path.read_text(encoding="utf-8")))
    assert not found, (
        f"{name} iterates a set at line(s) {found}. Set order varies between "
        "processes, so the run stops being reproducible — sort it, or build the "
        "sequence in file order."
    )


def test_the_check_catches_what_it_is_for() -> None:
    """The exact line that caused it, so a broken checker fails rather than passes."""
    guilty = ast.parse("for player_id in set(loaded) | set(archive):\n    pass\n")
    assert iterated_sets(guilty)

    innocent = ast.parse(
        "seen = set()\n"
        "for player_id in ordered:\n"
        "    if player_id in seen:\n"
        "        continue\n"
    )
    assert not iterated_sets(innocent)
