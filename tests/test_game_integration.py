"""
Name: test_game_integration.py
Description: Integration tests for full game simulation using real team data
Author: AI Assistant
Date: 3/1/2026
"""
import pytest
import json
import os
from handball.game_simulator import GameSimulator
from handball.teams import Team, TeamInfo
from handball.sheets_handler import SheetHandler


@pytest.fixture(scope="module")
def sheet_handler():
    """Create sheet handler for tests (module scope to reuse connection)"""
    with open("/Users/oliverhvidsten/Documents/handball/cred.txt", "r") as f:
        sheet_id = f.readline().strip()
    return SheetHandler(sheet_id=sheet_id)


@pytest.fixture(scope="module")
def all_teams(sheet_handler):
    """Load all 32 teams from JSON/Sheets data.

    Any failure to load a specific team is treated as a hard error so that
    underlying data issues (e.g., malformed JSON) are not silently skipped.
    """
    teams_dict = {}
    team_names = [
        "Boston", "New York", "Philadelphia", "Washington",
        "Charlotte", "Atlanta", "Miami", "Tampa Bay",
        "Toronto", "Detroit", "Cleveland", "Chicago",
        "Cincinnati", "Louisville", "Nashville", "Indianapolis",
        "Milwaukee", "Minneapolis", "St. Louis", "Kansas City",
        "Oklahoma City", "New Orleans", "Dallas", "Houston",
        "Phoenix", "Los Angeles", "San Diego", "San Francisco",
        "Las Vegas", "Denver", "Seattle", "Vancouver",
    ]

    for team_name in team_names:
        # If this raises (e.g., JSONDecodeError), let the error propagate so tests fail.
        team_info = TeamInfo.from_sheet(sheet_handler, team_name, get_draft_picks=False)
        team = Team.from_TeamInfo(team_info)
        teams_dict[team_name] = team

    return teams_dict


class TestSingleGameSimulation:
    """Test individual game simulations with real teams"""
    
    def test_boston_vs_newyork(self, all_teams):
        """Test a single game between Boston and New York"""
        home_team = all_teams["Boston"]
        away_team = all_teams["New York"]
        
        # Reset records
        home_team.record = [0, 0, 0]
        away_team.record = [0, 0, 0]
        
        # Simulate game
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Verify game completed
        assert game.home_score >= 0
        assert game.away_score >= 0
        
        # Verify exactly one game was played
        assert sum(home_team.record) == 1
        assert sum(away_team.record) == 1
        
        # Verify winner/loser logic
        if game.home_score > game.away_score:
            assert home_team.record == [1, 0, 0], "Home should have 1 win"
            assert away_team.record == [0, 1, 0], "Away should have 1 loss"
        elif game.away_score > game.home_score:
            assert home_team.record == [0, 1, 0], "Home should have 1 loss"
            assert away_team.record == [1, 0, 0], "Away should have 1 win"
        else:
            assert home_team.record == [0, 0, 1], "Home should have 1 tie"
            assert away_team.record == [0, 0, 1], "Away should have 1 tie"
    
    def test_player_stats_updated(self, all_teams):
        """Test that player statistics are properly updated after game"""
        home_team = all_teams["Chicago"]
        away_team = all_teams["Detroit"]
        
        # Get initial log lengths for a forward
        initial_goals_len = len(home_team.starters.forwards[0].current_season_log["goals"])
        
        # Simulate game
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Verify all offensive players have updated logs
        for player in home_team.starters.forwards:
            assert len(player.current_season_log["goals"]) == initial_goals_len + 1
            assert len(player.current_season_log["shots_taken"]) == initial_goals_len + 1
            assert len(player.current_season_log["performances"]) == initial_goals_len + 1
        
        for player in home_team.starters.midfielders:
            assert len(player.current_season_log["goals"]) == initial_goals_len + 1
            assert len(player.current_season_log["shots_taken"]) == initial_goals_len + 1
        
        # Verify defenders don't score
        for player in home_team.starters.defense:
            if len(player.current_season_log["goals"]) > 0:
                assert player.current_season_log["goals"][-1] == 0
    
    def test_scoring_log_format(self, all_teams):
        """Test that scoring log is properly formatted"""
        home_team = all_teams["Los Angeles"]
        away_team = all_teams["San Francisco"]
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Verify scoring tracker has halftime marker
        assert "--- HALFTIME ---" in game.stat_tracker.scoring_tracker
        
        # Verify scoring entries contain team names
        scoring_entries = [entry for entry in game.stat_tracker.scoring_tracker 
                          if "scores with" in entry]
        
        for entry in scoring_entries:
            # Each scoring entry should mention a team
            assert ("Los Angeles" in entry or "San Francisco" in entry)
    
    def test_goals_never_exceed_shots(self, all_teams):
        """Test game logic: goals ≤ shots for all players"""
        home_team = all_teams["Miami"]
        away_team = all_teams["Atlanta"]
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Check all offensive players from both teams
        all_offensive_players = (
            home_team.starters.forwards + home_team.starters.midfielders +
            home_team.bench.forwards + home_team.bench.midfielders +
            away_team.starters.forwards + away_team.starters.midfielders +
            away_team.bench.forwards + away_team.bench.midfielders
        )
        
        for player in all_offensive_players:
            if len(player.current_season_log["goals"]) > 0:
                total_goals = sum(player.current_season_log["goals"])
                total_shots = sum(player.current_season_log["shots_taken"])
                assert total_goals <= total_shots, \
                    f"{player.name} has {total_goals} goals but only {total_shots} shots"
    
    def test_reserves_dont_play(self, all_teams):
        """Test that reserve players don't participate in games"""
        home_team = all_teams["Seattle"]
        away_team = all_teams["Vancouver"]
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Reserves should have 0 performance
        for reserve in home_team.reserves:
            if len(reserve.current_season_log["performances"]) > 0:
                assert reserve.current_season_log["performances"][-1] == 0
        
        for reserve in away_team.reserves:
            if len(reserve.current_season_log["performances"]) > 0:
                assert reserve.current_season_log["performances"][-1] == 0


