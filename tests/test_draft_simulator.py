"""
Name: test_draft_simulator.py
Description: Tests for draft_simulator random stat assignment (no name model).
Author: AI Assistant
Date: 6/17/2026
"""
import pytest

from handball.draft_simulator import (
    POSITIONS,
    assign_random_position,
    create_draft_player,
    player_generation,
    generate_draft_class,
    load_prospect_names,
)
from handball.players import Player


class TestPositionAssignment:
    def test_position_is_valid(self):
        for _ in range(100):
            assert assign_random_position() in POSITIONS

    def test_position_weights_respected(self):
        for _ in range(20):
            assert assign_random_position({"Goalie": 1.0}) == "Goalie"


class TestCreateDraftPlayer:
    def test_returns_player(self):
        p = create_draft_player("Rookie One", "Forward")
        assert isinstance(p, Player)
        assert p.name == "Rookie One"
        assert p.position == "Forward"

    def test_new_player_defaults(self):
        p = create_draft_player("Rookie Two", "Midfielder")
        assert p.years_in_league == 0
        assert p.rookie_contract is True
        assert 18 <= p.age <= 23

    def test_stats_within_bounds(self):
        for position in POSITIONS:
            p = create_draft_player(f"{position} Guy", position)
            assert 0.0 <= p.offense <= 10.0
            assert 0.0 <= p.defense <= 10.0
            assert 0.0 <= p.goalie_skill <= 10.0

    def test_goalie_stats_shape(self):
        p = create_draft_player("Net Minder", "Goalie")
        # Goalies carry skill in goalie_skill; offense/defense are nominal
        assert p.offense == pytest.approx(0.1)
        assert p.defense == pytest.approx(0.1)
        assert p.goalie_skill > 0.1

    def test_invalid_position_raises(self):
        with pytest.raises(ValueError):
            create_draft_player("Bad Pos", "Striker")


class TestPlayerGeneration:
    def test_parallel_lists(self):
        names = ["A", "B", "C"]
        positions = ["Forward", "Defense", "Goalie"]
        players = player_generation(names, positions)
        assert len(players) == 3
        assert [p.position for p in players] == positions
        assert [p.name for p in players] == names

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            player_generation(["A", "B"], ["Forward"])


class TestGenerateDraftClass:
    def test_count_matches_names(self):
        names = [f"Prospect {i}" for i in range(25)]
        players = generate_draft_class(names)
        assert len(players) == 25
        assert all(isinstance(p, Player) for p in players)
        assert all(p.position in POSITIONS for p in players)

    def test_explicit_positions_used(self):
        names = ["A", "B"]
        players = generate_draft_class(names, position_list=["Goalie", "Goalie"])
        assert all(p.position == "Goalie" for p in players)

    def test_seed_is_reproducible(self):
        names = [f"P{i}" for i in range(10)]
        first = generate_draft_class(names, seed=123)
        second = generate_draft_class(names, seed=123)
        assert [(p.position, p.offense, p.defense, p.goalie_skill) for p in first] == \
               [(p.position, p.offense, p.defense, p.goalie_skill) for p in second]

    def test_overall_centered_near_mean(self):
        # Overall skill is sampled from N(5, 1.5); averaged over a large
        # forward class, the offense/defense midpoint should sit near 5.
        names = [f"P{i}" for i in range(300)]
        positions = ["Forward"] * 300
        players = player_generation(names, positions)
        overalls = [(p.offense + p.defense) / 2 for p in players]
        assert 3.5 < sum(overalls) / len(overalls) < 6.5
        assert all(0 <= p.offense <= 10 and 0 <= p.defense <= 10 for p in players)


class TestLoadProspectNames:
    def test_txt_one_per_line(self, tmp_path):
        f = tmp_path / "names.txt"
        f.write_text("Alice\nBob\n\nCharlie\n")
        prospects = load_prospect_names(str(f))
        assert [n for n, _ in prospects] == ["Alice", "Bob", "Charlie"]
        assert all(pos is None for _, pos in prospects)

    def test_csv_with_position(self, tmp_path):
        f = tmp_path / "names.csv"
        f.write_text("Name,Position\nAlice,Forward\nBob,\n")
        prospects = load_prospect_names(str(f))
        assert prospects == [("Alice", "Forward"), ("Bob", None)]

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_prospect_names("/no/such/file.txt")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
