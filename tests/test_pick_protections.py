"""
Draft-pick protections attached at trade time: handball/trade_service.py's
propose_trade/approve_trade extension (alembic 0014's trade_assets.protection_top_n /
draft_picks.protection_top_n+protection_outcome), plus the API's TradeBody shape.
Against the dev Postgres. Skips when Postgres is unavailable. Local DB only
(truncates). Resolving a protection at lottery time is the draft agent's job and is
NOT tested here.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.pg_repository import PostgresTeamRepository
from handball.trade_service import (
    TradeError,
    accept_trade,
    approve_trade,
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
    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets")
_SEASON = 2027


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield
    app.dependency_overrides.clear()


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


def _team_id(slug: str) -> str:
    with _engine.connect() as c:
        return str(c.execute(text("select id from teams where slug = :s"), {"s": slug}).scalar_one())


_season_ctr = [_SEASON]


def _pick(rnd, original_slug, holder_slug=None) -> str:
    # One draft_picks row per (season, round, original_team_id): give each pick its
    # own season so a test can hold several picks for the same original team.
    _season_ctr[0] += 1
    original = _team_id(original_slug)
    holder = _team_id(holder_slug) if holder_slug else original
    with _engine.begin() as c:
        return str(c.execute(
            text("insert into draft_picks (season, round, original_team_id, holder_team_id, used) "
                 "values (:s, :r, cast(:o as uuid), cast(:h as uuid), false) returning id"),
            {"s": _season_ctr[0], "r": rnd, "o": original, "h": holder},
        ).scalar_one())


def _pick_row(pick_id: str) -> dict:
    with _engine.connect() as c:
        return dict(c.execute(
            text("select holder_team_id, protection_top_n, protection_outcome "
                 "from draft_picks where id = cast(:p as uuid)"),
            {"p": pick_id},
        ).mappings().first())


# -- handball.trade_service: proposal-time validation -----------------------
def test_protected_pick_proposes_and_lands_on_draft_picks(two_teams):
    pick_id = _pick(1, "Boston")
    tid = propose_trade(
        _engine, "Boston", "Denver",
        picks_out=[{"pick_id": pick_id, "protection_top_n": 8}],
    )
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    row = _pick_row(pick_id)
    assert str(row["holder_team_id"]) == _team_id("Denver")
    assert row["protection_top_n"] == 8
    assert row["protection_outcome"] is None


def test_protection_on_picks_in_side_also_lands(two_teams):
    # picks_in: Boston receives Denver's protected round-1 pick.
    pick_id = _pick(1, "Denver")
    tid = propose_trade(
        _engine, "Boston", "Denver",
        picks_in=[{"pick_id": pick_id, "protection_top_n": 12}],
    )
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    row = _pick_row(pick_id)
    assert str(row["holder_team_id"]) == _team_id("Boston")
    assert row["protection_top_n"] == 12


def test_round_2_pick_protection_refused(two_teams):
    pick_id = _pick(2, "Boston")
    with pytest.raises(TradeError, match="round-1"):
        propose_trade(
            _engine, "Boston", "Denver",
            picks_out=[{"pick_id": pick_id, "protection_top_n": 5}],
        )


def test_out_of_range_protection_refused(two_teams):
    pick_id = _pick(1, "Boston")
    with pytest.raises(TradeError, match="1 and 32"):
        propose_trade(
            _engine, "Boston", "Denver",
            picks_out=[{"pick_id": pick_id, "protection_top_n": 33}],
        )
    pick_id2 = _pick(1, "Boston")
    with pytest.raises(TradeError, match="1 and 32"):
        propose_trade(
            _engine, "Boston", "Denver",
            picks_out=[{"pick_id": pick_id2, "protection_top_n": 0}],
        )


def test_plain_string_pick_ids_still_work(two_teams):
    pick_id = _pick(1, "Boston")
    tid = propose_trade(_engine, "Boston", "Denver", picks_out=[pick_id])
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    row = _pick_row(pick_id)
    assert str(row["holder_team_id"]) == _team_id("Denver")
    assert row["protection_top_n"] is None
    assert row["protection_outcome"] is None


def test_retrading_a_protected_pick_without_restating_it_clears_protection(two_teams):
    """A trade's terms are the whole agreement about the pick's condition: a second
    trade that doesn't mention a protection isn't silently carrying the old one
    forward."""
    pick_id = _pick(1, "Boston")
    tid = propose_trade(_engine, "Boston", "Denver", picks_out=[{"pick_id": pick_id, "protection_top_n": 4}])
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)
    assert _pick_row(pick_id)["protection_top_n"] == 4

    # Denver now holds it and trades it away again, unprotected.
    tid2 = propose_trade(_engine, "Denver", "Boston", picks_out=[pick_id])
    accept_trade(_engine, tid2)
    approve_trade(_engine, tid2)
    assert _pick_row(pick_id)["protection_top_n"] is None


# -- API: TradeBody accepts both shapes --------------------------------------
def _as_manager(*slugs: str, role: str = "manager"):
    owned = [_team_id(s) for s in slugs if s]
    user_id = str(uuid.uuid4())
    with _engine.begin() as c:
        c.execute(
            text("insert into managers (user_id, role) values (cast(:u as uuid), :r)"),
            {"u": user_id, "r": role},
        )
        for tid in owned:
            c.execute(
                text("update teams set owner_id = cast(:u as uuid) where id = cast(:t as uuid)"),
                {"u": user_id, "t": tid},
            )
    mgr = Manager(user_id=user_id, owned_team_ids=owned, role=role)
    app.dependency_overrides[get_current_manager] = lambda: mgr


@pytest.fixture
def client():
    return TestClient(app)


def test_api_accepts_plain_and_protected_pick_shapes(client, two_teams):
    plain_pick = _pick(1, "Boston")
    protected_pick = _pick(1, "Boston")

    _as_manager("Boston")
    r = client.post("/trades", json={
        "from_team": "Boston", "to_team": "Denver",
        "picks_out": [plain_pick, {"pick_id": protected_pick, "protection_top_n": 15}],
    })
    assert r.status_code == 200, r.text
    trade_id = r.json()["trade_id"]

    _as_manager("Denver")
    client.post(f"/trades/{trade_id}/accept")
    _as_manager("", role="commissioner")
    r = client.post(f"/trades/{trade_id}/approve")
    assert r.status_code == 200, r.text

    assert _pick_row(plain_pick)["protection_top_n"] is None
    assert _pick_row(protected_pick)["protection_top_n"] == 15


def test_api_rejects_out_of_range_protection(client, two_teams):
    pick_id = _pick(1, "Boston")
    _as_manager("Boston")
    r = client.post("/trades", json={
        "from_team": "Boston", "to_team": "Denver",
        "picks_out": [{"pick_id": pick_id, "protection_top_n": 40}],
    })
    assert r.status_code == 422


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
