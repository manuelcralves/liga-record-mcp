"""Can a trained model combine what we already know better than the arithmetic?

Not "does machine learning beat the projection". That question was asked and it
moved underneath the answer: an ablation found a ridge worth +0.0184 and +0.0143
of correlation, and then found that ALL of it came from three fixture features —
opponent, venue, club strength. Those are in the estimator now, along with
suspensions, and the hand-written arithmetic passed the ridge while nobody was
looking. It reads 0.5386 and 0.5505 where the trained model reached about
0.5355 and 0.5344.

So the question left is narrower and more interesting. Given everything the
estimator already knows — INCLUDING its own answer, handed over as a feature —
is there anything left that a fitted model can extract and the arithmetic
cannot?

The design turns on that one feature. The ridge starts from the projection's
own output and has to improve on it, rather than competing with it from the
outside. If there is nothing to add, the fitted weight on that column comes back
near one and the rest near zero, and that is a legible answer rather than a
shrug.

    python scripts/measure_ridge.py

WHAT WOULD MAKE THIS WORTH ADOPTING, decided before running it: beating the
current estimator by more than tune.py's own +0.003 of correlation, on BOTH
seasons. Not on the season total — the noise floor there was measured today at
74 points, wider than anything a ridge is going to move.

A null result is the more useful of the two outcomes. It closes the question
with a number instead of an opinion, which is what stops it being reopened every
six months.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from liga_record_mcp.backtest import (  # noqa: E402
    ABSENT,
    SUSPENDED_PLAYS,
    adjusted_projection,
    club_form_upto,
    suspensions,
    two_part_projection,
)
from liga_record_mcp.models import (  # noqa: E402
    FIRST_SCORING_MATCHDAY,
    LAST_MATCHDAY,
    Position,
)
from liga_record_mcp.stats import (  # noqa: E402
    DEFENSIVE_SHARE,
    fixture_multipliers,
)

import measure_projection_accuracy as harness  # noqa: E402

SEASON_PATH = ROOT / "data" / "last-season.json"
ARCHIVE_PATH = ROOT / "data" / "season-2024-25.json"

#: Features start here rather than at the first predicted round, so that the
#: first prediction has something to train on. Round 2 is the earliest round
#: with any past at all, and its features are correspondingly thin — which is
#: honest rather than unfortunate: that is what the model would have known.
FEATURE_FROM = 2

#: Ridge penalties swept. The winner is chosen on ONE season and applied to the
#: other, once, both ways round — tune.py's protocol, and with two seasons it
#: is the only honest split there is.
PENALTIES = [0.1, 1.0, 10.0, 100.0, 1000.0]

POSITIONS = list(Position)


def rolling(history, player, upto, window, played_only=False, minutes=None):
    """A player's own recent record. Rounds STRICTLY before `upto`."""
    values = [
        history[player][m]
        for m in range(upto - window, upto)
        if m in history.get(player, {})
        and (not played_only or minutes[player].get(m, 0) > 0)
    ]
    return statistics.mean(values) if values else 0.0


def features_for(points, minutes, cells, table, cards, upto):
    """One row per player, all of it knowable before `upto` kicks off."""
    blended = adjusted_projection(
        points, minutes, cells, upto=upto, fixtures=table, cards=cards
    )
    playing, returns = two_part_projection(
        points, minutes, cells, upto=upto, parts=True
    )
    rates, league_against, league_for = club_form_upto(table, upto)
    banned = suspensions(cards, upto)

    rows = {}
    for player, cell in cells.items():
        if player not in blended:
            continue
        club, position_value = cell
        position = Position(position_value)

        fixture = table.get((upto, club))
        if fixture is None:
            defensive = attacking = 1.0
        else:
            opponent, at_home, _, _ = fixture
            club_against, club_for = rates.get(club, (league_against, league_for))
            opponent_against, opponent_for = rates.get(
                opponent, (league_against, league_for)
            )
            defensive, attacking = fixture_multipliers(
                max(club_against, 1e-9),
                max(club_for, 1e-9),
                opponent_against,
                opponent_for,
                league_against,
                league_for,
                at_home=at_home,
            )

        club_against, club_for = rates.get(club, (league_against, league_for))
        earlier = [m for m in minutes.get(player, {}) if m < upto]
        appearances = [m for m in earlier if minutes[player][m] > 0]

        row = [
            blended[player],            # the estimator's own answer
            playing[player],
            returns[player],
            defensive,
            attacking,
            DEFENSIVE_SHARE[position],
            1.0 if player in banned else 0.0,
            club_against,
            club_for,
            len(appearances) / max(1, len(earlier)),
            upto / LAST_MATCHDAY,       # how far into the season we are
            rolling(minutes, player, upto, 1) / 90.0,
            rolling(minutes, player, upto, 3) / 90.0,
            rolling(minutes, player, upto, 5) / 90.0,
            rolling(points, player, upto, 1),
            rolling(points, player, upto, 3),
            rolling(points, player, upto, 5),
            rolling(points, player, upto, 5, played_only=True, minutes=minutes),
        ]
        row.extend(1.0 if position is p else 0.0 for p in POSITIONS)
        rows[player] = row
    return rows


def solve(matrix, vector):
    """Gaussian elimination with partial pivoting. Small and dense; no library."""
    size = len(vector)
    augmented = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            if factor:
                for k in range(column, size + 1):
                    augmented[row][k] -= factor * augmented[column][k]
    answer = [0.0] * size
    for row in range(size - 1, -1, -1):
        total = augmented[row][size] - sum(
            augmented[row][k] * answer[k] for k in range(row + 1, size)
        )
        answer[row] = total / augmented[row][row]
    return answer