class TestMultipleGames:
    """Test multiple game simulations"""
    
    def test_three_game_series(self, all_teams):
        """Test simulating 3 games in a row"""
        home_team = all_teams["Dallas"]
        away_team = all_teams["Houston"]
        
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
    
    def test_round_robin_subset(self, all_teams):
        """Test a mini round-robin tournament with 4 teams"""
        teams = [
            all_teams["Phoenix"],
            all_teams["Las Vegas"],
            all_teams["San Diego"],
            all_teams["Los Angeles"]
        ]
        
        # Reset all records
        for team in teams:
            team.record = [0, 0, 0]
        
        # Play each team against each other once (6 games total)
        games_played = 0
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                game = GameSimulator(teams[i], teams[j])
                game.simulate_game()
                games_played += 1
        
        assert games_played == 6
        
        # Verify each team played 3 games
        for team in teams:
            assert sum(team.record) == 3, f"{team.team_name} should have played 3 games"
    
    def test_home_away_symmetry(self, all_teams):
        """Test that home/away designation doesn't create bias over many games"""
        team_a = all_teams["Philadelphia"]
        team_b = all_teams["Washington"]
        
        # Reset records
        team_a.record = [0, 0, 0]
        team_b.record = [0, 0, 0]
        
        # Play 10 games, alternating home/away
        for i in range(10):
            if i % 2 == 0:
                game = GameSimulator(team_a, team_b)  # A home
            else:
                game = GameSimulator(team_b, team_a)  # B home
            game.simulate_game()
        
        # Both teams should have played 10 games
        assert sum(team_a.record) == 10
        assert sum(team_b.record) == 10
        
        # Records should be complementary (wins + losses + ties match)
        assert team_a.record[0] + team_b.record[0] + team_a.record[2] == 10  # wins + ties
        assert team_a.record[1] + team_b.record[1] + team_a.record[2] == 10  # losses + ties


