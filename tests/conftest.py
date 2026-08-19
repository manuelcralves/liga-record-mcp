from __future__ import annotations

import pytest
from helpers import make_squad, selection_for

from liga_record_mcp.models import Selection, Squad


@pytest.fixture
def squad() -> Squad:
    return make_squad()


@pytest.fixture
def legal_selection(squad: Squad) -> Selection:
    """A 4-4-2 satisfying every §6.13 constraint."""
    return selection_for(squad, 4, 4, 2)
