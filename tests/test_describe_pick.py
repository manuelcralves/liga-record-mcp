"""Saying how much a recommendation should be trusted.

"Risky" is three different claims and they call for three different decisions:
the player might be unpredictable, the estimate might be a guess, or nobody
else might own him. Collapsing them into one word is how a recommendation gets
read the wrong way round — a differential dismissed as unreliable, or a guess
about a promoted club's midfielder taken as a measurement.

So the two axes are kept apart. How sure the number is says nothing about
where it puts you against the field, and the field says nothing about whether
the number is any good.

Every threshold is a percentile of the real market rather than a round number
somebody liked, and the pins below fail if that ever stops being true.
"""

from __future__ import annotations

from liga_record_mcp.stats import (
    AVAILABILITY_FRINGE,
    AVAILABILITY_REGULAR,
    EVIDENCE_MEASURED,
    EVIDENCE_THIN,
    OWNED_DIFFERENTIAL,
    OWNED_TEMPLATE,
    VOLATILITY_STEADY,
    VOLATILITY_SWINGY,
    describe_pick,
)


# --------------------------------------------------------------------------
# How sure the number is
# --------------------------------------------------------------------------


def test_a_thin_estimate_says_so_before_anything_else():
    """There is no point discussing the shape of a number that is not really
    about this player. Four of Manuel's twenty-three have two matches behind
    them: those projections are their clubs' averages wearing their names."""
    said = describe_pick(appearances=2, availability=0.61, owned_percent=8.0)
    assert said["evidence"] == "thin"
    assert said["verdict"].startswith("a bet")
    assert "club's average" in said["verdict"]


def test_a_measured_regular_is_called_safe():
    said = describe_pick(
        appearances=60, availability=0.9, volatility=1.0, owned_percent=5.0
    )
    assert said["evidence"] == "measured"
    assert said["verdict"].startswith("safe")


def test_a_man_who_is_often_not_in_the_side_is_flagged_however_well_measured():
    """§10.3(i) charges -1 for a round he does not play, and no amount of
    evidence about his good days makes that go away."""
    said = describe_pick(appearances=80, availability=0.4, volatility=1.0)
    assert said["role"] == "fringe"
    assert "-1" in said["verdict"]


def test_swinging_returns_are_a_warning_about_the_armband_not_the_signing():
    """§10.3(l) doubles the captain, so the spread matters far more for that
    one choice than for owning him at all."""
    said = describe_pick(appearances=60, availability=0.9, volatility=2.8)
    assert said["swing"] == "swingy"
    assert "captain" in said["verdict"]


# --------------------------------------------------------------------------
# Where it puts you against the field
# --------------------------------------------------------------------------


def test_a_player_nobody_owns_moves_you_either_way():
    said = describe_pick(
        appearances=60, availability=0.9, volatility=1.0, owned_percent=0.4
    )
    assert said["field"] == "differential"
    assert "either way" in said["verdict"]


def test_a_player_everybody_owns_mostly_keeps_you_level():
    said = describe_pick(
        appearances=60, availability=0.9, volatility=1.0, owned_percent=40.0
    )
    assert said["field"] == "template"
    assert "keeps you level" in said["verdict"]


def test_being_a_differential_is_not_advertised_when_the_number_is_a_guess():
    """Otherwise the two axes collapse: "nobody owns him" reads as an edge, and
    on two matches of evidence it is not an edge, it is a coin."""
    said = describe_pick(appearances=2, owned_percent=0.2)
    assert said["field"] == "differential"
    assert "either way" not in said["verdict"]


# --------------------------------------------------------------------------
# Working with what there is
# --------------------------------------------------------------------------


def test_the_parts_that_are_unknown_are_left_unsaid():
    """Ownership, availability and spread all go missing for someone. A
    verdict that invented them would be worse than a shorter one."""
    said = describe_pick(appearances=40)
    assert said["role"] is None
    assert said["swing"] is None
    assert said["field"] is None
    assert said["verdict"]


def test_the_cuts_are_the_markets_own_percentiles():
    """Measured over 498 players and two reconstructed seasons: the median
    player with any history has 30 matches; the median is owned by 0.9% of
    teams and the ninetieth percentile by 11.9%; the spread of a player's
    round-to-round points has quartiles at 1.17 and 2.20; and appearances have
    quartiles at 0.64 and 0.88 with a median of 0.76.

    The availability cut was 0.75 first, which is the median — so half the
    league fell on the wrong side of it by a hundredth and eleven of a
    twenty-three-man squad came out with one label. A threshold sitting on the
    median divides nothing.

    Pinned so that moving a threshold means re-measuring, not re-deciding.
    """
    assert (EVIDENCE_MEASURED, EVIDENCE_THIN) == (30, 15)
    assert (AVAILABILITY_REGULAR, AVAILABILITY_FRINGE) == (0.85, 0.65)
    # Neither cut may sit on the median, which is what went wrong the first time.
    assert AVAILABILITY_FRINGE < 0.76 < AVAILABILITY_REGULAR
    assert (VOLATILITY_STEADY, VOLATILITY_SWINGY) == (1.2, 2.2)
    assert (OWNED_TEMPLATE, OWNED_DIFFERENTIAL) == (10.0, 1.0)


def test_the_boundaries_fall_on_the_generous_side():
    """Exactly at a cut counts as the better label, so a player is never
    downgraded by a rounding error."""
    assert describe_pick(appearances=EVIDENCE_MEASURED)["evidence"] == "measured"
    assert describe_pick(appearances=EVIDENCE_THIN)["evidence"] == "partial"
    assert (
        describe_pick(appearances=40, availability=AVAILABILITY_REGULAR)["role"]
        == "regular"
    )
