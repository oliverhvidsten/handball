"""
Runnable proof of the repository layer: the JSON is a SELF-CONTAINED source of
truth (arrangement + hidden stats), so a Team round-trips with zero sheet access.

`pytest tests/test_repository.py` or `python tests/test_repository.py`.
"""
import json

import pytest

from handball.domain import Player, Team
from handball.league_views import TeamArrangement
from handball.repository import (
    InMemoryTeamRepository,
    JsonTeamRepository,
    RepositoryError,
    TeamRepository,
    team_from_dict,
    team_to_dict,
)


def _team(team_id="Boston") -> Team:
    def p(pid, name, pos, **kw):
        return Player(id=pid, name=name, position=pos, contract_term=2, contract_value=8, **kw)

    return Team(
        id=team_id, name=f"{team_id} Foxes", coaches=["HC", "OC", "DC"],
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


def test_inmemory_satisfies_protocol():
    assert isinstance(InMemoryTeamRepository(), TeamRepository)


def test_dict_roundtrip_preserves_everything():
    team = _team()
    back = team_from_dict(team_to_dict(team))
    assert back == team                      # full structural equality
    assert back.arrangement() == team.arrangement()


def test_hidden_stats_persist_through_repo():
    team = _team()
    team.get("f1").max_offense = 9.4         # a hidden field
    repo = InMemoryTeamRepository()
    repo.save(team)
    assert repo.load("Boston").get("f1").max_offense == 9.4


def test_load_returns_independent_copy():
    repo = InMemoryTeamRepository()
    repo.save(_team())
    a = repo.load("Boston")
    a.get("f1").age = 99                      # mutate the loaded copy
    b = repo.load("Boston")
    assert b.get("f1").age != 99             # store is not aliased


def test_json_file_is_self_contained(tmp_path):
    """The whole point: the file holds arrangement AND stats -- no sheet needed."""
    repo = JsonTeamRepository(tmp_path)
    repo.save(_team())

    raw = json.loads((tmp_path / "boston.json").read_text())
    assert raw["arrangement"]["starters"]["Forward"] == ["f1", "f2", "f3"]
    assert raw["players"]["f1"]["max_offense"] == 7.0   # hidden stat on disk
    assert raw["schema_version"] == 2

    loaded = repo.load("Boston")
    assert loaded == _team()


def test_json_atomic_write_leaves_no_tmp(tmp_path):
    repo = JsonTeamRepository(tmp_path)
    repo.save(_team())
    assert not list(tmp_path.glob("*.tmp"))


def test_all_team_ids_and_load_all(tmp_path):
    repo = JsonTeamRepository(tmp_path)
    repo.save(_team("Boston"))
    repo.save(_team("New York"))
    assert repo.all_team_ids() == ["Boston", "New York"]
    assert {t.id for t in repo.load_all()} == {"Boston", "New York"}
    assert (tmp_path / "new_york.json").exists()   # filename normalization


def test_corrupt_arrangement_rejected_on_load():
    team = _team()
    blob = team_to_dict(team)
    blob["arrangement"]["starters"]["Forward"] = ["f1", "f2"]  # only 2 -> invalid
    with pytest.raises(RepositoryError):
        team_from_dict(blob)


def test_legacy_schema_rejected_with_guidance():
    with pytest.raises(RepositoryError) as e:
        team_from_dict({"id": "Boston", "schema_version": 1, "players": {}})
    assert "migration" in str(e.value).lower()


def test_full_loop_repo_plus_gateway(tmp_path):
    """Model lives in the repo (truth); sheet is just inbox/outbox."""
    from handball.sheet_gateway import FakeSheetGateway

    repo = JsonTeamRepository(tmp_path)
    gw = FakeSheetGateway()

    repo.save(_team())                                   # truth on disk
    team = repo.load("Boston")                           # load WITHOUT sheet
    gw.publish(team.public_view())                       # publish projection

    base = gw.read_arrangement("Boston")
    edited = TeamArrangement(
        starters={**base.starters, "Forward": ("f1", "f2", "f4")},
        bench={**base.bench, "Forward": ("f3", "f5")},
        reserves=base.reserves,
    )
    gw.simulate_manager_edit("Boston", edited)

    on_sheet = gw.read_arrangement("Boston")
    if on_sheet != team.arrangement():
        team.apply_arrangement(on_sheet)
        repo.save(team)                                  # persist new truth
        gw.publish(team.public_view())                   # republish

    assert repo.load("Boston").starters["Forward"][2].id == "f4"  # survived reload


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
