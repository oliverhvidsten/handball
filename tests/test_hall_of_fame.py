"""
Hall of Fame: handball/hall_of_fame.py (induct/rescind/inductees) plus the API
router in api/hall_of_fame.py, against the dev Postgres. Skips when Postgres is
unavailable. Local DB only (truncates).
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball import hall_of_fame as hof
from handball.db import get_engine, is_local_db

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

_TABLES = "teams players injuries awards games player_game_lines hall_of_fame managers"
_SEASON = 2026


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield
    app.dependency_overrides.clear()


def _team(conn, slug: str) -> str:
    return str(conn.execute(
        text("insert into teams (slug, name) values (:s, :s) returning id"), {"s": slug}
    ).scalar_one())


def _player(conn, legacy_id, team_id=None, *, retired=False, position="Forward"):
    return str(conn.execute(
        text("insert into players (legacy_id, team_id, name, position, retired, "
             "offense, defense, goalie_skill, variance, peak_age, decline_age, decline_rate) "
             "values (:lid, cast(:tid as uuid), :lid, cast(:pos as player_position), :ret, "
             "5.0, 5.0, 0.1, 0.5, 27, 30, 0.15) returning id"),
        {"lid": legacy_id, "tid": team_id, "pos": position, "ret": retired},
    ).scalar_one())


_week = [0]


def _line(conn, player_uuid, team_id, *, goals=0, saves=0, perf=0.0, is_playoff=False):
    _week[0] += 1
    g = str(conn.execute(
        text("insert into games (season, week, home_team_id, away_team_id, home_score, "
             "away_score, is_playoff) values (:s, :wk, cast(:t as uuid), cast(:t as uuid), 1, 1, :po) "
             "returning id"),
        {"s": _SEASON, "wk": _week[0], "t": team_id, "po": is_playoff},
    ).scalar_one())
    conn.execute(
        text("insert into player_game_lines (game_id, player_id, team_id, season, goals, "
             "shots, saves, goals_allowed, performance, is_playoff) "
             "values (cast(:g as uuid), cast(:p as uuid), cast(:tm as uuid), :s, :goals, 0, "
             ":saves, 0, :perf, :po)"),
        {"g": g, "p": player_uuid, "tm": team_id, "s": _SEASON,
         "goals": goals, "saves": saves, "perf": perf, "po": is_playoff},
    )


# -- pure-ish handball.hall_of_fame tests -----------------------------------
def test_induct_requires_retired_player():
    with _engine.begin() as c:
        _player(c, "active-1", retired=False)
    with pytest.raises(hof.HallOfFameError, match="retired"):
        hof.induct(_engine, "active-1", _SEASON)


def test_induct_unknown_player():
    with pytest.raises(hof.HallOfFameError, match="no player"):
        hof.induct(_engine, "nobody", _SEASON)


def test_induct_and_rescind():
    with _engine.begin() as c:
        _player(c, "legend-1", retired=True)
    hof.induct(_engine, "legend-1", _SEASON, citation="Ten seasons of excellence.")
    inductees = hof.inductees(_engine)
    assert len(inductees) == 1
    row = inductees[0]
    assert row["legacy_id"] == "legend-1"
    assert row["inducted_season"] == _SEASON
    assert row["citation"] == "Ten seasons of excellence."

    hof.rescind(_engine, "legend-1")
    assert hof.inductees(_engine) == []


def test_rescind_unknown_is_an_error():
    with pytest.raises(hof.HallOfFameError, match="not in the Hall of Fame"):
        hof.rescind(_engine, "nobody")


def test_cannot_induct_twice():
    with _engine.begin() as c:
        _player(c, "legend-2", retired=True)
    hof.induct(_engine, "legend-2", _SEASON)
    with pytest.raises(hof.HallOfFameError, match="already"):
        hof.induct(_engine, "legend-2", _SEASON)


def test_career_totals_split_regular_and_playoff():
    with _engine.begin() as c:
        team = _team(c, "Alpha")
        pid = _player(c, "legend-3", team, retired=True)
        _line(c, pid, team, goals=10, saves=0, perf=5.0, is_playoff=False)
        _line(c, pid, team, goals=7, saves=0, perf=4.0, is_playoff=False)
        _line(c, pid, team, goals=3, saves=0, perf=9.0, is_playoff=True)
    hof.induct(_engine, "legend-3", _SEASON)
    row = hof.inductees(_engine)[0]
    assert row["regular_season"]["games"] == 2
    assert row["regular_season"]["goals"] == 17
    assert row["playoff"]["games"] == 1
    assert row["playoff"]["goals"] == 3


def test_inductees_with_no_game_log_get_zero_lines():
    with _engine.begin() as c:
        _player(c, "legend-4", retired=True)
    hof.induct(_engine, "legend-4", _SEASON)
    row = hof.inductees(_engine)[0]
    assert row["regular_season"]["games"] == 0
    assert row["playoff"]["games"] == 0


def test_eligible_retirees_this_season_first_then_search():
    with _engine.begin() as c:
        _player(c, "old-retiree", retired=True)
        c.execute(text("update players set retired_season = :s where legacy_id = 'old-retiree'"),
                  {"s": _SEASON - 3})
        _player(c, "this-season-retiree", retired=True)
        c.execute(text("update players set retired_season = :s where legacy_id = 'this-season-retiree'"),
                  {"s": _SEASON})
        _player(c, "still-active-2", retired=False)

    names = [r["legacy_id"] for r in hof.eligible_retirees(_engine, _SEASON)]
    assert names[0] == "this-season-retiree"
    assert "old-retiree" in names
    assert "still-active-2" not in names

    hof.induct(_engine, "this-season-retiree", _SEASON)
    names_after = [r["legacy_id"] for r in hof.eligible_retirees(_engine, _SEASON)]
    assert "this-season-retiree" not in names_after

    searched = [r["legacy_id"] for r in hof.eligible_retirees(_engine, _SEASON, query="old-ret")]
    assert searched == ["old-retiree"]


def test_api_eligible_retirees_is_commissioner_only(client):
    with _engine.begin() as c:
        _player(c, "legend-8", retired=True)
    _as_manager("manager")
    r = client.get(f"/hall-of-fame/eligible?season={_SEASON}")
    assert r.status_code == 403

    _as_manager("commissioner")
    r = client.get(f"/hall-of-fame/eligible?season={_SEASON}")
    assert r.status_code == 200, r.text
    assert any(x["legacy_id"] == "legend-8" for x in r.json()["retirees"])


def test_multiple_classes_newest_first():
    with _engine.begin() as c:
        _player(c, "old-timer", retired=True)
        _player(c, "recent", retired=True)
    hof.induct(_engine, "old-timer", _SEASON - 5)
    hof.induct(_engine, "recent", _SEASON)
    legacy_ids = [r["legacy_id"] for r in hof.inductees(_engine)]
    assert legacy_ids == ["recent", "old-timer"]


# -- API router ---------------------------------------------------------
def _as_manager(role: str = "manager"):
    user_id = str(uuid.uuid4())
    with _engine.begin() as c:
        c.execute(
            text("insert into managers (user_id, role) values (cast(:u as uuid), :r)"),
            {"u": user_id, "r": role},
        )
    mgr = Manager(user_id=user_id, owned_team_ids=[], role=role)
    app.dependency_overrides[get_current_manager] = lambda: mgr


@pytest.fixture
def client():
    return TestClient(app)


def test_api_get_is_open_to_any_manager(client):
    with _engine.begin() as c:
        _player(c, "legend-5", retired=True)
    hof.induct(_engine, "legend-5", _SEASON)
    _as_manager("manager")
    r = client.get("/hall-of-fame")
    assert r.status_code == 200, r.text
    assert len(r.json()["inductees"]) == 1


def test_api_induct_is_commissioner_only(client):
    with _engine.begin() as c:
        _player(c, "legend-6", retired=True)
    _as_manager("manager")
    r = client.post("/hall-of-fame", json={"player_id": "legend-6", "season": _SEASON})
    assert r.status_code == 403

    _as_manager("commissioner")
    r = client.post("/hall-of-fame", json={"player_id": "legend-6", "season": _SEASON, "citation": "A great one."})
    assert r.status_code == 200, r.text


def test_api_induct_non_retired_is_409(client):
    with _engine.begin() as c:
        _player(c, "still-active", retired=False)
    _as_manager("commissioner")
    r = client.post("/hall-of-fame", json={"player_id": "still-active", "season": _SEASON})
    assert r.status_code == 409


def test_api_rescind_commissioner_only(client):
    with _engine.begin() as c:
        _player(c, "legend-7", retired=True)
    hof.induct(_engine, "legend-7", _SEASON)

    _as_manager("manager")
    r = client.delete("/hall-of-fame/legend-7")
    assert r.status_code == 403

    _as_manager("commissioner")
    r = client.delete("/hall-of-fame/legend-7")
    assert r.status_code == 200, r.text
    assert hof.inductees(_engine) == []


def test_api_rescind_unknown_is_404(client):
    _as_manager("commissioner")
    r = client.delete("/hall-of-fame/nobody")
    assert r.status_code == 404


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
