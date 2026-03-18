"""
Tests for handball.free_agency — FreeAgencyHandler.

JSON persistence tests use tmp_path / monkeypatch to redirect file I/O.
Sheet interactions are mocked.
"""

import json
import os
from unittest.mock import MagicMock, call, patch

import pytest

from handball.players import Player, PlayerInfo
from handball.free_agency import (
    FREE_AGENTS_JSON_PATH,
    FreeAgencyHandler,
    _POSITION_TO_SHEET_GROUP,
    _SHEET_GROUPS,
)
from handball.sheets_handler import SheetHandler


# ======================================================================
# Helpers
# ======================================================================

def _make_player(name: str, position: str = "Forward", offense: float = 5.0,
                 defense: float = 4.0, goalie_skill: float = 0.1) -> Player:
    return Player(
        name=name,
        age=25,
        years_in_league=3,
        height=72,
        weight=180,
        position=position,
        offense=offense,
        defense=defense,
        goalie_skill=goalie_skill,
        max_offense=offense + 1,
        max_defense=defense + 1,
        max_goalie_skill=goalie_skill,
        variance=0.5,
    )


def _fa_handler(tmp_json_path=None) -> FreeAgencyHandler:
    """Return a FreeAgencyHandler with mocked SheetHandler."""
    sh = MagicMock(spec=SheetHandler)
    sh.write_free_agents = MagicMock()
    sh.read_free_agents = MagicMock(return_value=[])
    handler = FreeAgencyHandler(sh)
    return handler


@pytest.fixture
def fa_json_path(tmp_path, monkeypatch):
    """Redirect FREE_AGENTS_JSON_PATH to a temp directory."""
    path = str(tmp_path / "free_agents.json")
    monkeypatch.setattr("handball.free_agency.FREE_AGENTS_JSON_PATH", path)
    return path


# ======================================================================
# Initialization
# ======================================================================

class TestInit:
    def test_starts_empty(self):
        handler = _fa_handler()
        assert handler.count == 0
        assert handler.all_players() == []


# ======================================================================
# JSON persistence — save_json / load_json
# ======================================================================

class TestJsonPersistence:
    def test_save_and_load_round_trip(self, fa_json_path):
        handler = _fa_handler()
        p1 = _make_player("Alice", "Forward")
        p2 = _make_player("Bob", "Goalie", 0.1, 0.1, 7.0)
        handler.add_player(p1, persist=False)
        handler.add_player(p2, persist=False)

        handler.save_json()
        assert os.path.exists(fa_json_path)

        handler2 = _fa_handler()
        handler2.load_json()
        assert handler2.count == 2
        assert handler2.get_player("Alice") is not None
        assert handler2.get_player("Bob") is not None
        assert handler2.get_player("Alice").position == "Forward"
        assert handler2.get_player("Bob").position == "Goalie"

    def test_load_json_missing_file(self, fa_json_path):
        handler = _fa_handler()
        handler.load_json()
        assert handler.count == 0

    def test_save_json_creates_directories(self, tmp_path, monkeypatch):
        nested = str(tmp_path / "a" / "b" / "free_agents.json")
        monkeypatch.setattr("handball.free_agency.FREE_AGENTS_JSON_PATH", nested)
        handler = _fa_handler()
        handler.add_player(_make_player("X"), persist=False)
        handler.save_json()
        assert os.path.exists(nested)

    def test_save_json_overwrites(self, fa_json_path):
        handler = _fa_handler()
        handler.add_player(_make_player("Alice"), persist=False)
        handler.save_json()

        handler.add_player(_make_player("Bob"), persist=False)
        handler.save_json()

        with open(fa_json_path, "r") as f:
            data = json.load(f)
        assert len(data) == 2

    def test_load_preserves_all_player_fields(self, fa_json_path):
        p = _make_player("DetailPlayer", "Midfielder", 6.5, 7.2)
        p.contract_term = 3
        p.contract_value = 12
        p.is_injured = True

        handler = _fa_handler()
        handler.add_player(p, persist=False)
        handler.save_json()

        handler2 = _fa_handler()
        handler2.load_json()
        loaded = handler2.get_player("DetailPlayer")
        assert loaded is not None
        assert loaded.contract_term == 3
        assert loaded.contract_value == 12
        assert loaded.is_injured is True
        assert loaded.offense == pytest.approx(6.5)

    def test_empty_pool_saves_empty_json(self, fa_json_path):
        handler = _fa_handler()
        handler.save_json()
        with open(fa_json_path, "r") as f:
            data = json.load(f)
        assert data == {}


# ======================================================================
# add_player / add_players
# ======================================================================

