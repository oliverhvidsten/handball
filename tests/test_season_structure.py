"""
Offline tests for the season structure: the Schedule value object (period/week
slicing + round-robin) and SeasonRunner driving the orchestrator with the
deterministic SimpleGameEngine. No sheet, no OR-Tools.

`pytest tests/test_season.py` or `python tests/test_season.py`.
"""
import pytest

from handball.domain import Player, Team
from handball.orchestration import InMemoryRecordSink, SeasonOrchestrator, SimpleGameEngine
from handball.repository import InMemoryTeamRepository
from handball.season import (
    PERIODS,
    REGULAR_SEASON_WEEKS,
    WEEKS_PER_PERIOD,
    Schedule,
    SeasonRunner,
)
from handball.sheet_gateway import FakeSheetGateway


# --- Schedule (pure) -------------------------------------------------------
def test_round_robin_every_pair_once_no_double_booking():
    teams = ["A", "B", "C", "D"]
    sched = Schedule.round_robin(teams)
    # 4 teams -> 3 weeks, 2 games each
    assert sched.num_weeks == 3
    seen = set()
    for w in range(1, sched.num_weeks + 1):
        played = []
        for home, away in sched.week(w):
            played += [home, away]
            seen.add(frozenset((home, away)))
        assert len(played) == len(set(played))      # nobody plays twice/week
    # every unordered pair appears exactly once
    assert len(seen) == 6


def test_round_robin_odd_count_has_byes():
    sched = Schedule.round_robin(["A", "B", "C"])
    assert sched.num_weeks == 3
    # one team byes each week -> exactly one game per week
    for w in range(1, 4):
        assert len(sched.week(w)) == 1


def test_period_slices_by_eleven_weeks():
    # 55 one-game weeks, week N is (f"H{N}", f"A{N}")
    weeks = [[(f"H{n}", f"A{n}")] for n in range(1, REGULAR_SEASON_WEEKS + 1)]
    sched = Schedule.from_weeks(weeks)
    assert sched.num_weeks == REGULAR_SEASON_WEEKS

    p1 = sched.period(1)
    assert len(p1) == WEEKS_PER_PERIOD
    assert p1[0] == ("H1", "A1") and p1[-1] == ("H11", "A11")

    p5 = sched.period(5)
    assert p5[0] == ("H45", "A45") and p5[-1] == ("H55", "A55")

    # the five periods partition the whole season
    total = sum(len(sched.period(p)) for p in range(1, PERIODS + 1))
    assert total == REGULAR_SEASON_WEEKS


def test_from_generator_dict_keeps_pairings():
    data = {"weeks": [
        [{"team1": "A", "team2": "B", "matchup_type": "conference"}],
        [{"team1": "C", "team2": "D", "matchup_type": "inter_conference"}],
    ]}
    sched = Schedule.from_generator_dict(data)
    assert sched.week(1) == (("A", "B"),)
    assert sched.week(2) == (("C", "D"),)


def test_week_out_of_range_raises():
    sched = Schedule.round_robin(["A", "B"])
    with pytest.raises(IndexError):
        sched.week(99)


# --- SeasonRunner (integration with the orchestrator) ---------------------
def _player(pid, pos, off=5.0, gk=0.1):
    return Player(id=pid, name=pid, position=pos, offense=off, goalie_skill=gk)


def _team(tid, strength):
    """A legal team; `strength` scales forward/mid offense so stronger lineups
    win under SimpleGameEngine (lets us assert games actually resolve)."""
    def starters():
        return {
            "Forward": [_player(f"{tid}-sf{i}", "Forward", off=strength) for i in range(3)],
            "Midfielder": [_player(f"{tid}-sm{i}", "Midfielder", off=strength) for i in range(3)],
            "Defense": [_player(f"{tid}-sd{i}", "Defense") for i in range(3)],
            "Goalie": [_player(f"{tid}-sg", "Goalie", gk=3.0)],
        }
    bench = {
        "Forward": [_player(f"{tid}-bf{i}", "Forward") for i in range(2)],
        "Midfielder": [_player(f"{tid}-bm{i}", "Midfielder") for i in range(2)],
        "Defense": [_player(f"{tid}-bd{i}", "Defense") for i in range(2)],
        "Goalie": [_player(f"{tid}-bg", "Goalie", gk=2.0)],
    }
    return Team(id=tid, name=tid, coaches=[], starters=starters(), bench=bench, reserves=[])


@pytest.fixture
def orch_and_repo():
    repo = InMemoryTeamRepository()
    repo.save(_team("A", strength=8.0))
    repo.save(_team("B", strength=4.0))
    repo.save(_team("C", strength=6.0))
    repo.save(_team("D", strength=2.0))
    orch = SeasonOrchestrator(repo, FakeSheetGateway(), SimpleGameEngine(), InMemoryRecordSink())
    orch.publish_all()
    return orch, repo


def test_run_week_plays_all_games(orch_and_repo):
    orch, repo = orch_and_repo
    sched = Schedule.round_robin(["A", "B", "C", "D"])
    runner = SeasonRunner(orch, sched)

    results = runner.run_week(1)
    assert len(results) == 2
    # every game in week 1 resolved (SimpleGameEngine, distinct strengths)
    played = sum(sum(repo.load(t).record) for t in ("A", "B", "C", "D"))
    assert played == 4   # 2 games -> 4 team-results


def test_run_period_plays_whole_round_robin(orch_and_repo):
    orch, repo = orch_and_repo
    sched = Schedule.round_robin(["A", "B", "C", "D"])
    runner = SeasonRunner(orch, sched)

    results = runner.run_period(1)
    assert len(results) == 6                       # full round robin in period 1
    for t in ("A", "B", "C", "D"):
        assert sum(repo.load(t).record) == 3       # each team played 3 games
    recs = [repo.load(t).record for t in ("A", "B", "C", "D")]
    assert sum(r[0] for r in recs) == sum(r[1] for r in recs)   # wins == losses


def test_standings_reflect_strength(orch_and_repo):
    orch, repo = orch_and_repo
    runner = SeasonRunner(orch, Schedule.round_robin(["A", "B", "C", "D"]))
    runner.run_period(1)
    table = orch.standings()
    # A is strongest, D weakest -> A on top, D at the bottom
    assert table[0][0] == "A"
    assert table[-1][0] == "D"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
