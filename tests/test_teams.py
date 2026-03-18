"""
Name: test_teams.py
Description: Holds unit tests for functions in teams.py
Author: Oliver Hvidsten
Date: 8/3/2025 8:11PM PST
"""
import json
from pathlib import Path
from unittest.mock import patch, mock_open, MagicMock
from copy import deepcopy

import pytest
import numpy as np

from handball.players import Player, PlayerInfo, InjuryReport
from handball.teams import TeamInfo, Team, Subroster, SheetHandler


# ======================================================================
# Helpers — synthetic player / team factories (offline, no sheets needed)
# ======================================================================

def _make_player(name, position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1):
    return Player(
        name=name, age=25, years_in_league=3, height=72, weight=180,
        position=position, offense=offense, defense=defense,
        goalie_skill=goalie_skill, max_offense=offense + 1,
        max_defense=defense + 1, max_goalie_skill=goalie_skill,
        variance=0.5,
    )


def _make_player_info(name, position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1):
    return PlayerInfo(
        name=name, position=position, age=25, contract="3/$10",
        injured=False, offense=offense, defense=defense, goalie_skill=goalie_skill,
    )


def _make_subroster(prefix, is_bench=False):
    """Build a Subroster with 3 forwards, 3 middies, 3 defense, 1 goalie (starters) or 2/2/2/1 (bench)."""
    n = 2 if is_bench else 3
    return Subroster(
        forwards=[_make_player(f"{prefix}_F{i}", "Forward") for i in range(n)],
        midfielders=[_make_player(f"{prefix}_M{i}", "Midfielder") for i in range(n)],
        defense=[_make_player(f"{prefix}_D{i}", "Defense") for i in range(n)],
        goalie=_make_player(f"{prefix}_G", "Goalie", 0.1, 0.1, 7.0),
    )


def _make_full_team(name, draft_picks=None):
    if draft_picks is None:
        draft_picks = {"1st Round": [f"{name} 2026"], "2nd Round": []}
    return Team(
        team_name=name,
        starters=_make_subroster(f"{name}_S"),
        bench=_make_subroster(f"{name}_B", is_bench=True),
        reserves=[_make_player(f"{name}_R{i}", "Forward") for i in range(4)],
        draft_picks=draft_picks,
        record=[0, 0, 0],
    )


def _make_team_info(name):
    """Build a synthetic TeamInfo object with PlayerInfo objects (no sheet needed)."""
    return TeamInfo(
        team_name=name,
        coaches=["HC", "OC", "DC"],
        starters={
            "Forwards": [_make_player_info(f"{name}_SF{i}", "Forward") for i in range(3)],
            "Midfielders": [_make_player_info(f"{name}_SM{i}", "Midfielder") for i in range(3)],
            "Defense": [_make_player_info(f"{name}_SD{i}", "Defense") for i in range(3)],
            "Goalie": _make_player_info(f"{name}_SG", "Goalie", 0.1, 0.1, 7.0),
        },
        bench={
            "Forwards": [_make_player_info(f"{name}_BF{i}", "Forward") for i in range(2)],
            "Midfielders": [_make_player_info(f"{name}_BM{i}", "Midfielder") for i in range(2)],
            "Defense": [_make_player_info(f"{name}_BD{i}", "Defense") for i in range(2)],
            "Goalie": _make_player_info(f"{name}_BG", "Goalie", 0.1, 0.1, 5.0),
        },
        reserve=[_make_player_info(f"{name}_R{i}", "Forward") for i in range(4)],
        draft_picks={"1st Round": [f"{name} 2026"], "2nd Round": []},
        record=[5, 3, 1],
        total_salaries=42,
        raw_data=(None, None, {}),
    )


# ======================================================================
# Team — win / lose / tie
# ======================================================================

class TestTeamRecordTracking:
    def test_win_increments(self):
        team = _make_full_team("T")
        team.win()
        assert team.record == [1, 0, 0]

    def test_lose_increments(self):
        team = _make_full_team("T")
        team.lose()
        assert team.record == [0, 1, 0]

    def test_tie_increments(self):
        team = _make_full_team("T")
        team.tie()
        assert team.record == [0, 0, 1]

    def test_multiple_records(self):
        team = _make_full_team("T")
        team.win()
        team.win()
        team.lose()
        team.tie()
        assert team.record == [2, 1, 1]


