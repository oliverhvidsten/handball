"""
Proof of the cutover: the REAL GameSimulator runs directly on domain.Team via
GameSimulatorAdapter -- no sheet, no credentials. Records and per-player season
logs land on the domain model.

`pytest tests/test_game_adapter.py` or `python tests/test_game_adapter.py`.
"""
import numpy as np

from handball.domain import Player, Team
from handball.orchestration import (
    GameSimulatorAdapter,
    SeasonOrchestrator,
    InMemoryRecordSink,
)
from handball.repository import InMemoryTeamRepository
from handball.sheet_gateway import FakeSheetGateway


def _team(team_id: str) -> Team:
    def p(pid, name, pos, off=5.0, deff=5.0, gk=0.1):
        return Player(id=f"{team_id}-{pid}", name=f"{team_id} {name}", position=pos,
                      offense=off, defense=deff, goalie_skill=gk, variance=0.5)

    return Team(
        id=team_id, name=f"{team_id} FC", coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "F1", "Forward", off=7), p("f2", "F2", "Forward", off=6), p("f3", "F3", "Forward", off=6)],
            "Midfielder": [p("m1", "M1", "Midfielder"), p("m2", "M2", "Midfielder"), p("m3", "M3", "Midfielder")],
            "Defense": [p("d1", "D1", "Defense", deff=7), p("d2", "D2", "Defense", deff=7), p("d3", "D3", "Defense", deff=6)],
            "Goalie": [p("g1", "G1", "Goalie", off=0.1, deff=0.1, gk=6.0)],
        },
        bench={
            "Forward": [p("f4", "F4", "Forward"), p("f5", "F5", "Forward")],
            "Midfielder": [p("m4", "M4", "Midfielder"), p("m5", "M5", "Midfielder")],
            "Defense": [p("d4", "D4", "Defense"), p("d5", "D5", "Defense")],
            "Goalie": [p("g2", "G2", "Goalie", off=0.1, deff=0.1, gk=5.0)],
        },
        reserves=[p("r1", "R1", "Forward"), p("r2", "R2", "Defense")],
    )


def test_real_simulator_runs_on_domain_team():
    np.random.seed(7)
    home, away = _team("Boston"), _team("New York")
    result = GameSimulatorAdapter().play(home, away)

    assert result.home_id == "Boston" and result.away_id == "New York"
    assert result.home_score >= 0 and result.away_score >= 0
    assert not (result.home_score == result.away_score)   # allow_tie=False resolves in OT


def test_records_and_logs_land_on_the_model():
    np.random.seed(7)
    home, away = _team("Boston"), _team("New York")
    result = GameSimulatorAdapter().play(home, away)

    # records updated on the domain teams
    assert sum(home.record) == 1 and sum(away.record) == 1
    if result.home_score > result.away_score:
        assert home.record == (1, 0, 0) and away.record == (0, 1, 0)

    # every player got exactly one game appended to their season log
    for team in (home, away):
        for pl in team.roster():
            assert len(pl.current_season_log["goals"]) == 1
            assert len(pl.current_season_log["shots_taken"]) == 1
        # goalies got save/goals-allowed entries
        assert len(team.starters["Goalie"][0].current_season_log["saves"]) == 1
        assert len(team.bench["Goalie"][0].current_season_log["saves"]) == 1


def test_player_lines_are_complete_and_id_keyed():
    """player_lines covers every player on both teams, keyed by stable id, with
    the full stat shape the relational PostgresRecordSink persists."""
    np.random.seed(13)
    home, away = _team("Boston"), _team("New York")
    result = GameSimulatorAdapter().play(home, away)

    expected_ids = {p.id for p in home.roster()} | {p.id for p in away.roster()}
    assert set(result.player_lines) == expected_ids

    for line in result.player_lines.values():
        assert set(line) == {"goals", "shots", "saves", "goals_allowed", "performance"}

    # per-team goals in the lines reconcile with the score (mirrors the season-log
    # reconciliation, proving lines are sourced from this game's entries)
    home_line_goals = sum(result.player_lines[p.id]["goals"] for p in home.roster())
    away_line_goals = sum(result.player_lines[p.id]["goals"] for p in away.roster())
    assert home_line_goals == result.home_score
    assert away_line_goals == result.away_score

    # only goalies carry saves; the starting goalie of each team has >= 0 (and the
    # save lists were populated, unlike field players whose source list is empty)
    for team in (home, away):
        gid = team.starters["Goalie"][0].id
        assert result.player_lines[gid]["saves"] == team.starters["Goalie"][0].current_season_log["saves"][-1]


def test_goals_logged_reconcile_with_score():
    np.random.seed(13)
    home, away = _team("Boston"), _team("New York")
    result = GameSimulatorAdapter().play(home, away)

    home_goals_logged = sum(p.total_season_goals for p in home.roster())
    away_goals_logged = sum(p.total_season_goals for p in away.roster())
    assert home_goals_logged == result.home_score
    assert away_goals_logged == result.away_score


def test_full_orchestrator_with_real_engine_offline(tmp_path):
    """The whole stack: repo + gateway + REAL simulator, no Google."""
    np.random.seed(1)
    repo = InMemoryTeamRepository()
    repo.save(_team("Boston"))
    repo.save(_team("New York"))

    orch = SeasonOrchestrator(repo, FakeSheetGateway(), GameSimulatorAdapter(), InMemoryRecordSink())
    orch.publish_all()
    results = orch.run_period([("Boston", "New York")])

    assert len(results) == 1
    # records persisted through the repo
    assert sum(repo.load("Boston").record) == 1
    # season logs persisted too (survive serialize -> load)
    assert len(repo.load("Boston").starters["Forward"][0].current_season_log["goals"]) == 1
    assert len(orch.record_sink.games) == 1


def test_orchestrator_runs_without_a_gateway(tmp_path):
    """Batch-sim mode: no SheetGateway (the website owns the manager inbox). The
    period still plays, records persist, and games are recorded."""
    np.random.seed(1)
    repo = InMemoryTeamRepository()
    repo.save(_team("Boston"))
    repo.save(_team("New York"))

    orch = SeasonOrchestrator(repo, None, GameSimulatorAdapter(), InMemoryRecordSink())
    orch.publish_all()                                   # no-op, must not raise
    assert orch.apply_manager_lineup("Boston") is False  # no inbox to pull
    results = orch.run_period([("Boston", "New York")])

    assert len(results) == 1
    assert sum(repo.load("Boston").record) == 1
    assert len(orch.record_sink.games) == 1


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
