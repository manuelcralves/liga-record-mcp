"""Domain models and regulation constants for Liga Record.

Money is always integer euros, never float: quotes move in 50 000 steps
(§12.3-§12.4) and floor at 500 000 (§12.1), so integers are exact.

Section markers (§) refer to the official regulation at
https://liga.record.pt/info/ajuda.aspx, summarised in docs/PLANNING.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Position(str, Enum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    FWD = "FWD"


#: Labels as they appear on liga.record.pt, for the step-4 scrapers to map.
POSITION_LABELS_PT: dict[Position, str] = {
    Position.GK: "Guarda-redes",
    Position.DEF: "Defesa",
    Position.MID: "Médio",
    Position.FWD: "Avançado",
}

# --- Squad composition (§6.6) ---
SQUAD_QUOTA: dict[Position, int] = {
    Position.GK: 3,
    Position.DEF: 8,
    Position.MID: 8,
    Position.FWD: 4,
}
SQUAD_SIZE = sum(SQUAD_QUOTA.values())  # 23

# Note: there is deliberately no max-players-per-club constant. §6.5 states
# explicitly that no such limit exists, nor any limit on foreign players.

# --- Round selection (§6.13) ---
XI_SIZE = 11
BENCH_SIZE = 4
MAX_SUBS_ON = 3  # only 3 of the 4 named substitutes may come on

#: Legal starter counts per position: exactly one keeper, ranges for the rest.
STARTER_RANGE: dict[Position, tuple[int, int]] = {
    Position.GK: (1, 1),
    Position.DEF: (3, 5),
    Position.MID: (3, 5),
    Position.FWD: (1, 3),
}

# --- Money (§6.4, §12.1) ---
BASE_BUDGET = 40_000_000
MIN_PRICE = 500_000

# --- Transfers (§6.8, §6.9, §6.10, §6.11) ---
#: §6.8 — "a transferência de um só jogador entre cada ronda". One per round,
#: and nothing in the regulation banks an unused one. Every extra it grants is
#: scoped to "a ronda em questão", which is the same rule read from the other
#: side: allowances belong to a round and expire with it.
TRANSFERS_PER_ROUND = 1

#: §6.9 — the February reopening, 2 to 28 February 2027, covering Liga Record
#: rounds 16 to 19. Six swaps for the whole window, not six a round, and the
#: per-round transfer of §6.8 is switched off while it runs.
REOPENED_MAX_SWAPS = 6
REOPENED_FIRST_ROUND = 16
REOPENED_LAST_MATCHDAY = 24

#: §6.10 and §6.11 — compensation when the game changes under a participant:
#: two extra transfers if a player of his changes position, one if the player
#: moves to another competition. Per affected player, and for that round only.
#: §6.11 suspends both during the reopening and in the round before it.
POSITION_CHANGE_TRANSFERS = 2
COMPETITION_CHANGE_TRANSFERS = 1

# --- The calendar (§16.3, §16.4, §16.5, §19) ---
#: Liga Record is shorter than the league it scores, and a "ronda" is not a
#: "jornada" — the regulation uses both words in one sentence without ever
#: saying they differ.
#:
#: WHERE IT STARTS IS DISPUTED BY THE REGULATION ITSELF. Four clauses say the
#: competition is 29 rounds over matchdays 6 to 34:
#:
#:   §16.4  the standings count "entre a 6.ª e a 34.ª jornadas (inclusive)"
#:   §16.5  "cada uma das 29 rondas da Liga Record"
#:   §16.3  round 13 is matchday 18, and round 29 is matchday 34
#:   §12.1  the first price list is "para a 6.ª jornada"
#:
#: One clause says otherwise. §19 calls "a 5ª jornada do campeonato" the one
#: "que marca o início efetivo do passatempo", and says the trial covers "estas
#: 4 primeiras jornadas" and "dura até dia 1 de setembro" — which fits the
#: calendar exactly, since matchday 4 ends on 31 August. That reading gives 30
#: rounds over matchdays 5 to 34.
#:
#: Six is used here because four numbered statements agree on it and one piece
#: of prose does not, and because §16.4 is the clause that actually defines the
#: classification. It is an assumption, not a reading, and it is worth one
#: round either way.
#:
#: SETTLED BY WATCHING, not by re-reading. Two things are already observed and
#: they agree with §19 rather than §16:
#:
#:   * the squad locks at matchday 5 — transfers are unlimited until then, one
#:     a round after, which is the participant's own account of the site
#:   * the trial is described everywhere as the first FOUR matchdays
#:
#: So matchday 5 is probably the first that counts, which would make the
#: competition 30 rounds and not 29. What settles it is whether matchday 5's
#: score appears in the season total; until it is scored, nobody can look.
#:
#: The one thing that does NOT depend on the answer, and the only one with a
#: deadline attached: the squad has to be finished before matchday 5.
FIRST_SCORING_MATCHDAY = 6
LAST_MATCHDAY = 34
RECORD_ROUNDS = LAST_MATCHDAY - FIRST_SCORING_MATCHDAY + 1  # 29, if §16 is right

#: §19's reading, kept so the difference can be reasoned about rather than
#: forgotten the moment the constant above stops being questioned.
DISPUTED_FIRST_MATCHDAY = 5


def matchday_of_round(round_number: int) -> int:
    """Which Primeira Liga matchday a Liga Record round scores (§16.3)."""
    return round_number + FIRST_SCORING_MATCHDAY - 1


def round_of_matchday(matchday: int) -> int | None:
    """Which Liga Record round a matchday belongs to, or None before the start.

    Matchdays 1 to 5 are the trial period of §19: points are awarded exactly as
    in the real thing, and §16.4 does not count them.
    """
    if matchday < FIRST_SCORING_MATCHDAY:
        return None
    return matchday - FIRST_SCORING_MATCHDAY + 1


# --- The holiday mechanism (§6.17) ---
#: Three rounds a season, consecutive or not, never in the last three, a
#: participant may take half the round winner's score instead of his own team's
#: — rounded up, and whatever his team actually did.
HOLIDAY_ROUNDS = 3
HOLIDAY_SHARE_OF_WINNER = 0.5
HOLIDAY_BLOCKED_LAST_ROUNDS = 3

# --- The top-scorer bet (§10.3(m)) ---
#: Chosen once, when the first version of the team is submitted, and never
#: again. Costs nothing and is optional, so not making it is a choice too.
GOLDEN_BOOT_BONUS = 20
SILVER_BOOT_BONUS = 10
BRONZE_BOOT_BONUS = 5

# --- Coaches (§6.15) — one per club, and they never cost budget ---
COACH_COUNT = 18


class Valuation(str, Enum):
    """Which price to value a squad at.

    OPEN QUESTION: §12.2 says a participant keeps the price paid when a quote
    rises and only realises decreases at the February renewal, which implies
    in-season squad value is not simply the sum of current quotes. Unverified
    against the live site, so the basis stays an explicit parameter rather
    than a baked-in assumption.
    """

    CURRENT = "current"  # V.A., valor atual — the live quote
    PAID = "paid"  # V.I., valor inicial — the price actually paid


class TransferWindow(str, Enum):
    IN_SEASON = "in_season"  # §6.8 — one transfer per round
    CLOSED = "closed"  # §6.8 — February, before the market reopens
    REOPENED = "reopened"  # §6.9 — up to REOPENED_MAX_SWAPS swaps


class Player(BaseModel):
    """A player in the Liga Record pool.

    ``id`` is the game's player code. §4.5 describes it as one letter and five
    digits, but the real format is unverified, so it is not validated here.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    position: Position
    club: str
    # §12.1 — a quote never falls below MIN_PRICE, so anything lower means the
    # data is wrong. Fail at the boundary rather than carry it inward.
    value: int = Field(ge=MIN_PRICE)  # V.A. — current quote
    initial_value: int = Field(ge=MIN_PRICE)  # V.I. — price paid
    points_total: int = 0
    points_round: int = 0

    def price(self, basis: Valuation = Valuation.CURRENT) -> int:
        return self.value if basis is Valuation.CURRENT else self.initial_value