def gram(rows, targets):
    """X'X and X'y, which do not depend on the penalty — so the sweep is free."""
    width = len(rows[0])
    xtx = [[0.0] * width for _ in range(width)]
    xty = [0.0] * width
    for row, target in zip(rows, targets):
        for i in range(width):
            value = row[i]
            if value:
                xty[i] += value * target
                line = xtx[i]
                for j in range(i, width):
                    line[j] += value * row[j]
    for i in range(width):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    return xtx, xty


def fit(xtx, xty, penalty):
    """Ridge. The intercept column is left unpenalised, as it should be."""
    width = len(xty)
    damped = [list(line) for line in xtx]
    for i in range(1, width):
        damped[i][i] += penalty
    return solve(damped, xty)


def correlation(pairs):
    truth = [a for a, _ in pairs]
    guess = [g for _, g in pairs]
    mean_a, mean_g = statistics.mean(truth), statistics.mean(guess)
    covariance = sum((a - mean_a) * (g - mean_g) for a, g in pairs) / len(pairs)
    spread_a, spread_g = statistics.pstdev(truth), statistics.pstdev(guess)
    return covariance / (spread_a * spread_g) if spread_a and spread_g else 0.0


def walk_forward(points, minutes, cells, table, cards):
    """Predict every round from rounds strictly before it, for every penalty.

    The feature matrix is built ONCE per round. What a player looked like
    before round 7 does not depend on which round is being predicted, so the
    naive reading — rebuild the features for every (train, predict) pair — is
    thirty times the work for the same numbers.
    """
    built = {
        matchday: features_for(points, minutes, cells, table, cards, matchday)
        for matchday in range(FEATURE_FROM, LAST_MATCHDAY + 1)
    }

    predictions: dict[float, list[tuple[float, float]]] = {p: [] for p in PENALTIES}
    baseline: list[tuple[float, float]] = []
    weights: dict[float, list[float]] = {}
    # And the same pairs kept round by round.
    #
    # Pooled correlation is not the question a team sheet asks. Correlation is
    # invariant to an affine transform, so rescaling every estimate identically
    # cannot change it WITHIN a round — but a model refitted each week applies a
    # DIFFERENT rescale each week, and that does move the pooled figure without
    # reordering a single eleven. The manager picks from one round's ranking, so
    # the mean of the within-round correlations is the honest reading and the
    # pooled one can flatter a recalibrator.
    per_round: dict[float, list[list[tuple[float, float]]]] = {
        p: [] for p in PENALTIES
    }
    baseline_rounds: list[list[tuple[float, float]]] = []

    for matchday in range(FIRST_SCORING_MATCHDAY, LAST_MATCHDAY + 1):
        rows, targets = [], []
        for earlier in range(FEATURE_FROM, matchday):
            for player, row in built[earlier].items():
                if earlier in points.get(player, {}):
                    rows.append([1.0] + row)
                    targets.append(points[player][earlier])
        if not rows or len(rows) <= len(rows[0]):
            continue

        xtx, xty = gram(rows, targets)
        today = built[matchday]
        for penalty in PENALTIES:
            beta = fit(xtx, xty, penalty)
            if beta is None:
                continue
            weights[penalty] = beta
            fitted = [
                (
                    points[player][matchday],
                    beta[0] + sum(b * v for b, v in zip(beta[1:], row)),
                )
                for player, row in today.items()
            ]
            predictions[penalty].extend(fitted)
            per_round[penalty].append(fitted)
        plain = [
            (points[player][matchday], row[0]) for player, row in today.items()
        ]
        baseline.extend(plain)
        baseline_rounds.append(plain)

    return predictions, baseline, weights, per_round, baseline_rounds


def within(rounds):
    """The mean of the per-round correlations — what a team sheet is picked on."""
    scored = [correlation(pairs) for pairs in rounds if len(pairs) > 2]
    return statistics.mean(scored) if scored else 0.0


def report(label, season, archive):
    points, minutes, cells, _, table, cards = harness.load(season, archive)
    predictions, baseline, weights, per_round, baseline_rounds = walk_forward(
        points, minutes, cells, table, cards
    )
    base, base_within = correlation(baseline), within(baseline_rounds)
    print(f"\n{label}")
    print(f"  {'':<30}{'pooled':>9}{'within round':>14}")
    print(f"  {'estimator (fixture + bans)':<30}{base:>9.4f}{base_within:>14.4f}")
    scored = {}
    for penalty in PENALTIES:
        if not predictions[penalty]:
            continue
        r = correlation(predictions[penalty])
        w = within(per_round[penalty])
        scored[penalty] = w
        note = "" if abs(w - base_within) > 0.003 else "  (nothing that survives)"
        print(
            f"  {'ridge, penalty ' + str(penalty):<30}{r:>9.4f}{w:>14.4f}"
            f"{w - base_within:>+11.4f}{note}"
        )
    if weights:
        best = max(scored, key=scored.get)
        beta = weights[best]
        print(
            f"  weight the best fit put on the estimator's own answer: "
            f"{beta[1]:.3f}  (intercept {beta[0]:+.3f})"
        )
    return base, scored


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=Path, default=SEASON_PATH)
    parser.add_argument("--archive", type=Path, default=None)
    args = parser.parse_args()
    report(f"{args.season.name}, archive={bool(args.archive)}", args.season, args.archive)


if __name__ == "__main__":
    main()