class TestAddPlayer:
    def test_add_player_no_persist(self):
        handler = _fa_handler()
        p = _make_player("Alice")
        handler.add_player(p, persist=False)
        assert handler.count == 1
        assert handler.get_player("Alice") is p

    def test_add_player_with_persist(self, fa_json_path):
        handler = _fa_handler()
        p = _make_player("Alice", "Forward")
        handler.add_player(p, persist=True)
        assert os.path.exists(fa_json_path)
        handler.sheet_handler.write_free_agents.assert_called()

    def test_add_duplicate_overwrites(self):
        handler = _fa_handler()
        p1 = _make_player("Alice", "Forward", offense=3.0)
        p2 = _make_player("Alice", "Forward", offense=9.0)
        handler.add_player(p1, persist=False)
        handler.add_player(p2, persist=False)
        assert handler.count == 1
        assert handler.get_player("Alice").offense == pytest.approx(9.0)

    def test_add_players_bulk(self, fa_json_path):
        handler = _fa_handler()
        players = [_make_player(f"P{i}", "Forward") for i in range(5)]
        handler.add_players(players)
        assert handler.count == 5
        assert os.path.exists(fa_json_path)

    def test_add_players_bulk_persists_once(self, fa_json_path):
        handler = _fa_handler()
        players = [
            _make_player("F1", "Forward"),
            _make_player("G1", "Goalie", 0.1, 0.1, 6.0),
        ]
        handler.add_players(players)
        # write_to_sheet is called once by add_players, which writes each group
        assert handler.sheet_handler.write_free_agents.call_count >= 1


# ======================================================================
# remove_player
# ======================================================================

class TestRemovePlayer:
    def test_remove_returns_player(self):
        handler = _fa_handler()
        p = _make_player("Alice")
        handler.add_player(p, persist=False)
        removed = handler.remove_player("Alice", persist=False)
        assert removed is p
        assert handler.count == 0

    def test_remove_nonexistent_raises(self):
        handler = _fa_handler()
        with pytest.raises(KeyError):
            handler.remove_player("Nobody")

    def test_remove_persists(self, fa_json_path):
        handler = _fa_handler()
        handler.add_player(_make_player("Alice"), persist=False)
        handler.remove_player("Alice", persist=True)
        assert os.path.exists(fa_json_path)
        with open(fa_json_path, "r") as f:
            data = json.load(f)
        assert len(data) == 0

    def test_remove_one_of_many(self):
        handler = _fa_handler()
        for name in ["Alice", "Bob", "Carol"]:
            handler.add_player(_make_player(name), persist=False)
        handler.remove_player("Bob", persist=False)
        assert handler.count == 2
        assert handler.get_player("Bob") is None
        assert handler.get_player("Alice") is not None
        assert handler.get_player("Carol") is not None


# ======================================================================
# get_player / get_players_by_position / all_players / count
# ======================================================================

class TestQueryMethods:
    def test_get_player_exists(self):
        handler = _fa_handler()
        p = _make_player("Alice", "Defense")
        handler.add_player(p, persist=False)
        assert handler.get_player("Alice") is p

    def test_get_player_missing(self):
        handler = _fa_handler()
        assert handler.get_player("Nobody") is None

    def test_get_players_by_position(self):
        handler = _fa_handler()
        handler.add_player(_make_player("F1", "Forward"), persist=False)
        handler.add_player(_make_player("F2", "Forward"), persist=False)
        handler.add_player(_make_player("M1", "Midfielder"), persist=False)
        handler.add_player(_make_player("G1", "Goalie", 0.1, 0.1, 5.0), persist=False)

        forwards = handler.get_players_by_position("Forward")
        assert len(forwards) == 2
        assert all(p.position == "Forward" for p in forwards)

        goalies = handler.get_players_by_position("Goalie")
        assert len(goalies) == 1

        defenders = handler.get_players_by_position("Defense")
        assert len(defenders) == 0

    def test_all_players(self):
        handler = _fa_handler()
        handler.add_player(_make_player("A"), persist=False)
        handler.add_player(_make_player("B"), persist=False)
        assert len(handler.all_players()) == 2

    def test_count(self):
        handler = _fa_handler()
        assert handler.count == 0
        handler.add_player(_make_player("A"), persist=False)
        assert handler.count == 1
        handler.add_player(_make_player("B"), persist=False)
        assert handler.count == 2
        handler.remove_player("A", persist=False)
        assert handler.count == 1


# ======================================================================
# Sheet I/O — write_to_sheet / load_from_sheet
# ======================================================================

