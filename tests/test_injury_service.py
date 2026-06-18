"""
Offline tests for InjuryService: injury next-man-up expressed as an arrangement
computation routed through Team.apply_arrangement() (so every result is checked
by domain.validate()).

`pytest tests/test_injury_service.py` or `python tests/test_injury_service.py`.
"""
import pytest

from handball.domain import Player, Team, validate
from handball.injury_service import InjuryError, InjuryService


def _p(pid, pos, injured=False):
    return Player(id=pid, name=pid, position=pos, is_injured=injured)


def _team(**injuries):
    """A full, legal team. Pass e.g. sf1=True to mark that player injured AFTER
    construction (so it builds as a legal lineup, then we induce the injury)."""
    t = Team(
        id="T", name="T", coaches=[],
        starters={
            "Forward": [_p("sf1", "Forward"), _p("sf2", "Forward"), _p("sf3", "Forward")],
            "Midfielder": [_p("sm1", "Midfielder"), _p("sm2", "Midfielder"), _p("sm3", "Midfielder")],
            "Defense": [_p("sd1", "Defense"), _p("sd2", "Defense"), _p("sd3", "Defense")],
            "Goalie": [_p("sg1", "Goalie")],
        },
        bench={
            "Forward": [_p("bf1", "Forward"), _p("bf2", "Forward")],
            "Midfielder": [_p("bm1", "Midfielder"), _p("bm2", "Midfielder")],
            "Defense": [_p("bd1", "Defense"), _p("bd2", "Defense")],
            "Goalie": [_p("bg1", "Goalie")],
        },
        reserves=[_p("rf1", "Forward"), _p("rd1", "Defense")],
    )
    for pid, hurt in injuries.items():
        t.get(pid).is_injured = hurt
    return t


def test_starter_injury_swaps_in_healthy_bench():
    svc = InjuryService()
    team = _team(sf1=True)
    assert svc.apply(team, team.get("sf1")) is True

    arr = team.arrangement()
    # the injured starter is now on the bench; a healthy bench player started
    assert "sf1" in arr.bench["Forward"]
    assert "sf1" not in arr.starters["Forward"]
    assert "bf1" in arr.starters["Forward"]
    # still a legal lineup
    validate(arr, team)


def test_starter_injury_with_all_bench_hurt_promotes_reserve():
    svc = InjuryService()
    team = _team(sf1=True, bf1=True, bf2=True)   # whole forward bench unavailable
    assert svc.apply(team, team.get("sf1")) is True

    arr = team.arrangement()
    assert "rf1" in arr.starters["Forward"]      # healthy reserve promoted to start
    assert "sf1" in arr.reserves                 # injured parked in reserves
    assert "sf1" not in arr.starters["Forward"]
    validate(arr, team)


def test_no_healthy_depth_raises():
    svc = InjuryService()
    team = _team(sf1=True, bf1=True, bf2=True, rf1=True)  # every forward hurt
    with pytest.raises(InjuryError):
        svc.apply(team, team.get("sf1"))


def test_bench_injury_is_a_noop():
    svc = InjuryService()
    team = _team(bf1=True)
    before = team.arrangement()
    assert svc.apply(team, team.get("bf1")) is False
    assert team.arrangement() == before          # injured bench player is legal


def test_reconcile_fixes_multiple_injured_starters():
    svc = InjuryService()
    team = _team(sf1=True, sd1=True)
    assert svc.reconcile(team) is True

    arr = team.arrangement()
    starter_ids = {pid for ids in arr.starters.values() for pid in ids}
    assert "sf1" not in starter_ids and "sd1" not in starter_ids
    assert not any(team.get(pid).is_injured for pid in starter_ids)
    validate(arr, team)


def test_reconcile_is_idempotent_when_healthy():
    svc = InjuryService()
    team = _team()
    before = team.arrangement()
    assert svc.reconcile(team) is False
    assert team.arrangement() == before


def test_substitution_for_is_pure():
    svc = InjuryService()
    team = _team(sf1=True)
    before = team.arrangement()
    arr = svc.substitution_for(team, team.get("sf1"))
    assert arr is not None and arr != before
    assert team.arrangement() == before          # computed, not applied


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
