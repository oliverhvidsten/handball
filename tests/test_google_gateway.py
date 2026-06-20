"""
Offline tests for GoogleSheetGateway and SheetArrangementSource against a fake
SheetHandler that mimics the real TEAM_RANGE grid (A3:F32). These exercise the
exact code the LIVE migration runs -- they would have caught the
SheetHandler(team_id) construction bug.

`pytest tests/test_google_gateway.py` or `python tests/test_google_gateway.py`.
"""
import copy

from handball.domain import Player, Team
from handball.league_views import PlayerPublicView
from handball.migrate_v1_to_v2 import SheetArrangementSource
from handball.sheet_gateway import GoogleSheetGateway


class FakeSheetHandler:
    """Mimics the SheetHandler surface the gateway/source use: one workbook,
    teams addressed by tab name. Stores a 2D grid per tab."""

    def __init__(self):
        self.grids: dict[str, list[list]] = {}

        self.free_agents = {}  # position group -> list passed to write_free_agents

    def get_full_team_values(self, team_name):
        return copy.deepcopy(self.grids.get(team_name, []))

    def update_full_team_values(self, team_name, edited_data):
        self.grids[team_name] = copy.deepcopy(edited_data)

    def write_free_agents(self, free_agents_list, position):
        self.free_agents[position] = list(free_agents_list)


def _team() -> Team:
    def p(pid, name, pos, **kw):
        return Player(id=f"B-{pid}", name=name, position=pos, **kw)

    return Team(
        id="Boston", name="Boston Foxes", coaches=["Hank", "Olga", "Dex"],
        starters={
            "Forward": [p("f1", "Ada", "Forward"), p("f2", "Ben", "Forward"), p("f3", "Cy", "Forward")],
            "Midfielder": [p("m1", "Dee", "Midfielder"), p("m2", "Eli", "Midfielder"), p("m3", "Fin", "Midfielder")],
            "Defense": [p("d1", "Gus", "Defense"), p("d2", "Hal", "Defense"), p("d3", "Ike", "Defense")],
            "Goalie": [p("g1", "Jo", "Goalie", goalie_skill=7.0)],
        },
        bench={
            "Forward": [p("f4", "Kai", "Forward"), p("f5", "Lou", "Forward")],
            "Midfielder": [p("m4", "Mo", "Midfielder"), p("m5", "Ned", "Midfielder")],
            "Defense": [p("d4", "Oz", "Defense"), p("d5", "Pat", "Defense")],
            "Goalie": [p("g2", "Quin", "Goalie", goalie_skill=6.0)],
        },
        reserves=[p("r1", "Ray", "Forward"), p("r2", "Sam", "Defense")],
        record=(10, 4, 1),
    )


def test_publish_writes_expected_cells():
    h = FakeSheetHandler()
    gw = GoogleSheetGateway(h)
    gw.publish(_team().public_view())

    grid = h.grids["Boston"]
    assert grid[5][1] == "Ada"        # starter forward row
    assert grid[14][1] == "Jo"        # starter goalie row
    assert grid[17][1] == "Kai"       # bench forward row
    assert grid[26][1] == "Ray"       # first reserve row
    assert grid[0][2] == "Hank"       # head coach (col 2; col 0 is the label)
    assert grid[0][5] == "10-4-1"     # record cell


def test_read_arrangement_roundtrips_after_publish():
    h = FakeSheetHandler()
    gw = GoogleSheetGateway(h)
    team = _team()
    gw.publish(team.public_view())

    on_sheet = gw.read_arrangement("Boston")
    assert on_sheet == team.arrangement()      # baseline, names resolved to ids


def test_manager_edit_on_grid_is_read_back():
    h = FakeSheetHandler()
    gw = GoogleSheetGateway(h)
    team = _team()
    gw.publish(team.public_view())

    # Simulate a manager swapping starter F3 (row 7) with bench F4 (row 17).
    h.grids["Boston"][7][1], h.grids["Boston"][17][1] = (
        h.grids["Boston"][17][1], h.grids["Boston"][7][1])

    on_sheet = gw.read_arrangement("Boston")
    assert on_sheet != team.arrangement()
    assert on_sheet.starters["Forward"] == ("B-f1", "B-f2", "B-f4")
    assert on_sheet.bench["Forward"] == ("B-f3", "B-f5")


def test_arrangement_source_parses_same_layout():
    # Populate the workbook via the gateway, then read it with the migration's
    # SheetArrangementSource (shares SheetLayout) -- cross-validates the mapping.
    h = FakeSheetHandler()
    GoogleSheetGateway(h).publish(_team().public_view())

    src = SheetArrangementSource(h)
    arr = src.arrangement("Boston")
    meta = src.metadata("Boston")

    assert arr["starters"]["Forward"] == ["Ada", "Ben", "Cy"]
    assert arr["bench"]["Goalie"] == ["Quin"]
    # The source returns raw cells incl. trailing empties; migrate_team filters them.
    assert [n for n in arr["reserves"] if n] == ["Ray", "Sam"]
    assert meta["coaches"] == ["Hank", "Olga", "Dex"]
    assert meta["record"] == [10, 4, 1]


def _fa(name, pos):
    return PlayerPublicView(
        id=f"fa-{name}", name=name, position=pos, age=27, contract="1/$2M",
        injured=False, offense=4.0, defense=4.0, goalie_skill=0.1,
    )


def test_publish_free_agents_groups_by_position_with_plural_keys():
    h = FakeSheetHandler()
    gw = GoogleSheetGateway(h)
    gw.publish_free_agents([
        _fa("Ada", "Forward"),
        _fa("Ben", "Midfielder"),
        _fa("Cy", "Defense"),
        _fa("Dee", "Goalie"),
        _fa("Eli", "Forward"),
    ])

    # Singular domain positions map to the Free Agents tab's plural column groups.
    assert [p.name for p in h.free_agents["Forwards"]] == ["Ada", "Eli"]
    assert [p.name for p in h.free_agents["Midfielders"]] == ["Ben"]
    assert [p.name for p in h.free_agents["Defenders"]] == ["Cy"]
    assert [p.name for p in h.free_agents["Goalies"]] == ["Dee"]


def test_publish_free_agents_skips_empty_groups():
    h = FakeSheetHandler()
    GoogleSheetGateway(h).publish_free_agents([_fa("Ada", "Forward")])
    # Only the non-empty group is written -- no zero-length ranges sent.
    assert list(h.free_agents) == ["Forwards"]


def test_publish_free_agents_rejects_unknown_position():
    h = FakeSheetHandler()
    import pytest
    with pytest.raises(ValueError):
        GoogleSheetGateway(h).publish_free_agents([_fa("Ada", "Winger")])


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
