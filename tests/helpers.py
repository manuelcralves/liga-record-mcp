"""Builders for test squads and team sheets."""

from __future__ import annotations

from collections.abc import Sequence

from liga_record_mcp.models import SQUAD_QUOTA, Player, Position, Selection, Squad


def rules_broken(result) -> set[str]:
    """The set of regulation references a check reported."""
    return {v.rule for v in result.violations}


def make_player(
    player_id: str,
    position: Position,
    value: int,
    *,
    points_total: int = 0,
    name: str | None = None,
    club: str = "Test FC",
    initial_value: int | None = None,
) -> Player:
    return Player(
        id=player_id,
        name=name or player_id,
        position=position,
        club=club,
        value=value,
        initial_value=value if initial_value is None else initial_value,
        points_total=points_total,
    )


def make_squad(players: Sequence[Player] | None = None) -> Squad:
    """A §6.6-shaped squad: 3 GK, 8 DEF, 8 MID, 4 FWD.

    Values ascend distinctly (600 000 up to 2 800 000) so the §11.3 tie-break
    is unambiguous, and points_total ascends with them. Total squad value comes
    to 39 100 000 against the 40 000 000 budget, leaving a realistically tight
    900 000 for transfer tests.
    """
    if players is not None:
        return Squad(team_id=156412, team_name="Melro", players=tuple(players))

    built: list[Player] = []
    n = 0
    for position, quota in SQUAD_QUOTA.items():
        for i in range(1, quota + 1):
            n += 1
            built.append(
                make_player(
                    f"{position.value}{i}",
                    position,
                    500_000 + n * 100_000,
                    points_total=n,
                    club=f"Club {i}",
                )
            )
    return Squad(team_id=156412, team_name="Melro", players=built)


def selection_for(
    squad: Squad,
    defenders: int,
    midfielders: int,
    forwards: int,
    *,
    captain: str | None = None,
    coach_id: str | None = "C1",
    bench: tuple[str, ...] | None = None,
) -> Selection:
    """A team sheet of the requested shape, benching the first four leftovers."""
    ids = {
        position: [p.id for p in squad.players if p.position is position]
        for position in Position
    }
    starters = (
        ids[Position.GK][:1]
        + ids[Position.DEF][:defenders]
        + ids[Position.MID][:midfielders]
        + ids[Position.FWD][:forwards]
    )
    if bench is None:
        used = set(starters)
        bench = tuple(p.id for p in squad.players if p.id not in used)[:4]
    return Selection(
        starters=tuple(starters),
        bench=bench,
        captain=captain or starters[0],
        coach_id=coach_id,
    )
