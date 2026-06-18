"""
Tests for schedule_generator.py to verify league schedule and rival requirements.
"""

import json
from pathlib import Path

import pytest

from handball.schedule_generator import (
    ScheduleGenerator,
    league,
    load_or_create_rivals,
    validate_schedule,
    MatchupType,
)


def _all_teams() -> list[str]:
    teams: list[str] = []
    for divisions in league.values():
        for d_teams in divisions.values():
            teams.extend(d_teams)
    return teams


def test_rivals_persist_and_are_stable(tmp_path, monkeypatch):
    """
    Rivals should be assigned once and then loaded from JSON on subsequent calls,
    regardless of the seed passed in.
    """
    # Redirect rivals JSON to a temp location to avoid touching real datafiles
    rivals_path = tmp_path / "rivals.json"

    from handball import schedule_generator as sg_mod

    monkeypatch.setattr(sg_mod, "RIVALS_JSON_PATH", str(rivals_path))

    # First call creates rivals JSON with seed=1
    rivals1 = load_or_create_rivals(league, seed=1, path=str(rivals_path))
    assert rivals_path.exists()

    # Second call with different seed should ignore the seed and return same mapping
    rivals2 = load_or_create_rivals(league, seed=999, path=str(rivals_path))
    assert rivals1 == rivals2

    # File contents should match mapping
    with rivals_path.open("r") as f:
        stored = json.load(f)
    assert stored == rivals1


def test_rivals_structure():
    """
    Every team has exactly one rival, in the same division, and the relation is symmetric.
    """
    gen = ScheduleGenerator()
    rivals = gen.rivals
    teams = _all_teams()

    # Every team present and has a rival
    assert set(rivals.keys()) == set(teams)

    # Symmetry and same-division checks
    team_to_div: dict[str, str] = {}
    for conf_name, divisions in league.items():
        for div_name, div_teams in divisions.items():
            for t in div_teams:
                team_to_div[t] = f"{conf_name}:{div_name}"

    for team, rival in rivals.items():
        assert rivals[rival] == team  # symmetric
        assert team_to_div[team] == team_to_div[rival]  # same division


def test_schedule_validation_passes():
    """
    Generated schedule must pass all validation rules:
    - 50 games per team
    - correct counts vs rival / division / conference / inter-conference opponents
    - symmetric matchups
    """
    gen = ScheduleGenerator()
    tuple_schedule: dict[str, list[tuple[str, int, MatchupType]]] = {}
    for team, entries in gen.schedule.items():
        tuple_schedule[team] = [
            (str(e["opponent"]), int(e["games"]), str(e["matchup_type"]))  # type: ignore[arg-type]
            for e in entries
        ]

    # Raises AssertionError on failure
    validate_schedule(tuple_schedule, gen.rivals)


def test_each_team_plays_once_per_week():
    """
    In each constructed week, every team appears in at most one game.
    Also, total number of games equals 50 * 32 / 2 = 800.
    """
    gen = ScheduleGenerator()
    teams = _all_teams()

    total_games = 0
    for week_index, week in enumerate(gen.weeks, start=1):
        seen: set[str] = set()
        for g in week:
            a = str(g["team1"])
            b = str(g["team2"])
            assert a != b
            assert a not in seen, f"Team {a} appears more than once in week {week_index}"
            assert b not in seen, f"Team {b} appears more than once in week {week_index}"
            seen.add(a)
            seen.add(b)
            total_games += 1

        # All teams may not play every single week, but none should appear twice.
        assert seen.issubset(set(teams))

    # 32 teams * 50 games each / 2 teams per game = 800 total games
    assert total_games == 800


def test_each_team_has_50_games_and_plays_every_week():
    """
    Ensure each team:
    - plays exactly 50 games in total, and
    - has at most one game in any given week, and
    - with 55 total weeks, has exactly 5 byes (i.e. plays in 50 distinct weeks).
    """
    gen = ScheduleGenerator()
    teams = _all_teams()

    games_per_team: dict[str, int] = {t: 0 for t in teams}
    weeks_played: dict[str, set[int]] = {t: set() for t in teams}

    for week_index, week in enumerate(gen.weeks, start=1):
        for g in week:
            a = str(g["team1"])
            b = str(g["team2"])
            games_per_team[a] += 1
            games_per_team[b] += 1
            weeks_played[a].add(week_index)
            weeks_played[b].add(week_index)

    num_weeks = len(gen.weeks)
    assert num_weeks == 55, f"Expected 55 weeks, got {num_weeks}"

    for team in teams:
        # total games
        assert games_per_team[team] == 50, f"{team} should have 50 games"
        # A team should never be scheduled more than once in the same week
        assert len(weeks_played[team]) == games_per_team[team]
        # and must therefore play in exactly 50 of the 55 weeks (5 byes)
        assert len(weeks_played[team]) == 50, f"{team} should play in 50 distinct weeks"



def test_evaluate_week_and_str_output_consistent():
    """
    evaluate_week(n) should align with the textual representation from __str__.
    """
    gen = ScheduleGenerator()

    # Week 1 should have some games
    week1 = gen.evaluate_week(1)
    assert week1

    # A week beyond the last should be empty
    empty_week = gen.evaluate_week(len(gen.weeks) + 1)
    assert empty_week == []

    # __str__ should contain markers for the first and last week
    s = str(gen)
    assert "=== Week 1 ===" in s
    assert f"=== Week {len(gen.weeks)} ===" in s

    # For week 1, ensure each printed game matches a real matchup
    lines = s.splitlines()
    week1_lines = []
    in_week1 = False
    for line in lines:
        if line.startswith("=== Week 1 ==="):
            in_week1 = True
            continue
        if in_week1:
            if not line.strip():
                break
            week1_lines.append(line.strip())

    # Convert textual lines back to (team1, team2, type) and check membership
    printed_games = set()
    for line in week1_lines:
        # Format: "Team1 vs Team2 (type)"
        parts = line.split(" vs ")
        assert len(parts) == 2
        team1 = parts[0].strip()
        opp_part = parts[1]
        team2, type_part = opp_part.split("(", 1)
        team2 = team2.strip()
        mtype = type_part.strip(") ").strip()
        printed_games.add((team1, team2, mtype))

    actual_games = {
        (str(g["team1"]), str(g["team2"]), str(g["matchup_type"]))
        for g in week1
    }

    assert printed_games == actual_games

