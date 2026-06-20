"""
Phase 4 verification: the full Postgres-backed batch stack -- migrate teams, build
build_production_league_pg (no Sheet, real simulator), run a week, and confirm the
results land in Postgres (records on teams, games + player_game_lines persisted).
Skips when the dev Postgres is unavailable.
"""
import pytest
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.league import build_production_league_pg
from handball.migrate_json_to_pg import DEFAULT_DATAFILES, migrate
from handball.season import Schedule

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets")


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield


def test_run_week_persists_games_and_records_to_postgres():
    migrate(DEFAULT_DATAFILES, _engine)

    league = build_production_league_pg(year=7, seed=0)
    teams = league.orch.team_repo.all_team_ids()[:4]      # 4 real migrated teams
    league.set_schedule(Schedule.round_robin(teams))

    results = league.run_week(1)                           # round-robin of 4 -> 2 games/week
    assert len(results) == 2

    with _engine.connect() as c:
        n_games = c.execute(text("select count(*) from games")).scalar_one()
        n_lines = c.execute(text("select count(*) from player_game_lines")).scalar_one()
        season_tag = c.execute(text("select distinct season from games")).scalar_one()
        # records persisted on the teams that played (2 games => 4 participations)
        played = c.execute(
            text("select coalesce(sum(wins+losses+ties),0) from teams")
        ).scalar_one()

    assert n_games == 2
    assert n_lines == 2 * 42                                # 2 games x (21 + 21) rosters
    assert season_tag == 7                                  # year passed to the builder
    assert played == 4
