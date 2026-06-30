"""
Pure (no-DB) unit tests for the Coach domain object's tenure-transition logic.
Runs anywhere; the fastest guard on the career-history rules.

    pytest tests/test_coach.py
"""
from handball.domain import Coach
from handball.league_views import CoachTenure


def _open(coach: Coach):
    return [t for t in coach.tenures if t.end_season is None]


def test_first_assignment_opens_one_tenure():
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2025)
    assert c.tenures == [CoachTenure("Boston", "HC", 2025, None)]
    assert c.current_tenure() == CoachTenure("Boston", "HC", 2025, None)


def test_same_post_reassign_is_noop():
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2025)
    c.assign("Boston", "HC", 2025)   # identical re-seed
    c.assign("Boston", "HC", 2026)   # later season, same post
    assert len(c.tenures) == 1
    assert _open(c) == [CoachTenure("Boston", "HC", 2025, None)]


def test_role_swap_same_team_records_two_stints():
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "OC", 2024)
    c.assign("Boston", "HC", 2025)
    assert c.tenures == [
        CoachTenure("Boston", "OC", 2024, 2024),
        CoachTenure("Boston", "HC", 2025, None),
    ]
    assert len(_open(c)) == 1


def test_move_team_closes_old_opens_new():
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2024)
    c.assign("Seattle", "HC", 2027)
    assert c.tenures == [
        CoachTenure("Boston", "HC", 2024, 2026),
        CoachTenure("Seattle", "HC", 2027, None),
    ]


def test_same_season_replacement_never_ends_before_start():
    # Hired in 2025, replaced (moved) in 2025: end is clamped to start, not 2024.
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2025)
    c.assign("Seattle", "HC", 2025)
    closed = c.tenures[0]
    assert closed.start_season == 2025 and closed.end_season == 2025
    assert closed.end_season >= closed.start_season


def test_leave_closes_open_tenure():
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2024)
    c.leave(2026)
    assert c.current_tenure() is None
    assert c.tenures == [CoachTenure("Boston", "HC", 2024, 2025)]


def test_return_after_leaving_creates_gap():
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2024)
    c.leave(2026)                    # ends 2025
    c.assign("Boston", "HC", 2030)   # rehired years later
    assert c.tenures == [
        CoachTenure("Boston", "HC", 2024, 2025),
        CoachTenure("Boston", "HC", 2030, None),
    ]