# ======================================================================
# Subroster — update_team_dict
# ======================================================================

class TestSubroster:
    def test_update_team_dict_starters(self):
        sub = _make_subroster("X")
        d = {}
        sub.update_team_dict(d)
        assert sub.forwards[0].name in d
        assert sub.midfielders[0].name in d
        assert sub.defense[0].name in d
        assert sub.goalie.name in d
        total_expected = 3 + 3 + 3 + 1
        assert len(d) == total_expected

    def test_update_team_dict_bench(self):
        sub = _make_subroster("Y", is_bench=True)
        d = {}
        sub.update_team_dict(d)
        total_expected = 2 + 2 + 2 + 1
        assert len(d) == total_expected

    def test_update_team_dict_values_are_serializable(self):
        sub = _make_subroster("Z")
        d = {}
        sub.update_team_dict(d)
        json_str = json.dumps(d)
        assert isinstance(json_str, str)

    def test_from_teaminfo_round_trip(self):
        """Subroster.from_TeamInfo -> update_team_dict should recreate same player names."""
        team = _make_full_team("RT")
        d = {}
        team.starters.update_team_dict(d)
        starters_info = {
            "Forwards": [PlayerInfo.from_Player(p) for p in team.starters.forwards],
            "Midfielders": [PlayerInfo.from_Player(p) for p in team.starters.midfielders],
            "Defense": [PlayerInfo.from_Player(p) for p in team.starters.defense],
            "Goalie": PlayerInfo.from_Player(team.starters.goalie),
        }
        rebuilt = Subroster.from_TeamInfo(starters_info, d)
        assert rebuilt.forwards[0].name == team.starters.forwards[0].name
        assert rebuilt.goalie.name == team.starters.goalie.name


# ======================================================================
# Team — update_performances
# ======================================================================

class TestTeamUpdatePerformances:
    def test_performances_assigned_to_correct_players(self):
        team = _make_full_team("P")
        perfs = list(range(17))
        team.update_performances(perfs)

        assert team.starters.forwards[0].current_season_log["performances"][-1] == 0
        assert team.starters.forwards[2].current_season_log["performances"][-1] == 2
        assert team.starters.midfielders[0].current_season_log["performances"][-1] == 3
        assert team.starters.defense[2].current_season_log["performances"][-1] == 8
        assert team.bench.forwards[0].current_season_log["performances"][-1] == 9
        assert team.bench.midfielders[1].current_season_log["performances"][-1] == 12
        assert team.bench.defense[1].current_season_log["performances"][-1] == 14
        assert team.starters.goalie.current_season_log["performances"][-1] == 15
        assert team.bench.goalie.current_season_log["performances"][-1] == 16

    def test_reserves_get_zero_performance(self):
        team = _make_full_team("P")
        perfs = [1.0] * 17
        team.update_performances(perfs)
        for r in team.reserves:
            assert r.current_season_log["performances"][-1] == 0


# ======================================================================
# Team — update_offensive_stats
# ======================================================================

