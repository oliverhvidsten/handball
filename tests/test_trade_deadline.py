"""
The trade deadline: once TRADE_DEADLINE_AFTER_PERIOD periods of the active season have
run, no trade may be proposed or accepted until the next league year opens. A trade
both teams already agreed to may still be APPROVED -- period 5 requires a clear
commissioner queue anyway, so killing accepted trades at the deadline would just make
the queue impossible to clear.

Against the dev Postgres (local DB only; it truncates). season_state is truncated
before AND after every test: this module moves the league clock, and a cursor left
behind would shut the market under a later test module.
"""
import uuid

import pytest
from sqlalchemy import text

from handball import schedule_repository as sched_repo
from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.extensions import windows
from handball.pg_repository import PostgresTeamRepository
from handball.simulation_vars import TRADE_DEADLINE_AFTER_PERIOD
from handball.trade_service import (
    TradeError,
    accept_trade,
    approve_trade,
    get_trade_status,
    propose_trade,
)

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

if _PG_OK:
    from fastapi.testclient import TestClient

    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets playoff_series season_state")
_SEASON = 2026


@pytest.fixture(autouse=True)
def _clean_db():
    _truncate()
    yield
    _truncate()
    app.dependency_overrides.clear()


def _truncate():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))


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


@pytest.fixture
def client():
    return TestClient(app)


def _periods(n: int, season: int = _SEASON) -> None:
    """Put the league clock at `n` periods run."""
    sched_repo.init_season_state(_engine, season, schedule_seed=season, injury_seed=season)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run = :n where season = :s"),
                  {"n": n, "s": season})


def _before() -> None:
    _periods(TRADE_DEADLINE_AFTER_PERIOD - 1)


def _after() -> None:
    _periods(TRADE_DEADLINE_AFTER_PERIOD)


def _crown_champion(winner: str = "Boston", loser: str = "Denver", season: int = _SEASON):
    """A finished bracket -- the single decided Final /season/advance gates on."""
    with _engine.begin() as c:
        c.execute(
            text("insert into playoff_series (season, round, conference, label, "
                 "high_seed_team_id, low_seed_team_id, high_seed, low_seed, winner_team_id) "
                 "select :s, 1, null, 'Final', "
                 "(select id from teams where slug = :w), "
                 "(select id from teams where slug = :l), 1, 1, "
                 "(select id from teams where slug = :w)"),
            {"s": season, "w": winner, "l": loser},
        )


def _as_manager(*slugs: str, role: str = "manager"):
    """Override auth to act as a manager owning the given team slug(s) (or a
    commissioner when none are given). Mirrors tests/test_api.py."""
    with _engine.connect() as c:
        owned = [str(c.execute(text("select id from teams where slug=:s"),
                               {"s": s}).scalar_one()) for s in slugs if s]
    user_id = str(uuid.uuid4())
    with _engine.begin() as c:
        c.execute(text("insert into managers (user_id, role) values (cast(:u as uuid), :r)"),
                  {"u": user_id, "r": role})
        for tid in owned:
            c.execute(text("update teams set owner_id = cast(:u as uuid) "
                           "where id = cast(:t as uuid)"), {"u": user_id, "t": tid})
    app.dependency_overrides[get_current_manager] = lambda: Manager(
        user_id=user_id, owned_team_ids=owned, role=role)


def _swap(**kw) -> str:
    return propose_trade(_engine, "Boston", "Denver",
                         players_out=["boston-r1"], players_in=["denver-f5"], **kw)


# -- the service -------------------------------------------------------------
def test_the_market_is_open_before_the_deadline(two_teams):
    _before()
    assert windows(_engine)["trade_deadline_passed"] is False
    tid = _swap()
    accept_trade(_engine, tid)
    assert get_trade_status(_engine, tid) == "accepted"


def test_no_trade_may_be_proposed_after_the_deadline(two_teams):
    _after()
    assert windows(_engine)["trade_deadline_passed"] is True
    with pytest.raises(TradeError, match="trade deadline has passed"):
        _swap()
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from trades")).scalar_one() == 0


def test_an_internal_trade_is_refused_too(two_teams):
    """Owning both teams is not a way round the calendar."""
    _after()
    with pytest.raises(TradeError, match="trade deadline has passed"):
        _swap(internal=True)


def test_a_proposal_left_open_cannot_be_accepted_after_the_deadline(two_teams):
    _before()
    tid = _swap()                       # proposed in time...
    _after()                            # ...but nobody answered before the deadline
    with pytest.raises(TradeError, match="trade deadline has passed"):
        accept_trade(_engine, tid)
    assert get_trade_status(_engine, tid) == "proposed"


def test_an_accepted_trade_may_still_be_approved(two_teams):
    repo = two_teams
    _before()
    tid = _swap()
    accept_trade(_engine, tid)
    _after()

    approve_trade(_engine, tid)         # the commissioner's queue still clears

    assert get_trade_status(_engine, tid) == "committed"
    assert len(repo.load("Boston").roster()) == 19


def test_the_deadline_is_read_off_the_period_count(two_teams):
    for n in range(TRADE_DEADLINE_AFTER_PERIOD):
        _periods(n)
        assert windows(_engine)["trade_deadline_passed"] is False
    _periods(TRADE_DEADLINE_AFTER_PERIOD + 1)
    assert windows(_engine)["trade_deadline_passed"] is True


def test_a_league_with_no_season_state_has_an_open_market(two_teams):
    """A fresh league that has never run a period is not past its deadline."""
    assert windows(_engine)["trade_deadline_passed"] is False
    assert get_trade_status(_engine, _swap()) == "proposed"


# -- through the API ---------------------------------------------------------
def test_api_refuses_a_proposal_after_the_deadline(client, two_teams):
    _after()
    _as_manager("Boston")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    assert r.status_code == 400 and "deadline" in r.json()["detail"]


def test_api_refuses_an_acceptance_after_the_deadline(client, two_teams):
    _before()
    tid = _swap()
    _after()
    _as_manager("Denver")
    r = client.post(f"/trades/{tid}/accept")
    assert r.status_code == 409 and "deadline" in r.json()["detail"]


def test_api_still_approves_after_the_deadline(client, two_teams):
    _before()
    tid = _swap()
    accept_trade(_engine, tid)
    _after()
    _as_manager("", role="commissioner")
    r = client.post(f"/trades/{tid}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "committed"


def test_advancing_the_season_reopens_the_market(client, two_teams):
    _periods(5)                                  # the regular season is over
    _crown_champion()
    _as_manager("", role="commissioner")
    assert client.get("/contracts/windows").json()["trade_deadline_passed"] is True
    with pytest.raises(TradeError, match="trade deadline has passed"):
        _swap()

    assert client.post("/season/advance").status_code == 200

    w = client.get("/contracts/windows").json()
    assert w["season"] == _SEASON + 1 and w["trade_deadline_passed"] is False
    assert get_trade_status(_engine, _swap()) == "proposed"