class MarketPlayer(BaseModel):
    """A player in the transfer market — not necessarily one you own.

    Carries what the squad page does not: how many teams hold this player. That
    is the whole basis of a differential pick, so it is worth a separate model
    rather than being flattened into Player.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    position: Position
    club: str
    value: int = Field(ge=MIN_PRICE)  # V.A.
    initial_value: int = Field(ge=MIN_PRICE)  # V.I.
    points_total: int = 0
    points_round: int = 0
    owned_by_teams: int = Field(default=0, ge=0)
    owned_percent: float = Field(default=0.0, ge=0.0, le=100.0)

    def as_player(self) -> Player:
        """The squad-shaped view, so the rules functions can take it directly."""
        return Player(
            id=self.id,
            name=self.name,
            position=self.position,
            club=self.club,
            value=self.value,
            initial_value=self.initial_value,
            points_total=self.points_total,
            points_round=self.points_round,
        )


class Fixture(BaseModel):
    """One match in the league calendar.

    Club names are the site's own labels ("Sp. Braga", "V. Guimarães") and are
    the join key back to a Player's club, so they are kept verbatim rather than
    normalised into something tidier that would no longer match.
    """

    model_config = ConfigDict(frozen=True)

    round_number: int = Field(ge=1)
    home: str
    away: str
    home_goals: int | None = None
    away_goals: int | None = None
    #: As the site labels it, e.g. "07 AGO 20:15". Carries no year, so it is
    #: kept as text rather than parsed into a datetime that would invent one.
    kickoff: str | None = None

    @property
    def played(self) -> bool:
        return self.home_goals is not None and self.away_goals is not None

    def opponent_of(self, club: str) -> str | None:
        if club == self.home:
            return self.away
        if club == self.away:
            return self.home
        return None

    def is_home_for(self, club: str) -> bool:
        return club == self.home


class TeamStanding(BaseModel):
    """One entry in a Liga Record leaderboard.

    The only measure of whether any of this is working. Every projection in
    this project is an argument about the future; a position is a fact about
    the present, and it is the number the participant actually cares about.

    `position_change` is signed the way the site reports it: positive means
    dropping down the table, because the position number grew.
    """

    model_config = ConfigDict(frozen=True)

    team_id: int
    team_name: str
    user_name: str
    points_total: int
    points_round: int
    #: Rank among every team in the country.
    position: int
    #: Rank inside a private league. Only the league service fills this in; a
    #: national query leaves it None. It is the number a participant playing
    #: against friends actually cares about, so it is kept distinct from
    #: `position` rather than overwriting it.
    position_league: int | None = None
    position_round: int | None = None
    position_change: int | None = None
    formation: str | None = None
    region: str = ""

    @property
    def moved_up(self) -> bool | None:
        """True if the team climbed, None if the site gave no movement."""
        if self.position_change is None or self.position_change == 0:
            return None
        return self.position_change < 0


class ClubRecord(BaseModel):
    """A club's results over one or more completed seasons.

    This is the historical prior. Team strength explains most of a defender's
    Liga Record rate — clean sheets and goals conceded are club events, not
    individual ones — and a full season of it is worth far more than two
    matches of the current one.
    """

    model_config = ConfigDict(frozen=True)

    club: str  # the Liga Record label, so it joins to Player.club
    seasons: tuple[str, ...] = ()
    matches: int = Field(default=0, ge=0)
    goals_for: int = Field(default=0, ge=0)
    goals_against: int = Field(default=0, ge=0)
    clean_sheets: int = Field(default=0, ge=0)
    wins: int = Field(default=0, ge=0)

    @property
    def has_history(self) -> bool:
        """False for promoted clubs — a real state, not a zero."""
        return self.matches > 0

    @property
    def goals_for_per_match(self) -> float | None:
        return self.goals_for / self.matches if self.matches else None

    @property
    def goals_against_per_match(self) -> float | None:
        return self.goals_against / self.matches if self.matches else None

    @property
    def clean_sheet_rate(self) -> float | None:
        return self.clean_sheets / self.matches if self.matches else None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.matches if self.matches else None


class Coach(BaseModel):
    """One of the 18 selectable coaches (§6.15). Free — never costs budget."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    club: str
    points_total: int = 0
    points_round: int = 0


