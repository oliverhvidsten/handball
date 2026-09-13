"""
Name: db.py
Description: One place that knows how to reach Postgres. Both PostgresTeamRepository
    and PostgresRecordSink (and Alembic's env.py) get their connection here, so
    the URL/driver lives in a single spot.

    Driver is psycopg3 via SQLAlchemy ("postgresql+psycopg://..."). The URL comes
    from $HANDBALL_DB_URL, defaulting to the local Docker Postgres used in dev/tests.
    A project-root .env (git-ignored) is auto-loaded on import, so the migration
    CLI, Alembic, and the API all pick up HANDBALL_DB_URL / SUPABASE_* without any
    shell sourcing (and without mangling special characters in the DB password).
Author: relational backend
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

# Local dev/test default: the `handball-pg` Docker container.
DEFAULT_DB_URL = "postgresql+psycopg://postgres:dev@localhost:5432/handball_dev"


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a project-root .env into the environment without
    overriding already-set vars. Hand-rolled (no dependency) and shell-safe: values
    are taken verbatim, so '$' / '@' / '&' in a DB password are never interpreted."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


def db_url() -> str:
    return os.environ.get("HANDBALL_DB_URL", DEFAULT_DB_URL)


def is_local_db(url: str | None = None) -> bool:
    """True only for a local Postgres. Destructive integration tests gate on this
    so a misconfigured HANDBALL_DB_URL can never truncate a remote (Supabase) DB."""
    u = url or db_url()
    return "localhost" in u or "127.0.0.1" in u


# One engine per (url, options) for the life of the process. api/auth.py called
# get_engine() on EVERY authenticated request, and every call built a fresh Engine
# with its own pool -- a connection per request that lingered until garbage
# collection, which against a 15-client pooler is a lockout waiting to happen.
_ENGINES: dict[tuple, Engine] = {}


def get_engine(url: str | None = None, **kwargs) -> Engine:
    # Supabase's session-mode pooler admits 15 clients for the whole project, and
    # SQLAlchemy's default pool (5 + 10 overflow) would happily hold all of them idle
    # in ONE process -- a local API next to the Render one then locks the league out
    # with "max clients reached". Cap every engine at 5 so two API processes plus a
    # script still fit, drop idle connections after ten minutes, and ping before
    # reuse so a connection the pooler closed is replaced rather than erroring.
    # Callers may still override any of these.
    key = (url or db_url(), tuple(sorted((k, repr(v)) for k, v in kwargs.items())))
    cached = _ENGINES.get(key)
    if cached is not None:
        return cached
    # Alembic passes poolclass=NullPool (no pooling at all), which takes none of
    # the sizing arguments -- so they apply only when the default QueuePool is in play.
    if "poolclass" not in kwargs:
        kwargs.setdefault("pool_size", 3)
        kwargs.setdefault("max_overflow", 2)
        kwargs.setdefault("pool_recycle", 600)
    kwargs.setdefault("pool_pre_ping", True)
    engine = create_engine(url or db_url(), future=True, **kwargs)

    # Force full-precision float text output. The Supabase pooler runs with
    # extra_float_digits=0, which renders double precision to ~15 sig digits and
    # silently loses the last ~2 ULP on read (a double does NOT round-trip).
    # Setting 3 yields the shortest exact representation. Harmless on local PG.
    @event.listens_for(engine, "connect")
    def _full_float_precision(dbapi_conn, _record):  # pragma: no cover - on connect
        with dbapi_conn.cursor() as cur:
            cur.execute("SET extra_float_digits = 3")
        # COMMIT so the session SET survives SQLAlchemy's rollback-on-return to the
        # pool. Without this, only the first query on each pooled connection gets
        # full precision and every reuse silently reverts to truncated floats.
        dbapi_conn.commit()

    _ENGINES[key] = engine
    return engine
