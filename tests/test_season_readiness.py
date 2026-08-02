"""
Unit tests for the season-start readiness registry (handball/season_readiness.py).
The checks are pure functions of a LeagueState snapshot, so these run with no DB --
only load_league_state()/season_blockers() touch Postgres, and those are covered by
the API/integration tests.
"""
import pytest

from handball.salary_cap import HARD_CAP
from handball.season_readiness import (
    Blocker,
    LeagueState,
    OpenFreeAgency,
    SeasonNotReady,
    TeamPayroll,
    assert_ready,
    blockers,
    checks,
    readiness_check,
)


def _state(*payrolls: int) -> LeagueState:
    """A league of teams named Team1..TeamN with the given payrolls."""
    return LeagueState(
        season=2026,
        payrolls=tuple(
            TeamPayroll(team_id=f"uuid-{i}", slug=f"team{i}", name=f"Team{i}", payroll=v)
            for i, v in enumerate(payrolls, start=1)
        ),
    )


# -- the registry ------------------------------------------------------------
def test_registry_has_the_hard_cap_check():
    names = [c.name for c in checks()]
    assert "hard_cap" in names
    assert len(names) == len(set(names))                  # names identify checks


def test_registry_rejects_a_duplicate_name():
    with pytest.raises(ValueError, match="duplicate"):
        @readiness_check("hard_cap", "a second check by the same name")
        def _dupe(state):                                 # pragma: no cover
            return []


def test_every_check_reports_nothing_for_a_clean_league():
    assert blockers(_state(100, 150, HARD_CAP)) == []


# -- hard cap ----------------------------------------------------------------
def test_team_at_the_cap_is_not_a_blocker():
    assert blockers(_state(HARD_CAP)) == []


def test_team_over_the_cap_blocks_the_season():
    found = blockers(_state(HARD_CAP + 12))
    assert len(found) == 1
    b = found[0]
    assert isinstance(b, Blocker)
    assert b.check == "hard_cap"
    assert b.subject == "Team1"
    assert f"${12}M over" in b.message
    assert b.detail["over_by"] == 12
    assert b.detail["payroll"] == HARD_CAP + 12
    assert b.detail["slug"] == "team1"


def test_one_blocker_per_offending_team_worst_first():
    found = blockers(_state(HARD_CAP + 5, 100, HARD_CAP + 30, HARD_CAP))
    assert [b.subject for b in found] == ["Team3", "Team1"]     # sorted by overage
    assert [b.detail["over_by"] for b in found] == [30, 5]


# -- free agency -------------------------------------------------------------
def _fa(round_status: str = "offers", live: int = 0) -> LeagueState:
    return LeagueState(
        season=2027,
        payrolls=_state(100).payrolls,
        free_agency=OpenFreeAgency(season=2027, round_number=2,
                                   round_status=round_status, live_auctions=live),
    )


def test_a_settled_free_agency_is_not_a_blocker():
    assert blockers(_state(100)) == []                    # free_agency is None


def test_an_open_offer_round_blocks_the_season():
    found = [b for b in blockers(_fa("offers")) if b.check == "free_agency_open"]
    assert len(found) == 1
    assert "still taking offers" in found[0].message
    assert found[0].detail["round_number"] == 2


def test_live_auctions_block_the_season_and_are_counted():
    found = [b for b in blockers(_fa("resolution", live=4)) if b.check == "free_agency_open"]
    assert "4 auction(s) still live" in found[0].message
    assert found[0].detail["live_auctions"] == 4


def test_a_finished_round_still_blocks_until_the_period_closes():
    """Every board resolved is not the same as free agency being over -- the
    commissioner may still open another round, so the period itself must be closed."""
    found = [b for b in blockers(_fa("complete")) if b.check == "free_agency_open"]
    assert "still open" in found[0].message


def test_free_agency_and_the_hard_cap_are_reported_together():
    state = LeagueState(
        season=2027,
        payrolls=_state(HARD_CAP + 5).payrolls,
        free_agency=OpenFreeAgency(2027, 1, "offers", 0),
    )
    assert {b.check for b in blockers(state)} == {"hard_cap", "free_agency_open"}


# -- the gate ----------------------------------------------------------------
def test_assert_ready_passes_a_clean_league():
    assert_ready(_state(100, 200, HARD_CAP))              # no raise


def test_assert_ready_raises_with_every_blocker():
    with pytest.raises(SeasonNotReady) as e:
        assert_ready(_state(HARD_CAP + 1, HARD_CAP + 2))
    assert len(e.value.blockers) == 2
    assert len(e.value.problems) == 2                     # API-facing message list
    assert "hard cap" in str(e.value)


def test_empty_league_is_ready():
    # A league with no teams yet (fresh DB) has nothing to block on.
    assert blockers(LeagueState(season=2026)) == []


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
