"""Where squad and market data come from.

The rest of the package depends on the SquadSource protocol, never on a
concrete source, so swapping a hand-written file for the live site touches only
which object gets constructed.

The market client is separate: it reads the public player pool rather than one
person's squad, needs no authentication, and is deliberately read-only.
"""

from .appearances import (
    history_for,
    load_appearances,
    record_round,
    recorded_rounds,
    save_appearances,
)
from .base import SquadSource, SquadSourceError
from .decisions import (
    holidays_used,
    load_decisions,
    record_decision,
    record_season_choice,
    save_decisions,
    track_record,
)
from .last_season import LastSeasonError, LastSeasonSource
from .history import (
    CLUB_NAMES,
    DEFAULT_SEASONS,
    OpenFootballClient,
    combine,
    parse_season,
)
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
    parse_standing,
)
from .manual import ManualSquadSource, load_coaches

__all__ = [
    "CLUB_NAMES",
    "DEFAULT_SEASONS",
    "MARKET_MAX_VALUE",
    "MARKET_MIN_VALUE",
    "POSITION_CODE",
    "POSITION_FROM_CODE",
    "LastSeasonError",
    "LastSeasonSource",
    "LigaRecordClient",
    "OpenFootballClient",
    "combine",
    "parse_season",
    "ManualSquadSource",
    "MarketError",
    "SiteError",
    "SquadSource",
    "SquadSourceError",
    "history_for",
    "holidays_used",
    "load_decisions",
    "load_appearances",
    "load_coaches",
    "record_decision",
    "record_round",
    "record_season_choice",
    "recorded_rounds",
    "save_appearances",
    "save_decisions",
    "track_record",
    "parse_fixtures",
    "parse_market_player",
    "parse_standing",
]
