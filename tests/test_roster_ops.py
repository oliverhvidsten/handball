"""
Runnable proof of (b): validate() + Team.apply_arrangement(), and the full
offline cycle wiring them to the gateway from (c):

  model -> public_view -> publish -> manager edit -> read_arrangement
        -> validate -> apply_arrangement -> model changed -> republish

`pytest tests/test_roster_ops.py` or `python tests/test_roster_ops.py`.
"""
import pytest

from handball.domain import ArrangementError, Player, Team, validate
from handball.league_views import DEFAULT_RULES, TeamArrangement
from handball.sheet_gateway import FakeSheetGateway


def _team() -> Team:
    def p(pid, name, pos, injured=False):
        return Player(id=pid, name=name, position=pos, is_injured=injured,
                      contract_term=2, contract_value=8)

    return Team(
        id="Boston", name="Boston Foxes",
        coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "Ada", "Forward"), p("f2", "Ben", "Forward"), p("f3", "Cy", "Forward")],
            "Midfielder": [p("m1", "Dee", "Midfielder"), p("m2", "Eli", "Midfielder"), p("m3", "Fin", "Midfielder")],
            "Defense": [p("d1", "Gus", "Defense"), p("d2", "Hal", "Defense"), p("d3", "Ike", "Defense")],
            "Goalie": [p("g1", "Jo", "Goalie")],
        },
        bench={
            "Forward": [p("f4", "Kai", "Forward"), p("f5", "Lou", "Forward")],
            "Midfielder": [p("m4", "Mo", "Midfielder"), p("m5", "Ned", "Midfielder")],
            "Defense": [p("d4", "Oz", "Defense"), p("d5", "Pat", "Defense")],
            "Goalie": [p("g2", "Quin", "Goalie")],
        },
        reserves=[p("r1", "Ray", "Forward"), p("r2", "Sam", "Defense")],
        record=(10, 4, 1),
    )


def _swap_forward(base: TeamArrangement, starter_out, bench_in) -> TeamArrangement:
    """Promote a bench forward, demote a starting forward."""
    starters_f = tuple(bench_in if x == starter_out else x for x in base.starters["Forward"])
    bench_f = tuple(starter_out if x == bench_in else x for x in base.bench["Forward"])
    return TeamArrangement(
        starters={**base.starters, "Forward": starters_f},
        bench={**base.bench, "Forward": bench_f},
        reserves=base.reserves,
    )


def test_identity_arrangement_is_valid():
    team = _team()
    validate(team.arrangement(), team)  # must not raise


def test_legal_swap_applies():
    team = _team()
    edited = _swap_forward(team.arrangement(), "f3", "f4")
    team.apply_arrangement(edited)
    assert team.arrangement().starters["Forward"] == ("f1", "f2", "f4")
    assert "f3" in team.arrangement().bench["Forward"]


def test_public_view_hides_secret_stats():
    team = _team()
    pv = team.starters["Forward"][0].public_view()
    assert not hasattr(pv, "max_offense")
    assert not hasattr(pv, "decline_rate")


# -- each validation rule fails as designed -------------------------------

def test_wrong_count_rejected():
    team = _team()
    base = team.arrangement()
    bad = TeamArrangement(
        starters={**base.starters, "Forward": ("f1", "f2")},  # only 2
        bench=base.bench, reserves=base.reserves,
    )
    with pytest.raises(ArrangementError) as e:
        team.apply_arrangement(bad)
    assert any("Forward" in m and "expected 3" in m for m in e.value.problems)


def test_cross_position_assignment_allowed():
    """Any player may fill any slot regardless of card position (stats are
    intrinsic to the player and unaffected by the slot)."""
    team = _team()
    base = team.arrangement()
    # Swap a starting forward (f3) and a starting defender (d1) into each other's
    # position slot. Counts per position are preserved; only the rule that a
    # player must match their slot's position is being exercised.
    arr = TeamArrangement(
        starters={**base.starters, "Forward": ("f1", "f2", "d1"), "Defense": ("f3", "d2", "d3")},
        bench=base.bench,
        reserves=base.reserves,
    )
    team.apply_arrangement(arr)                  # no longer rejected
    assert team.arrangement().starters["Forward"] == ("f1", "f2", "d1")
    assert "f3" in team.arrangement().starters["Defense"]
    assert team.get("d1").position == "Defense"  # the player's card position is unchanged


def test_injured_starter_allowed():
    """The 'no injured starter' rule was dropped -- an injured player may sit in
    the starting lineup (they contribute nothing in the sim; the web editor warns
    before saving)."""
    team = _team()
    team.get("f4").is_injured = True            # bench forward gets hurt
    base = team.arrangement()
    arr = _swap_forward(base, "f3", "f4")         # manager starts him anyway
    team.apply_arrangement(arr)                   # accepted
    assert "f4" in team.arrangement().starters["Forward"]


def test_phantom_and_missing_player_rejected():
    team = _team()
    base = team.arrangement()
    bad = TeamArrangement(
        starters={**base.starters, "Forward": ("f1", "f2", "zzz")},  # phantom; drops f3
        bench=base.bench, reserves=base.reserves,
    )
    with pytest.raises(ArrangementError) as e:
        team.apply_arrangement(bad)
    assert any("unknown player" in m for m in e.value.problems)
    assert any("not placed anywhere" in m for m in e.value.problems)


def test_failed_validation_leaves_team_unchanged():
    team = _team()
    before = team.arrangement()
    bad = TeamArrangement(starters={**before.starters, "Forward": ("f1",)},
                          bench=before.bench, reserves=before.reserves)
    with pytest.raises(ArrangementError):
        team.apply_arrangement(bad)
    assert team.arrangement() == before  # atomic: no partial mutation


# -- full loop: model <-> gateway <-> manager -----------------------------

def test_full_offline_cycle():
    team = _team()
    gw = FakeSheetGateway()

    gw.publish(team.public_view())                       # outbox
    baseline = gw.read_arrangement("Boston")
    assert baseline == team.arrangement()                # no diff yet

    # Manager rearranges on the sheet between cycles.
    edited = _swap_forward(baseline, "f3", "f4")
    gw.simulate_manager_edit("Boston", edited)

    on_sheet = gw.read_arrangement("Boston")             # inbox
    if on_sheet != team.arrangement():                   # the diff = manager intent
        team.apply_arrangement(on_sheet)                 # validate + apply
    gw.publish(team.public_view())                       # republish -> new baseline

    assert gw.read_arrangement("Boston") == team.arrangement()
    assert team.starters["Forward"][2].id == "f4"
    assert gw.publish_calls == 2


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
