"""Where a season reconstruction may be written.

`last-season.json` is 2025/26, and the archive reads it as last season. Phase 2
reconstructs 2026/27 with the same script, and run without `--out` it would
have written this season over the model's memory of the last one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_last_season", ROOT / "scripts" / "build_last_season.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_another_season_may_not_overwrite_last_season(builder):
    with pytest.raises(SystemExit, match="--out"):
        builder.output_path(156, None)
    with pytest.raises(SystemExit):
        builder.output_path(156, builder.OUT_PATH)


def test_another_season_goes_where_it_is_told(builder, tmp_path):
    target = tmp_path / "season-2026-27.json"
    assert builder.output_path(156, target) == target


def test_last_season_still_writes_to_its_own_file(builder):
    assert builder.output_path(builder.LAST_SEASON, None) == builder.OUT_PATH
