"""
Name: test_game_simulator_simple.py
Description: Simple integration tests for game simulation using real team data
Author: AI Assistant
Date: 3/1/2026
"""
import json
import os

import pytest

from handball.game_simulator import GameSimulator, GameClock
from handball.teams import Team, TeamInfo
from handball.sheets_handler import SheetHandler


class TestGameClock:
    """Test the GameClock class"""
    
    def test_clock_initialization(self):
        clock = GameClock()
        assert clock.time_left == 0
    
    def test_set_time(self):
        clock = GameClock()
        clock.set_time(1800)
        assert clock.time_left == 1800
    
    def test_decrement(self):
        clock = GameClock()
        clock.set_time(100)
        clock.decrement(10)
        assert clock.time_left == 90
    
    def test_decrement_to_zero(self):
        clock = GameClock()
        clock.set_time(10)
        clock.decrement(5)
        assert clock.time_left == 5
        with pytest.raises(ZeroDivisionError):
            clock.decrement(10)
    
    def test_time_to_str(self):
        assert GameClock.time_to_str(0) == "00:00"
        assert GameClock.time_to_str(65) == "01:05"
        assert GameClock.time_to_str(1800) == "30:00"
        assert GameClock.time_to_str(3659) == "60:59"

    def test_decrement_past_zero_clamps(self):
        """Overshooting sets time_left to 0 then raises."""
        clock = GameClock()
        clock.set_time(5)
        with pytest.raises(ZeroDivisionError):
            clock.decrement(999)
        assert clock.time_left == 0

    def test_set_time_resets(self):
        """Calling set_time twice resets the clock."""
        clock = GameClock()
        clock.set_time(500)
        clock.decrement(100)
        clock.set_time(200)
        assert clock.time_left == 200

    def test_time_to_str_float_input(self):
        assert GameClock.time_to_str(90.7) == "01:30"


@pytest.mark.skipif(
    not os.path.exists("/Users/oliverhvidsten/Documents/handball/handball/datafiles/boston.json"),
    reason="Requires initialized team data"
)
class TestGameSimulatorIntegration:
    """Integration tests using real team data from JSON files"""
    
    @pytest.fixture
    def sheet_handler(self):
        """Create sheet handler for tests"""
        with open("/Users/oliverhvidsten/Documents/handball/cred.txt", "r") as f:
            sheet_id = f.readline().strip()
        return SheetHandler(sheet_id=sheet_id)
    
    @pytest.fixture
    def real_teams(self, sheet_handler):
        """Load two real teams from the system"""
        # Load Boston and New York as test teams
        boston_info = TeamInfo.from_sheet(sheet_handler, "Boston", get_draft_picks=False)
        newyork_info = TeamInfo.from_sheet(sheet_handler, "New York", get_draft_picks=False)

        boston_team = Team.from_TeamInfo(boston_info)
        newyork_team = Team.from_TeamInfo(newyork_info)

        return boston_team, newyork_team
    
    def test_game_simulation_with_real_teams(self, real_teams):
        """Test that a full game simulation runs with real team data"""
        home_team, away_team = real_teams
        
        initial_home_record = home_team.record.copy()
        initial_away_record = away_team.record.copy()
        
        # Simulate game
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Verify game completed
        assert game.home_score >= 0
        assert game.away_score >= 0
        
        # Verify records updated
        assert sum(home_team.record) == sum(initial_home_record) + 1
        assert sum(away_team.record) == sum(initial_away_record) + 1
        
        # Verify winner/loser recorded correctly
        if game.home_score > game.away_score:
            assert home_team.record[0] == initial_home_record[0] + 1  # Home win
            assert away_team.record[1] == initial_away_record[1] + 1  # Away loss
        elif game.away_score > game.home_score:
            assert away_team.record[0] == initial_away_record[0] + 1  # Away win
            assert home_team.record[1] == initial_home_record[1] + 1  # Home loss
        else:
            assert home_team.record[2] == initial_home_record[2] + 1  # Home tie
            assert away_team.record[2] == initial_away_record[2] + 1  # Away tie
    
    def test_player_stats_updated_after_game(self, real_teams):
        """Test that player statistics are updated after game"""
        home_team, away_team = real_teams
        
        # Get initial log lengths
        initial_forward_goals_len = len(home_team.starters.forwards[0].current_season_log["goals"])
        
        # Simulate game
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Verify all starters have updated logs
        for player in home_team.starters.forwards:
            assert len(player.current_season_log["goals"]) == initial_forward_goals_len + 1
            assert len(player.current_season_log["shots_taken"]) == initial_forward_goals_len + 1
            assert len(player.current_season_log["performances"]) == initial_forward_goals_len + 1
    
    def test_multiple_games_in_sequence(self, real_teams):
        """Test that multiple games can be simulated in sequence"""
        home_team, away_team = real_teams
        
        # Reset records
        home_team.record = [0, 0, 0]
        away_team.record = [0, 0, 0]
        
        # Simulate 3 games
        for i in range(3):
            game = GameSimulator(home_team, away_team)
            game.simulate_game()
        
        # Verify 3 games were played
        assert sum(home_team.record) == 3
        assert sum(away_team.record) == 3
        
        # Verify player logs have 3 entries
        assert len(home_team.starters.forwards[0].current_season_log["goals"]) >= 3
    
    def test_scoring_log_created(self, real_teams):
        """Test that a scoring log is created during game"""
        home_team, away_team = real_teams
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Verify scoring tracker exists and has halftime marker
        assert hasattr(game.stat_tracker, 'scoring_tracker')
        assert "--- HALFTIME ---" in game.stat_tracker.scoring_tracker
    
    def test_goals_never_exceed_shots(self, real_teams):
        """Test that no player scores more goals than shots taken"""
        home_team, away_team = real_teams
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Check all offensive players
        all_offensive_players = (
            home_team.starters.forwards + 
            home_team.starters.midfielders +
            home_team.bench.forwards +
            home_team.bench.midfielders +
            away_team.starters.forwards +
            away_team.starters.midfielders +
            away_team.bench.forwards +
            away_team.bench.midfielders
        )
        
        for player in all_offensive_players:
            if len(player.current_season_log["goals"]) > 0:
                total_goals = sum(player.current_season_log["goals"])
                total_shots = sum(player.current_season_log["shots_taken"])
                assert total_goals <= total_shots, \
                    f"{player.name} has {total_goals} goals but only {total_shots} shots"
    
    def test_defenders_never_score(self, real_teams):
        """Test that defenders and goalies never score"""
        home_team, away_team = real_teams
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Check defenders
        all_defenders = (
            home_team.starters.defense +
            home_team.bench.defense +
            away_team.starters.defense +
            away_team.bench.defense
        )
        
        for defender in all_defenders:
            if len(defender.current_season_log["goals"]) > 0:
                assert sum(defender.current_season_log["goals"]) == 0, \
                    f"Defender {defender.name} scored goals!"
        
        # Check goalies
        all_goalies = [
            home_team.starters.goalie,
            home_team.bench.goalie,
            away_team.starters.goalie,
            away_team.bench.goalie
        ]
        
        for goalie in all_goalies:
            if len(goalie.current_season_log["goals"]) > 0:
                assert sum(goalie.current_season_log["goals"]) == 0, \
                    f"Goalie {goalie.name} scored goals!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
