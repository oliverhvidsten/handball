"""
Runnable proof of v1 -> v2 migration, fully offline (no sheet).

`pytest tests/test_migration.py` or `python tests/test_migration.py`.
"""
import json

import pytest

from handball.migrate_v1_to_v2 import (
    DictArrangementSource,
    migrate_team,
    run_migration,
)
from handball.repository import JsonTeamRepository


# A legacy team: 21 players keyed by NAME, full (hidden) stats, NO id/arrangement.
def _legacy_players() -> dict:
    def pd(pos, **extra):
        # real-shaped player fields (incl. hidden ones) so v2 output also LOADS.
        return {"position": pos, "age": 25, "is_injured": False,
                "offense": 5.0, "defense": 5.0, "goalie_skill": 0.1,
                "max_offense": 7.0, "decline_rate": 0.15,
                "contract_term": 2, "contract_value": 8, **extra}

    names_pos = (
        [(f"Fwd {i}", "Forward") for i in range(5)]
        + [(f"Mid {i}", "Midfielder") for i in range(5)]
        + [(f"Def {i}", "Defense") for i in range(5)]
        + [("Goalie A", "Goalie"), ("Goalie B", "Goalie")]
        + [("Res 0", "Forward"), ("Res 1", "Defense"), ("Res 2", "Midfielder"), ("Res 3", "Forward")]
    )
    out = {}
    for name, pos in names_pos:
        out[name] = {"name": name, **pd(pos)}
    return out


def _arrangement() -> dict:
    return {
        "starters": {
            "Forward": ["Fwd 0", "Fwd 1", "Fwd 2"],
            "Midfielder": ["Mid 0", "Mid 1", "Mid 2"],
            "Defense": ["Def 0", "Def 1", "Def 2"],
            "Goalie": ["Goalie A"],
        },
        "bench": {
            "Forward": ["Fwd 3", "Fwd 4"],
            "Midfielder": ["Mid 3", "Mid 4"],
            "Defense": ["Def 3", "Def 4"],
            "Goalie": ["Goalie B"],
        },
        "reserves": ["Res 0", "Res 1", "Res 2", "Res 3"],
    }


def _source(arr=None) -> DictArrangementSource:
    return DictArrangementSource(
        arrangements={"Boston": arr or _arrangement()},
        metadatas={"Boston": {"name": "Boston Foxes", "coaches": ["HC", "OC", "DC"], "record": [10, 4, 1]}},
    )


def test_clean_migration_produces_valid_v2():
    v2, issues = migrate_team("Boston", _legacy_players(), _source())
    assert v2 is not None
    assert [i for i in issues if i.severity == "error"] == []
    assert v2["schema_version"] == 2
    assert v2["name"] == "Boston Foxes" and v2["record"] == [10, 4, 1]
    # arrangement is now by id, and players are keyed by id
    starter_fwd_ids = v2["arrangement"]["starters"]["Forward"]
    assert all(fid in v2["players"] for fid in starter_fwd_ids)
    assert all(v2["players"][fid]["position"] == "Forward" for fid in starter_fwd_ids)


def test_ids_are_deterministic_and_readable():
    a, _ = migrate_team("Boston", _legacy_players(), _source())
    b, _ = migrate_team("Boston", _legacy_players(), _source())
    assert a["arrangement"] == b["arrangement"]          # stable across runs
    assert "boston-" in next(iter(a["players"]))          # human-readable id


def test_hidden_stats_carried_through():
    v2, _ = migrate_team("Boston", _legacy_players(), _source())
    any_player = next(iter(v2["players"].values()))
    assert any_player["max_offense"] == 7.0               # hidden stat preserved
    assert "id" in any_player


def test_unknown_legacy_fields_preserved_verbatim():
    legacy = _legacy_players()
    legacy["Fwd 0"]["scouting_grade"] = "A+"              # a field the model lacks
    v2, _ = migrate_team("Boston", legacy, _source())     # carried, not loaded
    fwd0_id = [pid for pid, p in v2["players"].items() if p["name"] == "Fwd 0"][0]
    assert v2["players"][fwd0_id]["scouting_grade"] == "A+"  # field-agnostic carry


def test_sheet_name_not_in_legacy_is_error():
    arr = _arrangement()
    arr["starters"]["Forward"] = ["Ghost", "Fwd 1", "Fwd 2"]   # not in legacy JSON
    v2, issues = migrate_team("Boston", _legacy_players(), _source(arr))
    assert v2 is None
    assert any("Ghost" in i.message and i.severity == "error" for i in issues)


def test_orphan_legacy_player_is_error():
    arr = _arrangement()
    arr["reserves"] = ["Res 0", "Res 1", "Res 2"]   # drop "Res 3" -> orphan
    v2, issues = migrate_team("Boston", _legacy_players(), _source(arr))
    assert v2 is None
    assert any("Res 3" in i.message and "not placed" in i.message for i in issues)


def test_wrong_count_arrangement_is_error():
    arr = _arrangement()
    arr["starters"]["Forward"] = ["Fwd 0", "Fwd 1"]   # only 2 starters
    # rebalance so membership still covers everyone (Fwd 2 -> reserves slot)
    arr["reserves"] = ["Fwd 2", "Res 0", "Res 1", "Res 2"]
    # now "Res 3" is orphaned too; both errors should surface
    v2, issues = migrate_team("Boston", _legacy_players(), _source(arr))
    assert v2 is None
    assert any("expected 3" in i.message for i in issues)


def test_migrated_output_loads_through_repository(tmp_path):
    """End to end: write legacy file, migrate, load v2 via the repo -- NO sheet."""
    legacy_dir = tmp_path / "v1"
    legacy_dir.mkdir()
    (legacy_dir / "boston.json").write_text(json.dumps(_legacy_players()))

    out_dir = tmp_path / "v2"
    issues = run_migration(legacy_dir, out_dir, _source(), team_ids=["Boston"], write=True)
    assert [i for i in issues if i.severity == "error"] == []

    team = JsonTeamRepository(out_dir).load("Boston")     # loads with zero sheet access
    assert team.name == "Boston Foxes"
    assert team.record == (10, 4, 1)
    assert len(team.roster()) == 21
    assert team.get(team.arrangement().starters["Goalie"][0]).position == "Goalie"
    assert team.get(team.arrangement().starters["Forward"][0]).max_offense == 7.0  # hidden survived


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