class Squad(BaseModel):
    """The 23 players under contract, plus the money around them."""

    model_config = ConfigDict(frozen=True)

    team_id: int
    team_name: str
    players: tuple[Player, ...]
    # Both are magnitudes: a negative penalty would quietly raise the budget.
    bonus: int = Field(default=0, ge=0)
    penalties: int = Field(default=0, ge=0)

    @property
    def budget(self) -> int:
        """§6.4 — 40M, raised only by earned bonus, reduced by penalties."""
        return BASE_BUDGET + self.bonus - self.penalties

    def value(self, basis: Valuation = Valuation.CURRENT) -> int:
        return sum(p.price(basis) for p in self.players)

    def balance(self, basis: Valuation = Valuation.CURRENT) -> int:
        return self.budget - self.value(basis)

    def by_id(self) -> dict[str, Player]:
        return {p.id: p for p in self.players}


class Selection(BaseModel):
    """One round's team sheet (§6.13, §6.15).

    ``bench`` order is significant: §11.2 processes automatic substitutions in
    the order the substitutes were placed, so this is a sequence, not a set.
    """

    model_config = ConfigDict(frozen=True)

    starters: tuple[str, ...]
    bench: tuple[str, ...]
    captain: str | None = None
    coach_id: str | None = None


class Violation(BaseModel):
    """A broken rule, carrying the regulation reference that broke."""

    rule: str
    detail: str


class SquadCheck(BaseModel):
    is_valid: bool
    violations: list[Violation] = Field(default_factory=list)


class SelectionCheck(BaseModel):
    is_valid: bool
    formation: str | None = None
    violations: list[Violation] = Field(default_factory=list)


class TransferCheck(BaseModel):
    is_valid: bool
    value_after: int | None = None
    balance_after: int | None = None
    violations: list[Violation] = Field(default_factory=list)


class Substitution(BaseModel):
    out_id: str
    in_id: str
    position: Position
    reason: str


class AutosubResult(BaseModel):
    substitutions: list[Substitution] = Field(default_factory=list)
    captain_id: str | None = None
    captain_inherited: bool = False
    unreplaced: list[str] = Field(default_factory=list)


class SquadSnapshot(BaseModel):
    """Squad data together with where and when it came from.

    Provenance travels with the data so a tool result can say "as of Tuesday"
    rather than presenting a stale squad as the current one. The clock lives
    here at the source boundary, never in rules.py.
    """

    model_config = ConfigDict(frozen=True)

    squad: Squad
    selection: Selection | None = None
    round_number: int | None = None
    fetched_at: datetime
    source: str
