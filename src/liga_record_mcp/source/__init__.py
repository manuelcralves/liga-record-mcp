"""Where squad and market data come from.

The rest of the package depends on the SquadSource protocol, never on a
concrete source, so swapping a hand-written file for the live site touches only
which object gets constructed.

The market client is separate: it reads the public player pool rather than one
person's squad, needs no authentication, and is deliberately read-only.
"""

from .base import SquadSource, SquadSourceError
from .live import (
    MARKET_MAX_VALUE,
    MARKET_MIN_VALUE,
    POSITION_CODE,
    POSITION_FROM_CODE,
    LigaRecordClient,
    MarketError,
    SiteError,
    parse_fixtures,
    parse_market_player,
)
from .manual import ManualSquadSource

__all__ = [
    "MARKET_MAX_VALUE",
    "MARKET_MIN_VALUE",
    "POSITION_CODE",
    "POSITION_FROM_CODE",
    "LigaRecordClient",
    "ManualSquadSource",
    "MarketError",
    "SiteError",
    "SquadSource",
    "SquadSourceError",
    "parse_fixtures",
    "parse_market_player",
]
