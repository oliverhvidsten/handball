"""
Offline tests for InjurySimulator: the per-game tick/roll/substitute loop on the
new stack. Deterministic via an injected random.Random plus np.random seeding
(injury duration is sampled with numpy inside InjuryReport.add).

`pytest tests/test_injury_simulator.py` or `python tests/test_injury_simulator.py`.
"""
import random

import numpy as np
import pytest

from handball.domain import Player, Team, validate
from handball.injury_simulator import InjurySimulator


def _p(pid, pos, risk=0.0):
    return Player(id=pid, name=pid, position=pos, injury_risk=risk)


def _team(risk=0.0):
    return Team(
        id="T", name="T", coaches=[],
        starters={
            "Forward": [_p("sf1", "Forward", risk), _p("sf2", "Forward", risk), _p("sf3", "Forward", risk)],
            "Midfielder": [_p("sm1", "Midfielder", risk), _p("sm2", "Midfielder", risk), _p("sm3", "Midfielder", risk)],
            "Defense": [_p("sd1", "Defense", risk), _p("sd2", "Defense", risk), _p("sd3", "Defense", risk)],
            "Goalie": [_p("sg1", "Goalie", risk)],
        },
        bench={
            "Forward": [_p("bf1", "Forward", risk), _p("bf2", "Forward", risk)],
            "Midfielder": [_p("bm1", "Midfielder", risk), _p("bm2", "Midfielder", risk)],
            "Defense": [_p("bd1", "Defense", risk), _p("bd2", "Defense", risk)],
            "Goalie": [_p("bg1", "Goalie", risk)],
        },
        reserves=[_p("rf1", "Forward", risk), _p("rd1", "Defense", risk)],
    )


def test_no_risk_no_injuries():
    team = _team(risk=0.0)
    sim = InjurySimulator(rng=random.Random(0))
    assert sim.process_team(team, week=1) is False
    assert sim.events == []


def test_certain_injury_is_substituted_and_lineup_stays_legal():
    np.random.seed(0)
    team = _team(risk=1.0)              # everyone healthy gets hurt this game
    sim = InjurySimulator(rng=random.Random(0))
    changed = sim.process_team(team, week=1)

    assert changed
    # every starting slot is healthy after substitution
    arr = team.arrangement()
    for ids in arr.starters.values():
        for pid in ids:
            assert not team.get(pid).is_injured
    validate(arr, team)               # persisted lineup is legal
    assert any(e.kind == "injury" for e in sim.events)


def test_deferred_when_position_wiped_out():
    """If every same-position player is already hurt, a new injury to that
    position's starter can't be substituted -> it's deferred, not persisted."""
    np.random.seed(0)
    team = _team(risk=0.0)
    # Starters stay healthy (legal initial lineup), but ALL forward depth -- both
    # bench forwards and the reserve forward -- is already injured with several
    # games left (so the pre-roll tick doesn't heal them).
    for pid in ("bf1", "bf2", "rf1"):
        pl = team.get(pid)
        pl.injure(0, "Knee (Sprain)")
        pl.injury_log.injuries[-1][3] = 5     # games_remaining
    validate(team.arrangement(), team)        # legal: only bench/reserve are hurt

    # now force a forward STARTER (sf1) to get hurt -- no healthy forward exists
    # to replace it, so the substitution can't be made.
    team.get("sf1").injury_risk = 1.0
    sim = InjurySimulator(rng=random.Random(0))
    sim.process_team(team, week=1)

    # sf1's injury was deferred: it plays on, lineup still legal
    assert team.get("sf1").is_injured is False
    assert any(e.kind == "deferred" and e.player_id == "sf1" for e in sim.events)
    validate(team.arrangement(), team)


def test_recovery_clears_tag_and_logs_event():
    np.random.seed(0)
    team = _team(risk=0.0)
    sf1, sf2, sf3, bf1, bf2 = (team.get(p) for p in ("sf1", "sf2", "sf3", "bf1", "bf2"))
    # sf1 injured with 1 game remaining; sit it on the bench so the team is legal
    sf1.injure(0, "Bruised heel")
    sf1.injury_log.injuries[-1][3] = 1                 # games_remaining = 1
    team.starters["Forward"] = [bf1, sf2, sf3]
    team.bench["Forward"] = [sf1, bf2]
    validate(team.arrangement(), team)
    # log a prior injury event so recovery can close it
    from handball.injury_simulator import InjuryEvent
    sim = InjurySimulator(rng=random.Random(0))
    sim.events.append(InjuryEvent("injury", "sf1", "sf1", "T", week=0, injury_type="Bruised heel", severity="minor"))

    sim.process_team(team, week=2)

    assert team.get("sf1").is_injured is False         # ticked to recovered
    closed = [e for e in sim.events if e.kind == "injury" and e.player_id == "sf1"]
    assert closed and closed[0].recovered_week == 2


def test_process_matchup_persists_and_publishes():
    """End-to-end with the orchestrator's repo + gateway."""
    from handball.orchestration import SeasonOrchestrator, SimpleGameEngine, InMemoryRecordSink
    from handball.repository import InMemoryTeamRepository
    from handball.sheet_gateway import FakeSheetGateway

    np.random.seed(0)
    repo = InMemoryTeamRepository()
    a, b = _team(risk=1.0), _team(risk=1.0)
    a.id = a.name = "A"
    b.id = b.name = "B"
    repo.save(a)
    repo.save(b)
    gw = FakeSheetGateway()
    orch = SeasonOrchestrator(repo, gw, SimpleGameEngine(), InMemoryRecordSink())
    orch.publish_all()

    sim = InjurySimulator(rng=random.Random(0))
    sim.process_matchup(orch, "A", "B", week=1)

    # injuries were recorded and the substituted lineups persisted legally
    assert sim.events
    for tid in ("A", "B"):
        t = repo.load(tid)              # load() validates -> legal on disk
        for ids in t.arrangement().starters.values():
            for pid in ids:
                assert not t.get(pid).is_injured


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
