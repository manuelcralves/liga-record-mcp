"""Where squad data comes from.

The rest of the package depends on the SquadSource protocol, never on a
concrete source, so swapping a hand-written file for the live site touches
only which object gets constructed.
"""

from .base import SquadSource, SquadSourceError
from .manual import ManualSquadSource

__all__ = ["ManualSquadSource", "SquadSource", "SquadSourceError"]
