"""
Phase 5 verification: the trade state machine + transactional commit, against the
dev Postgres. Skips when Postgres is unavailable.
"""
import pytest
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.pg_repository import PostgresTeamRepository
from handball.trade_service import (
    TradeError,
    accept_trade,
    approve_trade,
    cancel_trade,
    get_trade_status,
    propose_trade,
    reject_trade,
)

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

# season_state is in the list because a trade now reads the league clock: the trade
# deadline is a period count (handball/extensions.py), so a cursor left behind by
# another test module would shut the market under every test in here.
_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets season_state")


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield


def _team(team_id: str) -> Team:
    def p(pid, name, pos, off=5.0, deff=5.0, gk=0.1):
        return Player(id=f"{team_id.lower()}-{pid}", name=f"{team_id} {name}", position=pos,
                      offense=off, defense=deff, goalie_skill=gk, variance=0.5)

    return Team(
        id=team_id, name=team_id, coaches=["HC", "OC", "DC"],
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


@pytest.fixture
def two_teams():
    repo = PostgresTeamRepository(_engine)
    repo.save(_team("Boston"))
    repo.save(_team("Denver"))
    return repo


def _team_of(repo, legacy_id):
    """Which team currently rosters this player (by loading both)."""
    for tid in repo.all_team_ids():
        if repo.load(tid).get(legacy_id) is not None:
            return tid
    return None


def test_one_for_one_trade_commits_atomically(two_teams):
    repo = two_teams
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"])  # F-for-F
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    assert get_trade_status(_engine, tid) == "committed"
    # players swapped teams...
    assert _team_of(repo, "boston-r1") == "Denver"
    assert _team_of(repo, "denver-f5") == "Boston"
    # ...and both teams still load as a legal arrangement with 19 players each
    assert len(repo.load("Boston").roster()) == 19
    assert len(repo.load("Denver").roster()) == 19


def test_internal_trade_is_created_accepted(two_teams):
    # internal=True (both teams owned by proposer): created already 'accepted',
    # skipping counterparty acceptance; still commits only on approval.
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"], internal=True)
    assert get_trade_status(_engine, tid) == "accepted"
    approve_trade(_engine, tid)
    assert get_trade_status(_engine, tid) == "committed"


def test_infeasible_trade_rolls_back(two_teams):
    repo = two_teams
    # send away 2 forwards for a defenseman -> Boston left with 4 forwards (< 5 needed)
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1", "boston-f4"], players_in=["denver-d5"])
    accept_trade(_engine, tid)

    with pytest.raises(TradeError):
        approve_trade(_engine, tid)

    # nothing moved; status is still 'accepted'; both teams untouched
    assert get_trade_status(_engine, tid) == "accepted"
    assert _team_of(repo, "boston-r1") == "Boston"
    assert _team_of(repo, "boston-f4") == "Boston"
    assert _team_of(repo, "denver-d5") == "Denver"
    assert len(repo.load("Boston").roster()) == 19


def _set_contract(legacy_id: str, value: int) -> None:
    with _engine.begin() as c:
        c.execute(text("update players set contract_value = :v where legacy_id = :lid"),
                  {"v": value, "lid": legacy_id})


def test_trade_over_hard_cap_rolls_back(two_teams):
    repo = two_teams
    # Boston sits at $240M; taking on denver-f5's $20M for its own $0M reserve
    # would push it to $260M -- past the $250M hard cap -> reject + roll back.
    _set_contract("boston-d1", 240)
    _set_contract("denver-f5", 20)
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"])
    accept_trade(_engine, tid)

    with pytest.raises(TradeError, match="hard cap"):
        approve_trade(_engine, tid)

    assert get_trade_status(_engine, tid) == "accepted"      # rolled back
    assert _team_of(repo, "denver-f5") == "Denver"
    assert _team_of(repo, "boston-r1") == "Boston"


def test_trade_up_to_hard_cap_commits(two_teams):
    repo = two_teams
    # Boston at $230M takes on a $20M player for a $0M reserve -> exactly $250M,
    # which is allowed (the cap is a ceiling, not a strict inequality).
    _set_contract("boston-d1", 230)
    _set_contract("denver-f5", 20)
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"])
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    assert get_trade_status(_engine, tid) == "committed"
    assert _team_of(repo, "denver-f5") == "Boston"
    assert repo.load("Boston").total_salaries == 250


def test_over_cap_team_may_trade_its_way_back(two_teams):
    repo = two_teams
    # Boston is at $270M -- legal only because rookie draft deals are cap-exempt, and
    # a season-start blocker until it's fixed (handball/season_readiness.py). Shedding
    # the $30M contract for a $0M reserve is exactly the fix, so it must be allowed
    # even though Boston is still over the cap afterwards ($240M... under, here).
    _set_contract("boston-d1", 240)
    _set_contract("boston-r1", 30)
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"])
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    assert get_trade_status(_engine, tid) == "committed"
    assert repo.load("Boston").total_salaries == 240


def test_over_cap_team_may_not_trade_sideways(two_teams):
    # Over the cap and NOT reducing payroll -> still rejected.
    repo = two_teams
    _set_contract("boston-d1", 260)              # $260M: over the $250M hard cap
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"])   # $0 <-> $0
    accept_trade(_engine, tid)

    with pytest.raises(TradeError, match="already over"):
        approve_trade(_engine, tid)

    assert get_trade_status(_engine, tid) == "accepted"      # rolled back
    assert _team_of(repo, "denver-f5") == "Denver"


def test_lifecycle_guards(two_teams):
    # can't approve a trade that hasn't been accepted
    tid = propose_trade(_engine, "Boston", "Denver", players_out=["boston-r1"], players_in=["denver-f5"])
    with pytest.raises(TradeError):
        approve_trade(_engine, tid)

    # reject is terminal: can't then accept
    reject_trade(_engine, tid)
    assert get_trade_status(_engine, tid) == "rejected"
    with pytest.raises(TradeError):
        accept_trade(_engine, tid)


def test_cancel_after_accept(two_teams):
    tid = propose_trade(_engine, "Boston", "Denver", players_out=["boston-r1"], players_in=["denver-f5"])
    accept_trade(_engine, tid)
    cancel_trade(_engine, tid)
    assert get_trade_status(_engine, tid) == "cancelled"


def test_empty_trade_rejected(two_teams):
    with pytest.raises(TradeError):
        propose_trade(_engine, "Boston", "Denver")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
