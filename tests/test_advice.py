"""What every player is worth right now, computed in exactly one place.

This valuation existed inside the squad proposal and was about to be written a
second time inside the dashboard. Two copies would not have failed; they would
have quietly disagreed, and the page would have recommended one transfer while
the script recommended another, with nothing to say which was the model's
opinion. That is the failure these tests exist to make impossible.

The rest guard the two mistakes that were actually made while building it, both
of which produced a squad that looked reasonable and was not.
"""

from __future__ import annotations

from liga_record_mcp.advice import MIN_POOL_APPEARANCES, valuation
from liga_record_mcp.models import Player, Position
from liga_record_mcp.stats import UNUSED_PENALTY


def player(identifier, position=Position.MID, club="FC Porto", value=1_000_000):
    return Player(
        id=identifier,
        name=identifier,
        position=position,
        club=club,
        value=value,
        initial_value=value,
    )


def record(played, points, available=None, each=None):
    entry = {"played": played, "points": points, "available": available or played}
    if each:
        entry["each"] = each
    return entry


def nothing(players):
    return {i: record(0, 0.0, 0) for i in players}


# --------------------------------------------------------------------------
# The mistakes that were made
# --------------------------------------------------------------------------


def test_a_player_is_not_shrunk_toward_himself():
    """The three-goalkeepers bug, as it actually happened.

    Three keepers at one club, one good afternoon between them, and every one
    of them shrunk toward a group he was the only member of. The proposal
    bought all three at the floor price and sold Diogo Costa.

    The league around them is what a real market supplies, and it is the point:
    the fix is that a man's prior comes from everyone except him, at every
    level — his club, then his position, then the league.
    """
    squad = {
        "lucky": player("lucky", Position.GK, "Nacional"),
        "mate": player("mate", Position.GK, "Nacional"),
        "third": player("third", Position.GK, "Nacional"),
    }
    # An ordinary league of keepers returning about two a game.
    for n in range(12):
        squad[f"gk{n}"] = player(f"gk{n}", Position.GK, f"Club {n}")
    archive = {i: record(40, 80.0) for i in squad}
    archive["lucky"] = record(1, 30.0)
    archive["mate"] = record(0, 0.0, 0)
    archive["third"] = record(0, 0.0, 0)

    view = valuation(squad, archive, nothing(squad))
    assert view["lucky"]["returns"] < 5.0
    # And the two who never played must not inherit his afternoon either.
    assert view["mate"]["returns"] < 3.0
    assert view["third"]["returns"] < 3.0


def test_a_teammate_does_not_inherit_one_loud_afternoon():
    """A player with one appearance moved the group as much as one with
    seventy, so a single good day became the club's expected return and every
    team-mate inherited it."""
    squad = {
        "loud": player("loud", Position.MID, "Arouca"),
        "quiet": player("quiet", Position.MID, "Arouca"),
        "steady": player("steady", Position.MID, "Arouca"),
    }
    archive = {
        "loud": record(1, 30.0),
        "quiet": record(40, 80.0),
        "steady": record(40, 80.0),
    }
    view = valuation(squad, archive, nothing(squad))
    # His team-mates average two a game between them; one loud afternoon
    # elsewhere must not drag them upward.
    assert view["quiet"]["returns"] < 3.0
    assert view["steady"]["returns"] < 3.0


def test_a_club_with_too_little_history_falls_back_to_the_position():
    """A promoted club, or a position nobody there has played in the top
    flight. "We do not know" is the position's average, not a guess built from
    one man's fortnight."""
    squad = {
        "new": player("new", Position.FWD, "Marítimo"),
        "old": player("old", Position.FWD, "Benfica"),
    }
    archive = {"new": record(0, 0.0, 0), "old": record(60, 300.0)}
    view = valuation(squad, archive, nothing(squad))
    assert view["new"]["appearances"] == 0
    assert view["new"]["returns"] == view["new"]["prior"]
    assert view["new"]["label"] == "bet"


def test_the_pool_needs_a_real_sample_before_it_beats_the_position():
    squad = {
        "thin": player("thin", Position.DEF, "Alverca"),
        "mate": player("mate", Position.DEF, "Alverca"),
        "other": player("other", Position.DEF, "Benfica"),
    }
    archive = {
        "thin": record(0, 0.0, 0),
        "mate": record(MIN_POOL_APPEARANCES - 1, 90.0),
        "other": record(60, 120.0),
    }
    view = valuation(squad, archive, nothing(squad))
    # Nine appearances at ten a game is not enough to make a club's defenders
    # ten-point defenders.
    assert view["thin"]["returns"] < 6.0


# --------------------------------------------------------------------------
# The arithmetic it is meant to do
# --------------------------------------------------------------------------


def test_the_two_halves_multiply_out_to_the_estimate():
    """The whole point of keeping them apart is that they can be checked."""
    squad = {"a": player("a")}
    view = valuation(squad, {"a": record(50, 200.0, 60)}, nothing(squad))["a"]
    assert view["expected"] == (
        view["playing"] * view["returns"]
        + (1 - view["playing"]) * float(UNUSED_PENALTY)
    )


def test_a_man_who_never_plays_is_valued_near_the_penalty():
    """§10.3(i) is what owning him costs, and it is close to what he is worth."""
    squad = {"a": player("a"), "b": player("b")}
    archive = {"a": record(0, 0.0, 60), "b": record(50, 200.0, 60)}
    view = valuation(squad, archive, nothing(squad))
    assert view["a"]["playing"] < 0.3
    assert view["a"]["expected"] < 1.0


def test_this_season_moves_the_chance_of_playing_faster_than_the_archive():
    """Being in the side is news; a team sheet from two years ago is not."""
    squad = {"a": player("a")}
    archive = {"a": record(60, 240.0, 68)}
    dropped = valuation(squad, archive, {"a": record(0, 0.0, 6)})["a"]
    kept = valuation(squad, archive, {"a": record(6, 24.0, 6)})["a"]
    assert dropped["playing"] < kept["playing"] - 0.2


def test_the_spread_needs_enough_rounds_to_mean_anything():
    squad = {"a": player("a"), "b": player("b")}
    archive = {
        "a": record(30, 120.0, each=[4.0] * 30),
        "b": record(30, 120.0, each=[4.0] * 5),
    }
    view = valuation(squad, archive, nothing(squad))
    assert view["a"]["spread"] is not None
    assert view["b"]["spread"] is None


def test_ownership_rides_along_when_it_is_known_and_is_optional():
    squad = {"a": player("a"), "b": player("b")}
    archive = {i: record(60, 240.0) for i in squad}
    view = valuation(squad, archive, nothing(squad), owned={"a": 45.0})
    assert view["a"]["field_label"] == "template"
    assert view["b"]["field_label"] == ""


def test_a_player_nobody_has_a_record_for_does_not_break_it():
    """Half the market has never played a top-flight match, and the valuation
    has to have an opinion about all of them."""
    squad = {"a": player("a"), "b": player("b")}
    view = valuation(squad, {}, {})
    assert set(view) == {"a", "b"}
    assert all(entry["appearances"] == 0 for entry in view.values())
