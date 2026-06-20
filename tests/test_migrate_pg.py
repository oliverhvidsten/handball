"""
Phase 3 verification: migrate the real datafiles_v2 JSON into Postgres and assert
every team round-trips. Skips cleanly when the dev Postgres is unavailable.
"""
import pytest
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.migrate_json_to_pg import DEFAULT_DATAFILES, migrate, verify
from handball.pg_repository import PostgresTeamRepository
from handball.repository import JsonTeamRepository

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


def test_migrate_all_teams_roundtrip():
    n = migrate(DEFAULT_DATAFILES, _engine)
    assert n == len(JsonTeamRepository(DEFAULT_DATAFILES).all_team_ids())
    assert n >= 32
    assert verify(DEFAULT_DATAFILES, _engine) == []


def test_migrate_is_idempotent():
    n1 = migrate(DEFAULT_DATAFILES, _engine)
    n2 = migrate(DEFAULT_DATAFILES, _engine)        # re-run: upserts, no duplicates
    assert n1 == n2
    with _engine.connect() as c:
        n_players = c.execute(text("select count(*) from players")).scalar_one()
    assert n_players == 672                          # 32 teams x 21, no dupes
    assert verify(DEFAULT_DATAFILES, _engine) == []
