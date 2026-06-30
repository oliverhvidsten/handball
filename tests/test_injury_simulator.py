"""
Offline tests for InjurySimulator: the chunk-end tick/roll loop on the new stack.

Injuries are rolled and applied once per chunk (1/5-season period). There is NO
auto-substitution -- an injured player stays in whatever slot they occupy and
simply contributes nothing in the game simulator. Injury duration is measured in
chunks (minor=1, moderate=2, major=3) and the per-game injury_risk is scaled up
by INJURY_CHUNK_RISK_SCALE because we now roll once per chunk. Deterministic via
an injected random.Random.

`pytest tests/test_injury_simulator.py` or `python tests/test_injury_simulator.py`.
"""
import random

import pytest

from handball.domain import Player, Team, validate
from handball.injury_simulator import InjuryEvent, InjurySimulator
from handball.simulation_vars import INJURY_CHUNK_RISK_SCALE


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


# 10 starters + 7 bench players take the field; the 2 reserves do not.
_ACTIVE_COUNT = 17


def test_no_risk_no_injuries():
    team = _team(risk=0.0)
    sim = InjurySimulator(rng=random.Random(0))
    assert sim.process_team(team, chunk=1) is False
    assert sim.events == []


def test_certain_injury_no_substitution():
    team = _team(risk=1.0)              # every active player gets hurt this chunk
    sim = InjurySimulator(rng=random.Random(0))
    changed = sim.process_team(team, chunk=1)

    assert changed
    # No auto-substitution: injured players stay exactly where they were -- so the
    # whole starting lineup is now injured, and that is a LEGAL arrangement.
    arr = team.arrangement()
    for ids in arr.starters.values():
        for pid in ids:
            assert team.get(pid).is_injured
    validate(arr, team)
    # reserves never take the field, so they are not at risk
    assert not team.get("rf1").is_injured and not team.get("rd1").is_injured
    assert sum(e.kind == "injury" for e in sim.events) == _ACTIVE_COUNT


def test_rate_is_scaled_by_5():
    # 0.2 * 5 == 1.0, and rng.random() is always < 1.0, so the roll always hits.
    assert INJURY_CHUNK_RISK_SCALE == 5
    team = _team(risk=0.2)
    sim = InjurySimulator(rng=random.Random(0))
    sim.process_team(team, chunk=1)
    assert all(p.is_injured for p in InjurySimulator._active_players(team))


def test_duration_is_in_chunks():
    team = _team()
    assert team.get("sf1").injure(0, "Ankle (Sprain)") == 1      # minor
    assert team.get("sf2").injure(0, "Shoulder (Sprain)") == 2   # moderate
    assert team.get("sf3").injure(0, "ACL (Tear)") == 3          # major


def test_tick_decrements_one_chunk_then_recovers():
    team = _team(risk=0.0)
    sf1 = team.get("sf1")
    sf1.injure(0, "Shoulder (Sprain)")                 # moderate -> 2 chunks
    sim = InjurySimulator(rng=random.Random(0))

    sim.process_team(team, chunk=1)                    # 2 -> 1
    assert sf1.is_injured and sf1.injury_log.games_remaining == 1

    sim.process_team(team, chunk=2)                    # 1 -> 0, recovered
    assert sf1.is_injured is False


def test_recovery_clears_tag_and_logs_event():
    team = _team(risk=0.0)
    sf1 = team.get("sf1")
    sf1.injure(0, "Ankle (Sprain)")                    # minor -> 1 chunk
    sim = InjurySimulator(rng=random.Random(0))
    # a prior injury event for this player so recovery closes it
    sim.events.append(InjuryEvent(
        "injury", "sf1", "sf1", "T", chunk=0, injury_type="Ankle (Sprain)", severity="minor"))

    sim.process_team(team, chunk=2)                    # ticks 1 -> 0, recovers

    assert sf1.is_injured is False
    closed = [e for e in sim.events if e.kind == "injury" and e.player_id == "sf1"]
    assert closed and closed[0].recovered_chunk == 2


def test_process_period_end_persists_and_publishes():
    """End-to-end with the orchestrator's repo + gateway."""
    from handball.orchestration import SeasonOrchestrator, SimpleGameEngine, InMemoryRecordSink
    from handball.repository import InMemoryTeamRepository
    from handball.sheet_gateway import FakeSheetGateway

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
    sim.process_period_end(orch, ["A", "B"], chunk=1)

    assert sim.events
    # injured players persist; load() validates, and injured starters are legal
    for tid in ("A", "B"):
        t = repo.load(tid)
        assert any(
            t.get(pid).is_injured
            for ids in t.arrangement().starters.values()
            for pid in ids
        )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
