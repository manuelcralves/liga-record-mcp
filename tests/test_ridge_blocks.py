"""The ablation map, and the comment that promised a test like this one.

`measure_ridge.py` carried a note saying the block indices were held to
`features_for` by a test, and that `FEATURE_COUNT` was derived rather than
typed. Both were false: the count was the literal 22, and no test mentioned
either name. So adding a twenty-third column to `features_for` would leave it
outside every block, and the ablation table would print in full while
describing a model with one fewer input — the exact failure the comment claimed
to prevent, made possible by believing the comment.

A false note about a measurement is worse than no note: it is the reason nobody
looks. These are the checks it promised.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ridge():
    spec = importlib.util.spec_from_file_location(
        "measure_ridge", ROOT / "scripts" / "measure_ridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def a_real_row(ridge):
    """One matchday of the real season, through the real feature builder.

    A synthetic row could be built to agree with BLOCKS by construction, which
    would test the fixture rather than the code. This is the file the script
    actually runs on.
    """
    import sys

    sys.path[:0] = [str(ROOT / "scripts")]
    import measure_projection_accuracy as harness

    if not ridge.SEASON_PATH.is_file():
        pytest.skip(f"no season data at {ridge.SEASON_PATH}")
    points, minutes, cells, _, table, cards = harness.load(ridge.SEASON_PATH, None)
    rows = ridge.features_for(points, minutes, cells, table, cards, 10)
    if not rows:
        pytest.skip("the season produced no feature rows for matchday 10")
    return rows


# --- the promise the comment made ---------------------------------------------


def test_the_count_matches_what_features_for_actually_produces(ridge, a_real_row):
    """The check that makes the derived count mean something."""
    widths = {len(row) for row in a_real_row.values()}
    assert widths == {ridge.FEATURE_COUNT}, (
        f"features_for produces {widths} columns and FEATURE_COUNT says "
        f"{ridge.FEATURE_COUNT} — the ablation table describes a different model"
    )


def test_every_column_is_in_exactly_one_block(ridge):
    """A gap measures a feature under no block; an overlap charges one twice."""
    covered = [i for indices in ridge.BLOCKS.values() for i in indices]
    assert sorted(covered) == list(range(ridge.FEATURE_COUNT))
    assert len(covered) == len(set(covered))


def test_the_count_is_derived_from_the_blocks_not_typed(ridge):
    """The second false claim, made true.

    Typed, it drifts the moment a block is extended. Derived, extending a block
    moves the count with it and the test above catches the disagreement with
    `features_for`.
    """
    source = (ROOT / "scripts" / "measure_ridge.py").read_text(encoding="utf-8")
    assert "FEATURE_COUNT = 22" not in source, "the count went back to a literal"
    assert "FEATURE_COUNT = 1 + max(" in source


def test_a_broken_partition_refuses_at_import_rather_than_reporting(ridge):
    """An ablation run must not start against a map with a hole in it."""
    source = (ROOT / "scripts" / "measure_ridge.py").read_text(encoding="utf-8")
    assert "does not partition the feature row" in source


# --- and the blocks describe what they say they describe ----------------------


def test_the_position_block_is_the_one_hot_columns(ridge):
    """`features_for` ends with one column per position, so the last block has
    to be exactly that many and has to sit at the end."""
    position = ridge.BLOCKS["position"]
    assert len(position) == len(ridge.POSITIONS)
    assert max(position) == ridge.FEATURE_COUNT - 1


def test_the_position_columns_are_one_hot_in_a_real_row(ridge, a_real_row):
    """If the block pointed at the wrong indices this is what would show."""
    for row in a_real_row.values():
        flags = [row[i] for i in ridge.BLOCKS["position"]]
        assert sum(flags) == pytest.approx(1.0), (
            "the `position` block does not point at the one-hot columns"
        )
        assert set(flags) <= {0.0, 1.0}


def test_the_first_column_is_the_estimator_itself(ridge, a_real_row):
    """`our own answer` is the blended projection, so it must vary and must not
    be a flag — the cheapest way to notice the map slipping by one."""
    column = ridge.BLOCKS["our own answer"]
    assert column == (0,)
    values = [row[0] for row in a_real_row.values()]
    assert len(set(values)) > 1
    assert not set(values) <= {0.0, 1.0}
