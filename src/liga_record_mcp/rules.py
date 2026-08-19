"""The Liga Record rulebook as pure functions.

No I/O, no network, no clock. Every function here is a total function of its
arguments, so the whole rulebook is testable without touching the site.

Section markers (§) refer to the regulation; see models.py.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from .models import (
    BENCH_SIZE,
    MAX_SUBS_ON,
    MIN_PRICE,
    SQUAD_QUOTA,
    SQUAD_SIZE,
    STARTER_RANGE,
    XI_SIZE,
    AutosubResult,
    Player,
    Position,
    Selection,
    SelectionCheck,
    Squad,
    SquadCheck,
    Substitution,
    TransferCheck,
    TransferWindow,
    Valuation,
    Violation,
)


def _eur(amount: int) -> str:
    """Format euros the way the site does: 1 500 000."""
    return f"{amount:,}".replace(",", " ")


def _counts(players: Iterable[Player]) -> Counter[Position]:
    return Counter(p.position for p in players)


def formation(starters: Sequence[Player]) -> str:
    """DEF-MID-FWD, the way the game writes it. The keeper is implied."""
    c = _counts(starters)
    return f"{c[Position.DEF]}-{c[Position.MID]}-{c[Position.FWD]}"


def legal_formations() -> list[str]:
    """Every shape satisfying §6.13, derived rather than hardcoded.

    The per-position ranges alone allow 8 to 14 outfielders, so the XI size is
    an independent constraint. Intersecting them leaves seven formations.
    """
    lo_d, hi_d = STARTER_RANGE[Position.DEF]
    lo_m, hi_m = STARTER_RANGE[Position.MID]
    lo_f, hi_f = STARTER_RANGE[Position.FWD]
    lo_g, _ = STARTER_RANGE[Position.GK]
    shapes = [
        f"{d}-{m}-{f}"
        for d in range(lo_d, hi_d + 1)
        for m in range(lo_m, hi_m + 1)
        for f in range(lo_f, hi_f + 1)
        if lo_g + d + m + f == XI_SIZE
    ]
    return sorted(shapes)


def validate_squad(squad: Squad) -> SquadCheck:
    """Check the squad itself against §6.4 and §6.6.

    A hand-maintained squad file drifts: a mistyped position or a transfer
    recorded on one side only should fail loudly where the data enters, not
    surface three tools later as a confidently wrong recommendation.
    """
    violations: list[Violation] = []
    ids = [p.id for p in squad.players]

    repeated = sorted({i for i, n in Counter(ids).items() if n > 1})
    if repeated:
        violations.append(
            Violation(rule="§6.6", detail=f"duplicate player ids: {', '.join(repeated)}")
        )

    if len(squad.players) != SQUAD_SIZE:
        violations.append(
            Violation(
                rule="§6.6",
                detail=f"expected {SQUAD_SIZE} players, got {len(squad.players)}",
            )
        )

    counts = _counts(squad.players)
    for position, quota in SQUAD_QUOTA.items():
        found = counts[position]
        if found != quota:
            violations.append(
                Violation(
                    rule="§6.6",
                    detail=f"{position.value}: {found} in the squad, must be exactly {quota}",
                )
            )

    value = squad.value()
    if value > squad.budget:
        violations.append(
            Violation(rule="§6.4", detail=f"{_eur(value - squad.budget)} over budget")
        )

    return SquadCheck(is_valid=not violations, violations=violations)


def validate_selection(squad: Squad, selection: Selection) -> SelectionCheck:
    """Check one round's team sheet against §6.13, §6.17 and §10.3(l)."""
    violations: list[Violation] = []
    index = squad.by_id()
    picked = list(selection.starters) + list(selection.bench)

    unknown = sorted({i for i in picked if i not in index})
    if unknown:
        violations.append(
            Violation(
                rule="§6.13",
                detail=f"not in the 23-man squad: {', '.join(unknown)}",
            )
        )

    repeated = sorted({i for i, n in Counter(picked).items() if n > 1})
    if repeated:
        violations.append(
            Violation(
                rule="§6.13",
                detail=f"selected more than once: {', '.join(repeated)}",
            )
        )

    if len(selection.starters) != XI_SIZE:
        violations.append(
            Violation(
                rule="§6.13",
                detail=f"expected {XI_SIZE} starters, got {len(selection.starters)}",
            )
        )
    if len(selection.bench) != BENCH_SIZE:
        violations.append(
            Violation(
                rule="§6.13",
                detail=f"expected {BENCH_SIZE} substitutes, got {len(selection.bench)}",
            )
        )

    starters = [index[i] for i in selection.starters if i in index]
    counts = _counts(starters)
    for position, (low, high) in STARTER_RANGE.items():
        found = counts[position]
        if not low <= found <= high:
            wanted = str(low) if low == high else f"{low}-{high}"
            violations.append(
                Violation(
                    rule="§6.13",
                    detail=f"{position.value}: {found} in the XI, must be {wanted}",
                )
            )

    # §10.3(l) — the captain doubles points, so must be one of the starters.
    if selection.captain is not None and selection.captain not in selection.starters:
        violations.append(
            Violation(rule="§10.3(l)", detail="captain must be one of the starters")
        )

    # §6.17 — XI, four substitutes and a coach, or the round scores zero.
    if not selection.coach_id:
        violations.append(
            Violation(
                rule="§6.17",
                detail="no coach selected — the team scores zero for the round",
            )
        )

    shape = formation(starters) if len(starters) == XI_SIZE else None
    return SelectionCheck(
        is_valid=not violations, formation=shape, violations=violations
    )


