"""
Integration tests for the Postgres data layer (PostgresTeamRepository +
PostgresRecordSink), mirroring tests/test_repository.py but against a real
Postgres. Requires the dev database to exist and be migrated:

    docker run -d --name handball-pg -e POSTGRES_PASSWORD=dev \
        -e POSTGRES_DB=handball_dev -p 5432:5432 postgres:16
    python -m alembic upgrade head

The whole module skips cleanly if that database is not reachable.
"""
import numpy as np
import pytest
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.orchestration import GameSimulatorAdapter
from handball.pg_record_sink import PostgresRecordSink
from handball.pg_repository import PostgresTeamRepository

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception as _e:  # noqa: BLE001
    _PG_OK = False
    _PG_ERR = str(_e)

pytestmark = pytest.mark.skipif(
    not _PG_OK, reason="Postgres dev DB not available/migrated (see module docstring)"
)

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets")


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
        id=team_id, name=f"{team_id}", coaches=["HC", "OC", "DC"],
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


def test_save_then_load_roundtrips_equal():
    repo = PostgresTeamRepository(_engine)
    original = _team("Boston")
    repo.save(original)
    loaded = repo.load("Boston")
    assert loaded == original


def test_load_missing_raises_keyerror():
    repo = PostgresTeamRepository(_engine)
    with pytest.raises(KeyError):
        repo.load("Nowhere")


def test_all_team_ids_and_load_all():
    repo = PostgresTeamRepository(_engine)
    repo.save(_team("Boston"))
    repo.save(_team("Denver"))
    assert repo.all_team_ids() == ["Boston", "Denver"]
    assert {t.id for t in repo.load_all()} == {"Boston", "Denver"}


def test_resave_updates_record_and_reorders_slots():
    repo = PostgresTeamRepository(_engine)
    team = _team("Boston")
    repo.save(team)

    # mutate: record + reorder the forward starters (exercises slot-clear-then-set)
    team.record = (3, 1, 0)
    team.starters["Forward"] = list(reversed(team.starters["Forward"]))
    repo.save(team)

    loaded = repo.load("Boston")
    assert loaded.record == (3, 1, 0)
    assert [p.id for p in loaded.starters["Forward"]] == [p.id for p in team.starters["Forward"]]


def test_injury_log_roundtrips():
    repo = PostgresTeamRepository(_engine)
    team = _team("Boston")
    hurt = team.bench["Forward"][0]
    np.random.seed(0)
    hurt.injure(year=2026, injury_type="ankle")          # sets is_injured + logs it
    repo.save(team)

    loaded = repo.load("Boston")
    lp = loaded.get(hurt.id)
    assert lp.is_injured is True
    assert lp.injury_log.active_injury is True
    assert len(lp.injury_log.injuries) == 1
    assert lp.injury_log.injuries[0][0] == 2026          # year
    assert lp.injury_log.injuries[0][1] == "ankle"       # type


def test_record_sink_persists_game_lines_and_view_aggregates():
    repo = PostgresTeamRepository(_engine)
    home, away = _team("Boston"), _team("Denver")
    repo.save(home)
    repo.save(away)

    np.random.seed(7)
    result = GameSimulatorAdapter().play(home, away)   # mutates + builds full lines
    PostgresRecordSink(_engine, season=1).record_game(result, week=1)

    with _engine.connect() as c:
        n_games = c.execute(text("select count(*) from games")).scalar_one()
        n_lines = c.execute(text("select count(*) from player_game_lines")).scalar_one()
        total_goals = c.execute(text("select coalesce(sum(goals),0) from player_game_lines")).scalar_one()
        # leaderboard view sums per player-season
        view_goals = c.execute(text("select coalesce(sum(goals),0) from player_season_stats")).scalar_one()

    assert n_games == 1
    # every rostered player on both teams gets a line (21 + 21)
    assert n_lines == len(home.roster()) + len(away.roster())
    assert total_goals == result.home_score + result.away_score
    assert view_goals == total_goals


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