class TestSheetIO:
    def test_write_to_sheet_groups_by_position(self):
        handler = _fa_handler()
        handler.add_player(_make_player("F1", "Forward"), persist=False)
        handler.add_player(_make_player("F2", "Forward"), persist=False)
        handler.add_player(_make_player("M1", "Midfielder"), persist=False)
        handler.add_player(_make_player("G1", "Goalie", 0.1, 0.1, 5.0), persist=False)

        handler.write_to_sheet()

        calls = handler.sheet_handler.write_free_agents.call_args_list
        groups_written = {c[0][1] for c in calls}
        assert "Forwards" in groups_written
        assert "Midfielders" in groups_written
        assert "Goalies" in groups_written

    def test_write_to_sheet_empty_pool_no_writes(self):
        handler = _fa_handler()
        handler.write_to_sheet()
        handler.sheet_handler.write_free_agents.assert_not_called()

    def test_write_to_sheet_passes_player_info(self):
        handler = _fa_handler()
        handler.add_player(_make_player("Alice", "Forward", 7.0, 3.0), persist=False)
        handler.write_to_sheet()

        args = handler.sheet_handler.write_free_agents.call_args[0]
        infos = args[0]
        assert len(infos) == 1
        assert isinstance(infos[0], PlayerInfo)
        assert infos[0].name == "Alice"
        assert infos[0].offense == pytest.approx(7.0)

    def test_load_from_sheet_returns_player_infos(self):
        handler = _fa_handler()
        mock_info = PlayerInfo(
            name="SheetGuy", position="Forward", age=28, contract="2/$10",
            injured=False, offense=5.0, defense=3.0, goalie_skill=0.1,
        )
        handler.sheet_handler.read_free_agents = MagicMock(
            side_effect=lambda pos: [mock_info] if pos == "Forwards" else []
        )

        result = handler.load_from_sheet()
        assert len(result) == 1
        assert result[0].name == "SheetGuy"

    def test_load_from_sheet_calls_all_groups(self):
        handler = _fa_handler()
        handler.sheet_handler.read_free_agents = MagicMock(return_value=[])
        handler.load_from_sheet()

        called_groups = [c[0][0] for c in handler.sheet_handler.read_free_agents.call_args_list]
        for group in _SHEET_GROUPS:
            assert group in called_groups

    def test_load_from_sheet_does_not_populate_players(self):
        handler = _fa_handler()
        mock_info = PlayerInfo(
            name="X", position="Forward", age=28, contract="",
            injured=False, offense=5.0, defense=3.0, goalie_skill=0.1,
        )
        handler.sheet_handler.read_free_agents = MagicMock(return_value=[mock_info])
        handler.load_from_sheet()
        assert handler.count == 0


# ======================================================================
# _write_position_to_sheet
# ======================================================================

class TestWritePositionToSheet:
    def test_writes_correct_group(self):
        handler = _fa_handler()
        handler.add_player(_make_player("F1", "Forward"), persist=False)
        handler.add_player(_make_player("M1", "Midfielder"), persist=False)

        handler._write_position_to_sheet("Forward")

        args = handler.sheet_handler.write_free_agents.call_args[0]
        group = args[1]
        assert group == "Forwards"
        infos = args[0]
        assert len(infos) == 1
        assert infos[0].name == "F1"

    def test_unknown_position_no_write(self):
        handler = _fa_handler()
        handler._write_position_to_sheet("UnknownPosition")
        handler.sheet_handler.write_free_agents.assert_not_called()

    def test_empty_position_no_write(self):
        handler = _fa_handler()
        handler.add_player(_make_player("M1", "Midfielder"), persist=False)
        handler._write_position_to_sheet("Forward")
        handler.sheet_handler.write_free_agents.assert_not_called()


# ======================================================================
# Integration — add then remove round trip
# ======================================================================

class TestIntegration:
    def test_add_remove_json_round_trip(self, fa_json_path):
        handler = _fa_handler()
        players = [
            _make_player("Alice", "Forward"),
            _make_player("Bob", "Midfielder"),
            _make_player("Carol", "Defense"),
            _make_player("Dave", "Goalie", 0.1, 0.1, 6.0),
        ]
        for p in players:
            handler.add_player(p, persist=False)
        handler.save_json()

        handler.remove_player("Bob", persist=False)
        handler.save_json()

        fresh = _fa_handler()
        fresh.load_json()
        assert fresh.count == 3
        assert fresh.get_player("Bob") is None
        assert fresh.get_player("Alice") is not None

    def test_many_players_round_trip(self, fa_json_path):
        handler = _fa_handler()
        for i in range(100):
            handler.add_player(_make_player(f"Player_{i}", "Forward"), persist=False)
        handler.save_json()

        handler2 = _fa_handler()
        handler2.load_json()
        assert handler2.count == 100

    def test_position_to_sheet_group_coverage(self):
        """Every position that players can have maps to a sheet group."""
        for pos in ("Forward", "Midfielder", "Defense", "Goalie"):
            assert pos in _POSITION_TO_SHEET_GROUP
