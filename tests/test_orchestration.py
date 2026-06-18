"""
Proof of constructor injection: SeasonOrchestrator runs a full period fully
offline against InMemoryTeamRepository + FakeSheetGateway + a deterministic
engine -- no cred.txt, no SheetHandler, no Google.

`pytest tests/test_orchestration.py` or `python tests/test_orchestration.py`.
"""
import pytest

from handball.domain import Player, Team
from handball.league_views import TeamArrangement
from handball.orchestration import (
    GameEngine,
    InMemoryRecordSink,
    SeasonOrchestrator,
    SimpleGameEngine,
)
from handball.repository import InMemoryTeamRepository
from handball.sheet_gateway import FakeSheetGateway


def _team(team_id: str, base_off: float = 5.0) -> Team:
    def p(pid, name, pos, off=base_off, **kw):
        return Player(id=f"{team_id}-{pid}", name=name, position=pos, offense=off, **kw)

    return Team(
        id=team_id, name=f"{team_id} FC", coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "Ada", "Forward"), p("f2", "Ben", "Forward"), p("f3", "Cy", "Forward")],
            "Midfielder": [p("m1", "Dee", "Midfielder"), p("m2", "Eli", "Midfielder"), p("m3", "Fin", "Midfielder")],
            "Defense": [p("d1", "Gus", "Defense"), p("d2", "Hal", "Defense"), p("d3", "Ike", "Defense")],
            "Goalie": [p("g1", "Jo", "Goalie", off=0.1, goalie_skill=6.0)],
        },
        bench={
            # a clearly stronger bench forward, to test that promoting it matters
            "Forward": [p("f4", "Kai", "Forward", off=9.5), p("f5", "Lou", "Forward")],
            "Midfielder": [p("m4", "Mo", "Midfielder"), p("m5", "Ned", "Midfielder")],
            "Defense": [p("d4", "Oz", "Defense"), p("d5", "Pat", "Defense")],
            "Goalie": [p("g2", "Quin", "Goalie", off=0.1, goalie_skill=5.0)],
        },
        reserves=[p("r1", "Ray", "Forward"), p("r2", "Sam", "Defense")],
    )


def _orchestrator(*team_ids):
    repo = InMemoryTeamRepository()
    for tid in team_ids:
        repo.save(_team(tid))
    gw = FakeSheetGateway()
    orch = SeasonOrchestrator(repo, gw, SimpleGameEngine(), InMemoryRecordSink())
    orch.publish_all()
    return orch, repo, gw


def test_simple_engine_satisfies_protocol():
    assert isinstance(SimpleGameEngine(), GameEngine)


def test_orchestrator_takes_injected_collaborators():
    # No cred.txt / SheetHandler anywhere -- pure construction.
    orch, _, _ = _orchestrator("Boston", "New York")
    assert isinstance(orch, SeasonOrchestrator)


def test_matchup_updates_and_persists_records():
    orch, repo, _ = _orchestrator("Boston", "New York")
    result = orch.simulate_matchup("Boston", "New York")

    # records updated on BOTH teams and written through the repo
    b, n = repo.load("Boston"), repo.load("New York")
    assert sum(b.record) == 1 and sum(n.record) == 1          # one game each
    if result.home_score > result.away_score:
        assert b.record == (1, 0, 0) and n.record == (0, 1, 0)


def test_matchup_republishes_both_teams():
    orch, _, gw = _orchestrator("Boston", "New York")
    before = gw.publish_calls
    orch.simulate_matchup("Boston", "New York")
    assert gw.publish_calls == before + 2                      # both projections refreshed


def test_manager_lineup_edit_is_pulled_and_honored():
    orch, repo, gw = _orchestrator("Boston", "New York")
    base = gw.read_arrangement("Boston")

    # Manager promotes the strong bench forward f4, benches f3.
    edited = TeamArrangement(
        starters={**base.starters, "Forward": ("Boston-f1", "Boston-f2", "Boston-f4")},
        bench={**base.bench, "Forward": ("Boston-f3", "Boston-f5")},
        reserves=base.reserves,
    )
    gw.simulate_manager_edit("Boston", edited)

    changed = orch.apply_manager_lineup("Boston")
    assert changed is True
    assert repo.load("Boston").starters["Forward"][2].id == "Boston-f4"   # persisted
    assert orch.apply_manager_lineup("Boston") is False                   # no further diff


def test_invalid_manager_edit_rejected_and_atomic():
    from handball.domain import ArrangementError

    orch, repo, gw = _orchestrator("Boston", "New York")
    base = gw.read_arrangement("Boston")
    before = repo.load("Boston").arrangement()

    bad = TeamArrangement(starters={**base.starters, "Forward": ("Boston-f1",)},  # only 1
                          bench=base.bench, reserves=base.reserves)
    gw.simulate_manager_edit("Boston", bad)

    with pytest.raises(ArrangementError):
        orch.apply_manager_lineup("Boston")
    assert repo.load("Boston").arrangement() == before                    # untouched


def test_run_period_pulls_then_plays():
    orch, repo, gw = _orchestrator("Boston", "New York", "Dallas", "Houston")

    # One manager edits before the period; should be applied before games run.
    base = gw.read_arrangement("Boston")
    edited = TeamArrangement(
        starters={**base.starters, "Forward": ("Boston-f1", "Boston-f2", "Boston-f4")},
        bench={**base.bench, "Forward": ("Boston-f3", "Boston-f5")},
        reserves=base.reserves,
    )
    gw.simulate_manager_edit("Boston", edited)

    results = orch.run_period([("Boston", "New York"), ("Dallas", "Houston")])
    assert len(results) == 2
    assert repo.load("Boston").starters["Forward"][2].id == "Boston-f4"   # edit took effect
    assert all(sum(repo.load(t).record) == 1 for t in ("Boston", "New York", "Dallas", "Houston"))
    assert len(orch.record_sink.games) == 2


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