class TestTeamUpdateOffensiveStats:
    def test_goals_and_shots_assigned_correctly(self):
        team = _make_full_team("O")
        goals = [1, 0, 2, 0, 1, 0, 1, 0, 0, 1]
        shots = [3, 1, 4, 2, 3, 1, 2, 1, 0, 2]
        team.update_offensive_stats(goals, shots)

        assert team.starters.forwards[0].current_season_log["goals"][-1] == 1
        assert team.starters.forwards[0].current_season_log["shots_taken"][-1] == 3
        assert team.starters.midfielders[1].current_season_log["goals"][-1] == 1
        assert team.bench.forwards[0].current_season_log["goals"][-1] == 1
        assert team.bench.midfielders[1].current_season_log["goals"][-1] == 1

    def test_defenders_get_zero_goals(self):
        team = _make_full_team("O")
        goals = [0] * 10
        shots = [0] * 10
        team.update_offensive_stats(goals, shots)

        for d in team.starters.defense:
            assert d.current_season_log["goals"][-1] == 0
        for d in team.bench.defense:
            assert d.current_season_log["goals"][-1] == 0

    def test_goalies_get_zero_goals(self):
        team = _make_full_team("O")
        goals = [0] * 10
        shots = [0] * 10
        team.update_offensive_stats(goals, shots)
        assert team.starters.goalie.current_season_log["goals"][-1] == 0
        assert team.bench.goalie.current_season_log["goals"][-1] == 0

    def test_reserves_get_zero_goals(self):
        team = _make_full_team("O")
        goals = [0] * 10
        shots = [0] * 10
        team.update_offensive_stats(goals, shots)
        for r in team.reserves:
            assert r.current_season_log["goals"][-1] == 0

    def test_multiple_games_accumulate(self):
        team = _make_full_team("O")
        for _ in range(3):
            goals = [1] * 10
            shots = [2] * 10
            team.update_offensive_stats(goals, shots)
        assert len(team.starters.forwards[0].current_season_log["goals"]) == 3
        assert sum(team.starters.forwards[0].current_season_log["goals"]) == 3


# ======================================================================
# Team — update_team_JSON (mocked file write)
# ======================================================================

class TestTeamUpdateJSON:
    @patch("builtins.open", new_callable=mock_open)
    @patch("json.dump")
    def test_update_team_json_writes_all_players(self, mock_json_dump, mock_file):
        team = _make_full_team("JSONTest")
        team.update_team_JSON()

        mock_file.assert_called_once()
        mock_json_dump.assert_called_once()
        written_dict = mock_json_dump.call_args[0][0]

        total_players = 3 + 3 + 3 + 1 + 2 + 2 + 2 + 1 + 4
        assert len(written_dict) == total_players

    @patch("builtins.open", new_callable=mock_open)
    @patch("json.dump")
    def test_update_team_json_numpy_scalars_safe(self, mock_json_dump, mock_file):
        team = _make_full_team("NP")
        team.starters.forwards[0].offense = np.float64(7.5)
        team.update_team_JSON()

        written_dict = mock_json_dump.call_args[0][0]
        p_data = written_dict[team.starters.forwards[0].name]
        assert isinstance(p_data["offense"], float)

    @patch("builtins.open", new_callable=mock_open)
    @patch("json.dump")
    def test_update_team_dict_alias(self, mock_json_dump, mock_file):
        """update_team_dict is a backward-compat alias for update_team_JSON."""
        team = _make_full_team("Alias")
        team.update_team_dict()
        mock_json_dump.assert_called_once()


# ======================================================================
# Team — from_TeamInfo with mocked JSON
# ======================================================================

class TestTeamFromTeamInfo:
    def _team_dict_for(self, team_info):
        """Build a minimal team_dict from TeamInfo PlayerInfo objects."""
        d = {}
        for group in team_info.starters.values():
            items = group if isinstance(group, list) else [group]
            for pi in items:
                d[pi.name] = _make_player(pi.name, pi.position, pi.offense, pi.defense, pi.goalie_skill).to_dict()
        for group in team_info.bench.values():
            items = group if isinstance(group, list) else [group]
            for pi in items:
                d[pi.name] = _make_player(pi.name, pi.position, pi.offense, pi.defense, pi.goalie_skill).to_dict()
        for pi in team_info.reserve:
            d[pi.name] = _make_player(pi.name, pi.position, pi.offense, pi.defense, pi.goalie_skill).to_dict()
        return d

    def test_from_teaminfo_offline(self):
        ti = _make_team_info("OfflineTest")
        td = self._team_dict_for(ti)
        json_bytes = json.dumps(td)
        with patch("builtins.open", mock_open(read_data=json_bytes)):
            team = Team.from_TeamInfo(ti)
        assert team.team_name == "OfflineTest"
        assert len(team.starters.forwards) == 3
        assert len(team.bench.forwards) == 2
        assert len(team.reserves) == 4
        assert team.record == [5, 3, 1]

    def test_from_teaminfo_missing_json_non_example_raises(self):
        ti = _make_team_info("FakeCity")
        with pytest.raises(FileNotFoundError, match="FakeCity"):
            Team.from_TeamInfo(ti)

    def test_from_teaminfo_preserves_draft_picks(self):
        ti = _make_team_info("DPTest")
        td = self._team_dict_for(ti)
        json_bytes = json.dumps(td)
        with patch("builtins.open", mock_open(read_data=json_bytes)):
            team = Team.from_TeamInfo(ti)
        assert team.draft_picks == ti.draft_picks

    def test_from_teaminfo_none_draft_picks(self):
        ti = _make_team_info("NDP")
        ti.draft_picks = None
        td = self._team_dict_for(ti)
        json_bytes = json.dumps(td)
        with patch("builtins.open", mock_open(read_data=json_bytes)):
            team = Team.from_TeamInfo(ti)
        assert team.draft_picks is None


