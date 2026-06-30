"""
Offline integration for the LeagueOperations facade: the single composition root
driven through a full mini-season lifecycle (publish -> run period with injuries
-> standings -> draft -> playoffs) on InMemoryTeamRepository + FakeSheetGateway +
SimpleGameEngine. No sheet, no OR-Tools, no archive.

`pytest tests/test_league.py` or `python tests/test_league.py`.
"""
import random

import numpy as np
import pytest

from handball.domain import Player, Team
from handball.injury_simulator import InjurySimulator
from handball.league import LeagueOperations
from handball.orchestration import InMemoryRecordSink, SeasonOrchestrator, SimpleGameEngine
from handball.repository import InMemoryTeamRepository
from handball.season import Schedule
from handball.sheet_gateway import FakeSheetGateway


def _team(tid, strength, risk=0.0):
    def p(pid, pos, off=5.0, gk=0.1):
        return Player(id=pid, name=pid, position=pos, offense=off, goalie_skill=gk, injury_risk=risk)
    return Team(
        id=tid, name=tid, coaches=[],
        starters={
            "Forward": [p(f"{tid}-sf{i}", "Forward", off=strength) for i in range(3)],
            "Midfielder": [p(f"{tid}-sm{i}", "Midfielder", off=strength) for i in range(3)],
            "Defense": [p(f"{tid}-sd{i}", "Defense") for i in range(3)],
            "Goalie": [p(f"{tid}-sg", "Goalie", gk=3.0)],
        },
        bench={
            "Forward": [p(f"{tid}-bf{i}", "Forward") for i in range(2)],
            "Midfielder": [p(f"{tid}-bm{i}", "Midfielder") for i in range(2)],
            "Defense": [p(f"{tid}-bd{i}", "Defense") for i in range(2)],
            "Goalie": [p(f"{tid}-bg", "Goalie", gk=2.0)],
        },
        reserves=[],
    )


TEAMS = ["E1", "E2", "W1", "W2"]
CONF = {"E1": "East", "E2": "East", "W1": "West", "W2": "West"}


def _league(risk=0.0, with_injuries=False):
    repo = InMemoryTeamRepository()
    strength = 9.0
    for tid in TEAMS:                       # strictly decreasing strength
        repo.save(_team(tid, strength, risk=risk))
        strength -= 1.0
    orch = SeasonOrchestrator(repo, FakeSheetGateway(), SimpleGameEngine(), InMemoryRecordSink())
    injuries = InjurySimulator(rng=random.Random(0)) if with_injuries else None
    return LeagueOperations(
        orch,
        schedule=Schedule.round_robin(TEAMS),
        injuries=injuries,
        conference_of=CONF.get,
        teams_per_conference=2,
    )


def test_publish_all_writes_baseline():
    league = _league()
    league.publish_all()
    gw = league.orch.gateway
    assert gw.publish_calls == len(TEAMS)


def test_run_period_plays_full_round_robin_and_ranks_by_strength():
    league = _league()
    league.publish_all()
    results = league.run_period(1)
    assert len(results) == 6                       # 4-team round robin
    ranked = league.ranked_team_ids()
    assert ranked[0] == "E1" and ranked[-1] == "W2"


def test_no_schedule_raises():
    league = _league()
    league.schedule = None
    with pytest.raises(RuntimeError):
        league.run_period(1)


def test_run_period_with_injuries_applies_at_chunk_end():
    np.random.seed(0)
    league = _league(risk=1.0, with_injuries=True)   # everyone active gets hurt
    league.publish_all()
    league.run_period(1)
    # injuries are rolled once at the end of the chunk. There is no auto-sub, so
    # injured players stay in the starting lineup -- which is now a legal state.
    assert league.injuries.events
    repo = league.orch.team_repo
    for tid in TEAMS:
        t = repo.load(tid)                            # load() validates
        starters = [pid for ids in t.arrangement().starters.values() for pid in ids]
        assert any(t.get(pid).is_injured for pid in starters)


def test_full_lifecycle_draft_and_playoffs():
    np.random.seed(0)
    random.seed(0)
    league = _league()
    league.publish_all()
    league.run_period(1)

    # draft runs in reverse-standings order off the live ranking
    picks = league.run_draft([(f"Rookie{i}", "Forward") for i in range(4)], rounds=1)
    assert len(picks) == 4
    assert picks[0].holder_team_id == league.ranked_team_ids()[-1]   # worst picks first

    # playoffs seed the top team per conference and crown the overall #1
    bracket = league.run_playoffs()
    assert bracket.champion == "E1"
    # canonical records untouched by the playoff run
    assert league.ranked_team_ids()[0] == "E1"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