def validate_transfer(
    squad: Squad,
    out_id: str,
    incoming: Player,
    *,
    transfers_available: int = 1,
    basis: Valuation = Valuation.CURRENT,
    window: TransferWindow = TransferWindow.IN_SEASON,
) -> TransferCheck:
    """Check a single swap against §6.4 and §6.8.

    ``transfers_available`` is an input rather than something derived here:
    working it out needs the calendar plus §6.10/§6.11 position and
    competition-change compensations, which are step-4 concerns.

    The incoming player is paid for at their current quote. The outgoing player
    is credited at ``basis`` — see the Valuation docstring for why that is not
    yet settled.
    """
    violations: list[Violation] = []
    index = squad.by_id()
    outgoing = index.get(out_id)

    if window is TransferWindow.CLOSED:
        violations.append(
            Violation(
                rule="§6.8",
                detail="transfers are disabled in February until the market reopens",
            )
        )
    elif transfers_available < 1:
        violations.append(
            Violation(rule="§6.8", detail="no transfers available this round")
        )

    if outgoing is None:
        violations.append(
            Violation(rule="§6.8", detail=f"{out_id} is not in the squad")
        )
    if incoming.id in index:
        violations.append(
            Violation(rule="§6.6", detail=f"{incoming.name} is already in the squad")
        )
    if outgoing is not None and incoming.position is not outgoing.position:
        violations.append(
            Violation(
                rule="§6.8",
                detail=(
                    "must swap like for like to keep the positional contingent: "
                    f"{outgoing.position.value} out, {incoming.position.value} in"
                ),
            )
        )

    value_after: int | None = None
    balance_after: int | None = None
    if outgoing is not None:
        value_after = squad.value(basis) - outgoing.price(basis) + incoming.value
        balance_after = squad.budget - value_after
        if value_after > squad.budget:
            violations.append(
                Violation(
                    rule="§6.4",
                    detail=f"{_eur(value_after - squad.budget)} over budget",
                )
            )

    return TransferCheck(
        is_valid=not violations,
        value_after=value_after,
        balance_after=balance_after,
        violations=violations,
    )


def project_price_change(round_points: int) -> int:
    """Quote movement earned by one round's score (§12.3-§12.4).

    GAP IN THE REGULATION: §12.3 tabulates 4-5, 6-9 and 10+ upward, §12.4
    covers 0 and negative downward — but scores of 1, 2 or 3 are never
    mentioned. Treated here as no movement. Worth confirming against a real
    round before trusting it.
    """
    if round_points >= 10:
        return 150_000
    if round_points >= 6:
        return 100_000
    if round_points >= 4:
        return 50_000
    if round_points < 0:
        return -100_000
    if round_points == 0:
        return -50_000
    return 0


def project_new_price(current_value: int, round_points: int) -> int:
    """Apply the movement, respecting the §12.1 floor of 500 000."""
    return max(MIN_PRICE, current_value + project_price_change(round_points))


def simulate_autosubs(
    squad: Squad, selection: Selection, unavailable: Iterable[str]
) -> AutosubResult:
    """Apply the §11 automatic substitutions to a team sheet.

    A starter is replaced when they did not play, or their match was abandoned
    or postponed after the round closed (§11.1). Pass both cases in as
    ``unavailable``; the distinction does not change the outcome.

    Processing follows bench order (§11.2): walk the substitutes as named and
    let each replace a failing starter of the same position. Where several
    same-position starters need replacing but only one substitute is eligible,
    §11.3 awards the substitution to the lower-valued starter, then the
    lower-scoring one, both as recorded at the previous round's close.

    At most three substitutes come on (§6.13), and a replaced captain passes
    the armband to whoever comes on (§11.5).
    """
    index = squad.by_id()
    out = set(unavailable)
    failing = [i for i in selection.starters if i in out and i in index]

    substitutions: list[Substitution] = []
    replaced: set[str] = set()
    used_subs: set[str] = set()
    captain_id = selection.captain
    captain_inherited = False

    for bench_id in selection.bench:
        if len(substitutions) >= MAX_SUBS_ON:
            break
        coming_on = index.get(bench_id)
        # §10.3(i) — only players who actually take the field count, so a
        # substitute who is themselves unavailable cannot come on. A repeated
        # bench id cannot come on twice either: validate_selection rejects such
        # a sheet, but this function must not invent a second appearance for one
        # player if a malformed sheet reaches it anyway.
        if coming_on is None or bench_id in out or bench_id in used_subs:
            continue

        candidates = [
            index[i]
            for i in failing
            if i not in replaced and index[i].position is coming_on.position
        ]
        if not candidates:
            continue

        # §11.3 tie-break. Player id breaks a remaining exact tie so the result
        # is stable regardless of input order; the regulation stops at two
        # criteria and says nothing about that case.
        going_off = min(candidates, key=lambda p: (p.value, p.points_total, p.id))
        replaced.add(going_off.id)
        used_subs.add(bench_id)
        substitutions.append(
            Substitution(
                out_id=going_off.id,
                in_id=coming_on.id,
                position=coming_on.position,
                reason=f"{going_off.name} unavailable, {coming_on.name} on from the bench",
            )
        )
        if captain_id == going_off.id:
            captain_id = coming_on.id
            captain_inherited = True

    return AutosubResult(
        substitutions=substitutions,
        captain_id=captain_id,
        captain_inherited=captain_inherited,
        unreplaced=[i for i in failing if i not in replaced],
    )
