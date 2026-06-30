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


# -- season simulation endpoints -------------------------------------------
from handball import schedule_repository as sched_repo  # noqa: E402

# _active_season() falls back to DEFAULT_SEASON when no games/season_state exist.
_SEASON = 2026


@pytest.fixture(autouse=True)
def _clean_season_tables():
    """season_state / schedule_games aren't in the shared truncate list; clear them
    around each test so _active_season() and the run cursor start fresh."""
    with _engine.begin() as c:
        c.execute(text("truncate season_state, schedule_games restart identity cascade"))
    yield
    with _engine.begin() as c:
        c.execute(text("truncate season_state, schedule_games restart identity cascade"))


def _seed_one_week_schedule(season: int = _SEASON):
    """Persist a single-week Boston@Denver fixture + a generated season_state, as if
    /schedule/generate had run -- without invoking the heavy OR-Tools generator."""
    sched_repo.init_season_state(_engine, season, schedule_seed=season, injury_seed=season)
    sched_repo.save_schedule(
        _engine, season,
        {"weeks": [[{"team1": "Boston", "team2": "Denver", "matchup_type": "div"}]]},
    )
    sched_repo.mark_schedule_generated(_engine, season, season)


def test_season_state_reports_cursor(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("Boston")                        # any authenticated manager may read
    s = client.get("/season/state").json()
    assert s["season"] == _SEASON
    assert s["schedule_generated"] is True
    assert s["periods_run"] == 0
    assert s["next_period"] == 1
    assert s["total_periods"] == 5
    assert s["queue_clear"] is True


def test_run_period_persists_games_and_advances_cursor(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("", role="commissioner")
    # The run is a background job; the TestClient runs it to completion before the
    # 202 returns, so by the next line the period has finished.
    r = client.post("/periods/run")
    assert r.status_code == 202, r.text
    assert r.json()["run_status"] == "running"

    with _engine.connect() as c:
        n_games = c.execute(text("select count(*) from games where season=:s"), {"s": _SEASON}).scalar_one()
        played = c.execute(text("select coalesce(sum(wins+losses+ties),0) from teams")).scalar_one()
    assert n_games == 1                          # only the one persisted fixture
    assert played == 2                          # one game => two team participations

    state = client.get("/season/state").json()
    assert state["run_status"] == "done"
    assert state["periods_run"] == 1
    assert state["next_period"] == 2

    # a game row carries its week (regression guard: week used to land NULL)
    with _engine.connect() as c:
        assert c.execute(text("select week from games where season=:s"), {"s": _SEASON}).scalar_one() == 1


def test_run_period_rejects_concurrent_run(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:
        c.execute(text("update season_state set run_status='running' where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "already running" in r.json()["detail"].lower()


def test_run_period_requires_commissioner(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("Boston")                        # plain manager
    assert client.post("/periods/run").status_code == 403


def test_run_period_blocked_without_schedule(client, two_teams):
    _as_manager("", role="commissioner")         # no schedule seeded
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "schedule" in r.json()["detail"].lower()


def test_run_period_blocked_when_trade_queue_dirty(client, two_teams):
    _seed_one_week_schedule()
    # an accepted-but-unapproved trade must block a run
    boston, denver = _uuid_of("Boston"), _uuid_of("Denver")
    with _engine.begin() as c:
        c.execute(
            text("insert into trades (from_team_id, to_team_id, status) "
                 "values (cast(:f as uuid), cast(:t as uuid), 'accepted')"),
            {"f": boston, "t": denver},
        )
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "queue" in r.json()["detail"].lower()


def test_run_period_blocked_when_season_complete(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run = 5 where season = :s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "complete" in r.json()["detail"].lower()


def test_save_schedule_rejects_unknown_team(two_teams):
    with pytest.raises(sched_repo.ScheduleError):
        sched_repo.save_schedule(
            _engine, _SEASON,
            {"weeks": [[{"team1": "Boston", "team2": "Atlantis", "matchup_type": "div"}]]},
        )


def test_stale_running_blocks_run_until_reset(client, two_teams):
    _seed_one_week_schedule()
    # a 'running' row whose heartbeat is 10 min old == a dead worker
    with _engine.begin() as c:
        c.execute(
            text("update season_state set run_status='running', run_period=1, "
                 "updated_at = now() - interval '10 minutes' where season=:s"),
            {"s": _SEASON},
        )
    _as_manager("", role="commissioner")

    assert client.get("/season/state").json()["run_stale"] is True
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "reset" in r.json()["detail"].lower()

    assert client.post("/periods/reset").json()["run_status"] == "idle"
    assert client.get("/season/state").json()["run_status"] == "idle"


def test_reset_rolls_back_partial_period_games_and_records(client, two_teams):
    _seed_one_week_schedule()
    boston, denver = _uuid_of("Boston"), _uuid_of("Denver")
    # simulate a partial run: one period-1 game written + records moved, then died
    with _engine.begin() as c:
        c.execute(
            text("insert into games (season, week, home_team_id, away_team_id, home_score, away_score) "
                 "values (:s, 1, cast(:h as uuid), cast(:a as uuid), 30, 20)"),
            {"s": _SEASON, "h": boston, "a": denver},
        )
        c.execute(text("update teams set wins=1 where id=cast(:h as uuid)"), {"h": boston})
        c.execute(text("update teams set losses=1 where id=cast(:a as uuid)"), {"a": denver})
        c.execute(
            text("update season_state set run_status='error', run_period=1, run_error='boom' where season=:s"),
            {"s": _SEASON},
        )
    _as_manager("", role="commissioner")

    r = client.post("/periods/reset")
    assert r.status_code == 200, r.text
    assert r.json()["rolled_back_games"] == 1

    with _engine.connect() as c:
        assert c.execute(text("select count(*) from games where season=:s"), {"s": _SEASON}).scalar_one() == 0
        rec = c.execute(text("select wins, losses, ties from teams where id=cast(:h as uuid)"), {"h": boston}).first()
        assert tuple(rec) == (0, 0, 0)         # the win was rolled back
        rec = c.execute(text("select wins, losses, ties from teams where id=cast(:a as uuid)"), {"a": denver}).first()
        assert tuple(rec) == (0, 0, 0)         # the loss was rolled back
    assert client.get("/season/state").json()["run_status"] == "idle"


def test_reset_rejected_while_genuinely_running(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:   # fresh heartbeat => a live run, not stale
        c.execute(
            text("update season_state set run_status='running', run_period=1, updated_at=now() where season=:s"),
            {"s": _SEASON},
        )
    _as_manager("", role="commissioner")
    r = client.post("/periods/reset")
    assert r.status_code == 409
    assert "in progress" in r.json()["detail"].lower()


# -- offseason endpoints ---------------------------------------------------
def test_advance_requires_complete_season(client, two_teams):
    # season_state exists but the regular season isn't finished
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=2 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/season/advance")
    assert r.status_code == 409
    assert "regular season" in r.json()["detail"].lower()


def test_advance_requires_commissioner(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _as_manager("Boston")  # plain manager
    assert client.post("/season/advance").status_code == 403
    assert client.get("/retirement/candidates").status_code == 403


def test_advance_season_happy_path(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")

    r = client.post("/season/advance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_season"] == _SEASON + 1
    # records zeroed, new season opened, players aged
    assert client.get("/season/state").json()["season"] == _SEASON + 1
    with _engine.connect() as c:
        assert c.execute(text("select 1 from season_state where season=:s"), {"s": _SEASON + 1}).first() is not None


def test_lineup_save_does_not_wipe_awards(client, two_teams):
    # an award written for a player must survive a routine roster save() of their team
    repo = two_teams
    boston = repo.load("Boston")
    pid = boston.starters["Forward"][0].id
    with _engine.begin() as c:
        puid = c.execute(text("select id from players where legacy_id=:l"), {"l": pid}).scalar_one()
        c.execute(text("insert into awards (player_id, season, award) values (:p, :s, 'League MVP')"),
                  {"p": puid, "s": _SEASON})
    repo.save(repo.load("Boston"))   # routine save (e.g. a lineup edit)
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from awards where player_id=:p"), {"p": puid}).scalar_one() == 1


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
