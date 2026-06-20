"""
Phase 6 verification: the FastAPI write endpoints against the dev Postgres, with
the auth dependency overridden (no Supabase needed). Skips when Postgres is
unavailable.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.pg_repository import PostgresTeamRepository

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

# import the app only when PG is available (it builds an engine at import)
if _PG_OK:
    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets")


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


def _uuid_of(slug: str) -> str:
    with _engine.connect() as c:
        return str(c.execute(text("select id from teams where slug=:s"), {"s": slug}).scalar_one())


def _as_manager(*slugs: str, role: str = "manager"):
    """Override auth to act as a manager owning the given team slug(s) (or a
    commissioner when none are given). Seeds the managers row (so trades.proposed_by
    resolves) and sets teams.owner_id for each owned team."""
    owned = [_uuid_of(s) for s in slugs if s]
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


def _arrangement_payload(team: Team) -> dict:
    arr = team.arrangement()
    return {
        "starters": {pos: list(ids) for pos, ids in arr.starters.items()},
        "bench": {pos: list(ids) for pos, ids in arr.bench.items()},
        "reserves": list(arr.reserves),
    }


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def two_teams():
    repo = PostgresTeamRepository(_engine)
    repo.save(_team("Boston"))
    repo.save(_team("Denver"))
    return repo


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_put_valid_arrangement(client, two_teams):
    repo = two_teams
    team = repo.load("Boston")
    payload = _arrangement_payload(team)
    payload["starters"]["Forward"] = list(reversed(payload["starters"]["Forward"]))  # reorder

    _as_manager("Boston")
    r = client.put("/teams/Boston/arrangement", json=payload)
    assert r.status_code == 200, r.text
    assert [p.id for p in repo.load("Boston").starters["Forward"]] == payload["starters"]["Forward"]


def test_put_invalid_arrangement_returns_problems(client, two_teams):
    repo = two_teams
    payload = _arrangement_payload(repo.load("Boston"))
    payload["reserves"].append(payload["starters"]["Forward"][0])  # duplicate -> illegal

    _as_manager("Boston")
    r = client.put("/teams/Boston/arrangement", json=payload)
    assert r.status_code == 400
    assert "problems" in r.json()["detail"]


def test_put_other_teams_arrangement_forbidden(client, two_teams):
    repo = two_teams
    payload = _arrangement_payload(repo.load("Boston"))
    _as_manager("Denver")                       # acting as Denver, editing Boston
    r = client.put("/teams/Boston/arrangement", json=payload)
    assert r.status_code == 403


def test_trade_flow_propose_accept_approve(client, two_teams):
    repo = two_teams

    _as_manager("Boston")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    assert r.status_code == 200, r.text
    trade_id = r.json()["trade_id"]

    _as_manager("Denver")
    assert client.post(f"/trades/{trade_id}/accept").json()["status"] == "accepted"

    _as_manager("", role="commissioner")
    assert client.post(f"/trades/{trade_id}/approve").json()["status"] == "committed"

    # swap landed
    assert repo.load("Denver").get("boston-r1") is not None
    assert repo.load("Boston").get("denver-f5") is not None


def test_non_commissioner_cannot_approve(client, two_teams):
    _as_manager("Boston")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    trade_id = r.json()["trade_id"]
    _as_manager("Denver")
    client.post(f"/trades/{trade_id}/accept")

    _as_manager("Denver")                       # a plain manager, not commissioner
    assert client.post(f"/trades/{trade_id}/approve").status_code == 403


def test_multi_team_owner_can_edit_each_owned_team(client, two_teams):
    repo = two_teams
    _as_manager("Boston", "Denver")             # one manager owns BOTH teams
    for slug in ("Boston", "Denver"):
        payload = _arrangement_payload(repo.load(slug))
        payload["starters"]["Forward"] = list(reversed(payload["starters"]["Forward"]))
        r = client.put(f"/teams/{slug}/arrangement", json=payload)
        assert r.status_code == 200, r.text


def test_internal_trade_auto_accepts_then_commissioner_commits(client, two_teams):
    repo = two_teams
    # one manager owns both teams -> trade between them is "internal"
    _as_manager("Boston", "Denver")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["internal"] is True and body["status"] == "accepted"   # skipped counterparty
    trade_id = body["trade_id"]

    # still needs commissioner approval to commit
    _as_manager(role="commissioner")
    assert client.post(f"/trades/{trade_id}/approve").json()["status"] == "committed"
    assert repo.load("Denver").get("boston-r1") is not None
    assert repo.load("Boston").get("denver-f5") is not None
