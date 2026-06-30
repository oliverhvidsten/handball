"""
Integration tests for PostgresCoachRepository against a real (local) Postgres.
Mirrors tests/test_pg_repository.py: the module skips cleanly unless a LOCAL,
migrated dev database is reachable (destructive tests never run on a remote DB).

    docker run -d --name handball-pg -e POSTGRES_PASSWORD=dev \
        -e POSTGRES_DB=handball_dev -p 5432:5432 postgres:16
    python -m alembic upgrade head
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from handball.coach_repository import PostgresCoachRepository
from handball.db import get_engine, is_local_db
from handball.domain import Coach
from handball.league_views import CoachTenure

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from coaches limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception as _e:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(
    not _PG_OK, reason="local Postgres dev DB not available/migrated (see module docstring)"
)


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text("truncate coaches, coach_tenures restart identity cascade"))
        # two teams to move a coach between (FK target for tenures)
        c.execute(text("insert into teams (slug, name) values ('Boston','Boston') "
                       "on conflict (slug) do nothing"))
        c.execute(text("insert into teams (slug, name) values ('Seattle','Seattle') "
                       "on conflict (slug) do nothing"))
    yield


def test_round_trip_single_open_tenure():
    repo = PostgresCoachRepository(_engine)
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2025)
    repo.save(c)

    got = repo.load("jane-doe")
    assert got.name == "Jane Doe"
    assert got.tenures == [CoachTenure("Boston", "HC", 2025, None)]


def test_reassign_same_post_stays_one_tenure():
    repo = PostgresCoachRepository(_engine)
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2025)
    repo.save(c)
    # reload, re-assign same post a later season, re-save
    c = repo.load("jane-doe")
    c.assign("Boston", "HC", 2026)
    repo.save(c)
    assert repo.load("jane-doe").tenures == [CoachTenure("Boston", "HC", 2025, None)]


def test_move_records_closed_and_open_with_boundary():
    repo = PostgresCoachRepository(_engine)
    c = Coach(id="jane-doe", name="Jane Doe")
    c.assign("Boston", "HC", 2025)
    repo.save(c)
    c = repo.load("jane-doe")
    c.assign("Seattle", "HC", 2026)
    repo.save(c)

    tenures = repo.load("jane-doe").tenures
    assert tenures == [
        CoachTenure("Boston", "HC", 2025, 2025),     # end clamped to start (>= start)
        CoachTenure("Seattle", "HC", 2026, None),
    ]


def test_open_tenure_unique_index_rejects_second_open_hc():
    # Two different coaches both OPEN as Boston HC -> ux_coach_tenures_open_team_role.
    repo = PostgresCoachRepository(_engine)
    a = Coach(id="coach-a", name="Coach A"); a.assign("Boston", "HC", 2025)
    repo.save(a)
    b = Coach(id="coach-b", name="Coach B"); b.assign("Boston", "HC", 2025)
    with pytest.raises(IntegrityError):
        repo.save(b)