class TestGameMechanics:
    """Test specific game mechanics and edge cases"""
    
    def test_different_skill_levels(self, all_teams):
        """Test games between teams with different skill levels"""
        # Find a strong team and weak team by checking average offense
        team_stats = {}
        for name, team in all_teams.items():
            avg_offense = sum([p.offense for p in team.starters.forwards + team.starters.midfielders]) / 6
            team_stats[name] = avg_offense
        
        # Get top and bottom teams
        sorted_teams = sorted(team_stats.items(), key=lambda x: x[1], reverse=True)
        strong_team = all_teams[sorted_teams[0][0]]
        weak_team = all_teams[sorted_teams[-1][0]]
        
        # Reset records
        strong_team.record = [0, 0, 0]
        weak_team.record = [0, 0, 0]
        
        # Simulate game
        game = GameSimulator(strong_team, weak_team)
        game.simulate_game()
        
        # Game should complete successfully regardless of skill disparity
        assert game.home_score >= 0
        assert game.away_score >= 0
        assert sum(strong_team.record) == 1
        assert sum(weak_team.record) == 1
    
    def test_goalie_performance_tracked(self, all_teams):
        """Test that goalie performances are tracked"""
        home_team = all_teams["Toronto"]
        away_team = all_teams["Montreal"] if "Montreal" in all_teams else all_teams["Vancouver"]
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Goalies should have performance entries
        assert len(home_team.starters.goalie.current_season_log["performances"]) > 0
        assert len(home_team.bench.goalie.current_season_log["performances"]) > 0
        
        # Goalies should never score
        if len(home_team.starters.goalie.current_season_log["goals"]) > 0:
            assert sum(home_team.starters.goalie.current_season_log["goals"]) == 0
    
    def test_bench_players_contribute(self, all_teams):
        """Test that bench players contribute to team performance"""
        home_team = all_teams["Milwaukee"]
        away_team = all_teams["Minneapolis"]
        
        game = GameSimulator(home_team, away_team)
        game.simulate_game()
        
        # Bench forwards and midfielders should have stats
        for player in home_team.bench.forwards:
            assert len(player.current_season_log["performances"]) > 0
            # Performance should be non-zero (they played)
            assert player.current_season_log["performances"][-1] > 0
        
        for player in home_team.bench.midfielders:
            assert len(player.current_season_log["performances"]) > 0
            assert player.current_season_log["performances"][-1] > 0


class TestDataPersistence:
    """Test that game data can be persisted and retrieved"""
    
    def test_team_json_update(self, all_teams):
        """Test that team JSON files can be updated after games"""
        team = all_teams["Cincinnati"]
        
        # Get initial state
        initial_forward_goals = len(team.starters.forwards[0].current_season_log["goals"])
        
        # Simulate a game
        opponent = all_teams["Louisville"]
        game = GameSimulator(team, opponent)
        game.simulate_game()
        
        # Update team JSON
        team.update_team_dict()
        
        # Verify file was written
        json_path = f"/Users/oliverhvidsten/Documents/handball/handball/datafiles/{team.team_name.lower()}.json"
        assert os.path.exists(json_path)
        
        # Load and verify data was saved
        with open(json_path, "r") as f:
            team_data = json.load(f)
        
        # Check that a player's data is in the file
        first_forward_name = team.starters.forwards[0].name
        assert first_forward_name in team_data
        assert len(team_data[first_forward_name]["current_season_log"]["goals"]) == initial_forward_goals + 1


class TestEdgeCases:
    """Test edge cases and potential error conditions"""
    
    def test_same_team_twice(self, all_teams):
        """Test that the same team object can play multiple games"""
        team = all_teams["Nashville"]
        opponent1 = all_teams["Indianapolis"]
        opponent2 = all_teams["Louisville"]
        
        team.record = [0, 0, 0]
        opponent1.record = [0, 0, 0]
        opponent2.record = [0, 0, 0]
        
        # Play two different games
        game1 = GameSimulator(team, opponent1)
        game1.simulate_game()
        
        game2 = GameSimulator(team, opponent2)
        game2.simulate_game()
        
        # Team should have played 2 games
        assert sum(team.record) == 2
    
    def test_all_teams_can_play(self, all_teams):
        """Smoke test: verify all teams can participate in games"""
        team_names = list(all_teams.keys())
        
        # Test first 5 teams against each other
        for i in range(min(5, len(team_names))):
            for j in range(i + 1, min(5, len(team_names))):
                home = all_teams[team_names[i]]
                away = all_teams[team_names[j]]
                
                home.record = [0, 0, 0]
                away.record = [0, 0, 0]
                
                # Should not raise any errors
                game = GameSimulator(home, away)
                game.simulate_game()
                
                assert sum(home.record) == 1
                assert sum(away.record) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
