"""The data-source seam.

Everything above this protocol is indifferent to where squad data came from: a
hand-written YAML file today, liga.record.pt once the scraper exists. That is
the whole point of the seam — the MCP server, the rules and the tests can all
be finished and trusted before the site is ever touched.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import SquadSnapshot


class SquadSourceError(RuntimeError):
    """The squad data is missing, malformed, or breaks the regulation.

    Raised at the boundary rather than returned, because there is no useful
    partial answer: a squad that is not a legal squad cannot be reasoned about.
    """


@runtime_checkable
class SquadSource(Protocol):
    """Anything that can produce a current squad."""

    #: Short label recorded on every snapshot, e.g. "manual" or "ligarecord".
    name: str

    def load(self) -> SquadSnapshot:
        """Return the current squad, or raise SquadSourceError."""
        ...
