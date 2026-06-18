"""
Runnable proof of the offline mailbox flow:

  publish -> read baseline (no diff)
          -> manager edits -> read reflects edit (diff detected)
          -> republish     -> baseline resets, edit cleared

Runs with zero Google dependencies / no gs_key.json. `pytest tests/test_sheet_gateway.py`
or `python tests/test_sheet_gateway.py`.
"""
from handball.league_views import (
    DEFAULT_RULES,
    PlayerPublicView,
    TeamArrangement,
    TeamPublicView,
)
from handball.sheet_gateway import FakeSheetGateway, SheetGateway


def _p(pid, name, pos, off=5.0, deff=5.0, gk=0.1):
    return PlayerPublicView(
        id=pid, name=name, position=pos, age=25, contract="2/$8M",
        injured=False, offense=off, defense=deff, goalie_skill=gk,
    )


def _sample_view() -> TeamPublicView:
    return TeamPublicView(
        id="Boston",
        name="Boston Foxes",
        coaches=["Coach HC", "Coach OC", "Coach DC"],
        starters={
            "Forward": [_p("f1", "Ada", "Forward"), _p("f2", "Ben", "Forward"), _p("f3", "Cy", "Forward")],
            "Midfielder": [_p("m1", "Dee", "Midfielder"), _p("m2", "Eli", "Midfielder"), _p("m3", "Fin", "Midfielder")],
            "Defense": [_p("d1", "Gus", "Defense"), _p("d2", "Hal", "Defense"), _p("d3", "Ike", "Defense")],
            "Goalie": [_p("g1", "Jo", "Goalie", gk=7.0)],
        },
        bench={
            "Forward": [_p("f4", "Kai", "Forward"), _p("f5", "Lou", "Forward")],
            "Midfielder": [_p("m4", "Mo", "Midfielder"), _p("m5", "Ned", "Midfielder")],
            "Defense": [_p("d4", "Oz", "Defense"), _p("d5", "Pat", "Defense")],
            "Goalie": [_p("g2", "Quin", "Goalie", gk=6.0)],
        },
        reserves=[_p("r1", "Ray", "Forward"), _p("r2", "Sam", "Defense")],
        record=(10, 4, 1),
        total_salaries=120,
    )


def test_fake_satisfies_protocol():
    assert isinstance(FakeSheetGateway(), SheetGateway)


def test_publish_then_read_is_baseline_no_diff():
    gw = FakeSheetGateway()
    view = _sample_view()
    gw.publish(view)

    baseline = TeamArrangement.from_public_view(view)
    on_sheet = gw.read_arrangement("Boston")

    # No manager edit -> read equals baseline -> service does nothing.
    assert on_sheet == baseline


def test_manager_edit_is_detected_as_a_diff():
    gw = FakeSheetGateway()
    view = _sample_view()
    gw.publish(view)
    baseline = TeamArrangement.from_public_view(view)

    # Manager promotes bench forward "Kai" (f4) into the starting lineup,
    # demoting "Cy" (f3) to the bench.
    edited = TeamArrangement(
        starters={**baseline.starters, "Forward": ("f1", "f2", "f4")},
        bench={**baseline.bench, "Forward": ("f3", "f5")},
        reserves=baseline.reserves,
    )
    gw.simulate_manager_edit("Boston", edited)

    on_sheet = gw.read_arrangement("Boston")
    assert on_sheet != baseline          # diff detected => manager intent
    assert on_sheet == edited
    assert on_sheet.starters["Forward"] == ("f1", "f2", "f4")


def test_republish_resets_baseline_and_clears_edit():
    gw = FakeSheetGateway()
    view = _sample_view()
    gw.publish(view)

    edited = TeamArrangement(
        starters={**TeamArrangement.from_public_view(view).starters, "Forward": ("f1", "f2", "f4")},
        bench={**TeamArrangement.from_public_view(view).bench, "Forward": ("f3", "f5")},
        reserves=TeamArrangement.from_public_view(view).reserves,
    )
    gw.simulate_manager_edit("Boston", edited)
    assert gw.read_arrangement("Boston") == edited

    # After the service applies the edit to the model and republishes, the
    # sheet baseline reflects the new canonical state and the pending edit clears.
    new_view = _sample_view()  # stand-in for the model after apply_arrangement
    gw.publish(new_view)
    assert gw.read_arrangement("Boston") == TeamArrangement.from_public_view(new_view)


def test_reserve_max_and_rules_available():
    # Roster shape lives in one config object, not scattered magic numbers.
    assert DEFAULT_RULES.starter_caps["Forward"] == 3
    assert DEFAULT_RULES.max_roster == 21


if __name__ == "__main__":
    test_fake_satisfies_protocol()
    test_publish_then_read_is_baseline_no_diff()
    test_manager_edit_is_detected_as_a_diff()
    test_republish_resets_baseline_and_clears_edit()
    test_reserve_max_and_rules_available()
    print("offline sheet-gateway flow: all checks passed")