# ======================================================================
# TeamInfo — get_updates_from_Team
# ======================================================================

class TestTeamInfoGetUpdates:
    def test_record_syncs(self):
        ti = _make_team_info("SyncTest")
        team = _make_full_team("SyncTest")
        team.record = [10, 5, 2]
        ti.get_updates_from_Team(team)
        assert ti.record == [10, 5, 2]

    def test_starter_stats_sync(self):
        ti = _make_team_info("StatSync")
        team = _make_full_team("StatSync")
        team.starters.forwards[0].offense = 9.9
        team.starters.forwards[0].age = 30
        ti.get_updates_from_Team(team)
        assert ti.starters["Forwards"][0].offense == pytest.approx(9.9)
        assert ti.starters["Forwards"][0].age == 30

    def test_bench_stats_sync(self):
        ti = _make_team_info("BenchSync")
        team = _make_full_team("BenchSync")
        team.bench.forwards[0].offense = 8.1
        team.bench.forwards[0].age = 22
        ti.get_updates_from_Team(team)
        assert ti.bench["Forwards"][0].offense == pytest.approx(8.1)
        assert ti.bench["Forwards"][0].age == 22

    def test_starter_not_overwritten_by_bench(self):
        """After the fix, bench sync should not clobber starter values."""
        ti = _make_team_info("NoClobber")
        team = _make_full_team("NoClobber")
        team.starters.forwards[0].offense = 9.5
        team.bench.forwards[0].offense = 3.0
        ti.get_updates_from_Team(team)
        assert ti.starters["Forwards"][0].offense == pytest.approx(9.5)
        assert ti.bench["Forwards"][0].offense == pytest.approx(3.0)

    def test_goalie_sync_separate(self):
        """Starter and bench goalies should update independently."""
        ti = _make_team_info("GoalieSync")
        team = _make_full_team("GoalieSync")
        team.starters.goalie.goalie_skill = 8.8
        team.bench.goalie.goalie_skill = 4.2
        ti.get_updates_from_Team(team)
        assert ti.starters["Goalie"].goalie_skill == pytest.approx(8.8)
        assert ti.bench["Goalie"].goalie_skill == pytest.approx(4.2)

    def test_reserves_sync(self):
        ti = _make_team_info("ResSync")
        team = _make_full_team("ResSync")
        team.reserves[0].offense = 1.1
        team.reserves[0].age = 19
        ti.get_updates_from_Team(team)
        assert ti.reserve[0].offense == pytest.approx(1.1)
        assert ti.reserve[0].age == 19


# ======================================================================
# TeamInfo — update_sheet (mocked SheetHandler)
# ======================================================================

class TestTeamInfoUpdateSheet:
    def test_update_sheet_calls_handler(self):
        ti = _make_team_info("SheetTest")
        raw_team_info = [[""] * 7 for _ in range(30)]
        raw_team_info[0][5] = "5-3-1"
        raw_team_info[1][5] = "$42M"
        ti.raw_data = (raw_team_info, [], {})

        mock_handler = MagicMock()
        ti.update_sheet(mock_handler, update_draft_picks=False)
        mock_handler.update_full_team_values.assert_called_once()
        mock_handler.update_player_notes.assert_called_once()
        mock_handler.update_draft_picks.assert_not_called()

    def test_update_sheet_with_draft_picks(self):
        ti = _make_team_info("DraftSheet")
        raw_team_info = [[""] * 7 for _ in range(30)]
        raw_team_info[0][5] = "5-3-1"
        raw_team_info[1][5] = "$42M"
        ti.raw_data = (raw_team_info, [["", ""] for _ in range(5)], {})

        mock_handler = MagicMock()
        ti.update_sheet(mock_handler, update_draft_picks=True)
        mock_handler.update_draft_picks.assert_called_once()


