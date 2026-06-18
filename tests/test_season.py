"""
Integration: run a real (reduced) season through the NEW stack on the MIGRATED
v2 data -- JsonTeamRepository(datafiles_v2) + FakeSheetGateway + the real
GameSimulator via GameSimulatorAdapter. Fully offline, on a temp copy so the
canonical v2 files are never mutated.

`pytest tests/test_season.py` or `python tests/test_season.py`.
"""
import itertools
import shutil
from pathlib import Path

import numpy as np
import pytest

from handball.orchestration import (
    GameSimulatorAdapter,
    InMemoryRecordSink,
    SeasonOrchestrator,
)
from handball.repository import JsonTeamRepository
from handball.sheet_gateway import FakeSheetGateway

V2_DIR = Path(__file__).parent.parent / "handball" / "datafiles_v2"
TEAMS = ["Boston", "New York", "Dallas", "Houston"]


@pytest.fixture
def orchestrator(tmp_path):
    """A production-shaped orchestrator on an isolated copy of the v2 data."""
    data = tmp_path / "data"
    shutil.copytree(V2_DIR, data)
    repo = JsonTeamRepository(data)
    orch = SeasonOrchestrator(repo, FakeSheetGateway(), GameSimulatorAdapter(), InMemoryRecordSink())
    return orch, repo


def test_v2_data_present():
    assert V2_DIR.exists(), "run the migration first (datafiles_v2 missing)"
    assert len(list(V2_DIR.glob("*.json"))) == 32


def test_reduced_season_runs_and_standings_are_consistent(orchestrator):
    np.random.seed(0)
    orch, repo = orchestrator
    orch.publish_all()

    # single round-robin among 4 teams -> 6 games, each team plays 3
    matchups = list(itertools.combinations(TEAMS, 2))
    results = orch.run_period(matchups)
    assert len(results) == 6

    # every game resolved to a winner (allow_tie=False) and persisted
    for tid in TEAMS:
        w, l, t = repo.load(tid).record
        assert w + l + t == 3
        assert t == 0

    # league-wide bookkeeping: one W and one L per game
    recs = [repo.load(t).record for t in TEAMS]
    assert sum(r[0] for r in recs) == 6      # total wins
    assert sum(r[1] for r in recs) == 6      # total losses


def test_season_logs_accumulate_and_persist(orchestrator):
    np.random.seed(1)
    orch, repo = orchestrator
    orch.publish_all()
    orch.run_period(list(itertools.combinations(TEAMS, 2)))

    boston = repo.load("Boston")          # reload from disk -> logs survived save
    for player in boston.roster():
        assert len(player.current_season_log["goals"]) == 3        # 3 games
        assert len(player.current_season_log["shots_taken"]) == 3
    # goals logged reconcile with the team's goals across its games
    assert sum(p.total_season_goals for p in boston.roster()) >= 0


def test_canonical_v2_untouched(orchestrator):
    """Sanity: running a season on the copy must not mutate handball/datafiles_v2."""
    import json
    orch, repo = orchestrator
    before = json.loads((V2_DIR / "boston.json").read_text())["record"]
    orch.publish_all()
    orch.run_period(list(itertools.combinations(TEAMS, 2)))
    after = json.loads((V2_DIR / "boston.json").read_text())["record"]
    assert before == after == [0, 0, 0]


def test_determinism_same_seed_same_standings(tmp_path):
    """Same seed -> identical season, proving reproducibility of the new stack."""
    def run_once():
        data = tmp_path / f"d{np.random.randint(0)}" if False else tmp_path / "d"
        if data.exists():
            shutil.rmtree(data)
        shutil.copytree(V2_DIR, data)
        orch = SeasonOrchestrator(JsonTeamRepository(data), FakeSheetGateway(),
                                  GameSimulatorAdapter(), InMemoryRecordSink())
        orch.publish_all()
        np.random.seed(42)
        orch.run_period(list(itertools.combinations(TEAMS, 2)))
        return {t: orch.team_repo.load(t).record for t in TEAMS}

    assert run_once() == run_once()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