# ======================================================================
# Sheet-dependent tests (original tests, kept for integration)
# ======================================================================

def test_load_team_from_sheet():
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    with cred_path.open("r") as f:
        line = f.readline()

    sheet_handler = SheetHandler(sheet_id=line)
    team_info = TeamInfo.from_sheet(sheet_handler=sheet_handler, team_name="EXAMPLE", get_draft_picks=False)
    team_obj = Team.from_TeamInfo(team_info=team_info)

    assert team_obj.team_name == "EXAMPLE"
    assert isinstance(team_obj.starters, Subroster)
    assert isinstance(team_obj.bench, Subroster)
    assert isinstance(team_obj.reserves, list)


def test_load_teaminfo_from_sheet():
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    with cred_path.open("r") as f:
        line = f.readline()

    sheet_handler = SheetHandler(sheet_id=line)
    team_info = TeamInfo.from_sheet(sheet_handler=sheet_handler, team_name="EXAMPLE", get_draft_picks=False)

    assert (team_info.team_name == "EXAMPLE" and
            team_info.coaches == ["HC Name", "OC Name", "DC Name"] and
            len(team_info.starters) == 4 and
            len(team_info.bench) == 4 and
            len(team_info.reserve) == 4 and
            team_info.record == [0, 0, 0] and
            team_info.total_salaries == 8)


@pytest.mark.parametrize("team_name", ["Boston", "San Francisco"])
def test_real_team_json_can_be_loaded(team_name):
    datafile_path = Path("/Users/oliverhvidsten/Documents/handball/handball/datafiles") / f"{team_name.lower()}.json"
    assert datafile_path.exists(), f"Expected team datafile for {team_name} at {datafile_path}"

    with datafile_path.open("r") as f:
        team_dict = json.load(f)

    assert isinstance(team_dict, dict) and team_dict, "Team JSON should be a non-empty dict"

    for i, (name, pdata) in enumerate(team_dict.items()):
        player = Player.from_dict(pdata)
        assert player.name == name
        if i >= 4:
            break


@pytest.mark.parametrize("team_name", ["Boston", "San Francisco"])
def test_real_team_can_be_loaded_from_sheet(team_name):
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    assert cred_path.exists(), f"Missing credentials file at {cred_path}"
    with cred_path.open("r") as f:
        sheet_id = f.readline().strip()

    sheet_handler = SheetHandler(sheet_id=sheet_id)
    team_info = TeamInfo.from_sheet(sheet_handler=sheet_handler, team_name=team_name, get_draft_picks=False)

    assert team_info.team_name == team_name
    assert isinstance(team_info.coaches, list) and len(team_info.coaches) == 3
    assert isinstance(team_info.starters, dict)
    assert isinstance(team_info.bench, dict)
    assert isinstance(team_info.reserve, list)


@pytest.mark.parametrize("team_name", ["Boston", "San Francisco"])
def test_real_teaminfo_and_team_round_trip(team_name):
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    assert cred_path.exists(), f"Missing credentials file at {cred_path}"
    with cred_path.open("r") as f:
        sheet_id = f.readline().strip()

    sheet_handler = SheetHandler(sheet_id=sheet_id)
    team_info = TeamInfo.from_sheet(sheet_handler=sheet_handler, team_name=team_name, get_draft_picks=False)
    team = Team.from_TeamInfo(team_info)

    assert team.team_name == team_name
    assert isinstance(team.starters, Subroster)
    assert isinstance(team.bench, Subroster)
    assert isinstance(team.reserves, list)
    assert isinstance(team.record, list) and len(team.record) == 3

