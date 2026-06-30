"""
Name: coach_repository.py
Description: Postgres-backed repository for Coach entities and their career
    history -- the relational store for the tracking-only coaching layer. Modeled
    on PostgresTeamRepository: engine injection via handball.db.get_engine, writes
    in a single transaction, reads via .mappings().

    Mapping to the domain:
      - domain CoachId  -> coaches.legacy_id (stable slug, e.g. "jane-doe")
      - Coach.tenures   -> coach_tenures rows (full-replaced on save, ord = index)
      - CoachTenure.team_id (== teams.slug) -> coach_tenures.team_id (uuid, resolved
        from the slug on write; rendered back to the slug on read)

    The authoritative current-coaches-per-team and career views (team_coaches,
    coach_career) are defined in migration 0005 and read directly by the frontend;
    this repository is the write path (seed script) plus a Python read path used by
    tests.
Author: relational backend
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.db import get_engine
from handball.domain import Coach
from handball.league_views import CoachId, CoachTenure


class CoachRepositoryError(RuntimeError):
    pass


class PostgresCoachRepository:
    """One Coach == one coaches row + its coach_tenures rows."""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    # -- reads -------------------------------------------------------------
    def load(self, coach_id: CoachId) -> Coach:
        with self.engine.connect() as conn:
            return self._load(conn, coach_id)

    def all_coach_ids(self) -> list[CoachId]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("select legacy_id from coaches order by legacy_id")).all()
        return [r[0] for r in rows]

    def load_all(self) -> list[Coach]:
        with self.engine.connect() as conn:
            ids = [r[0] for r in conn.execute(
                text("select legacy_id from coaches order by legacy_id")).all()]
            return [self._load(conn, cid) for cid in ids]

    def _load(self, conn, coach_id: CoachId) -> Coach:
        crow = conn.execute(
            text("select id, legacy_id, name, age, pool_role::text as pool_role "
                 "from coaches where legacy_id = :lid"),
            {"lid": coach_id},
        ).mappings().first()
        if crow is None:
            raise KeyError(f"no coach {coach_id!r} in database")

        trows = conn.execute(
            text(
                "select t.slug as team_slug, ct.role::text as role, "
                "       ct.start_season, ct.end_season "
                "from coach_tenures ct join teams t on t.id = ct.team_id "
                "where ct.coach_id = :cid order by ct.ord"
            ),
            {"cid": crow["id"]},
        ).mappings().all()

        tenures = [
            CoachTenure(
                team_id=r["team_slug"], role=r["role"],
                start_season=r["start_season"], end_season=r["end_season"],
            )
            for r in trows
        ]
        return Coach(id=crow["legacy_id"], name=crow["name"],
                     age=crow["age"], pool_role=crow["pool_role"], tenures=tenures)

    # -- writes ------------------------------------------------------------
    def save(self, coach: Coach) -> None:
        with self.engine.begin() as conn:
            cid = conn.execute(
                text(
                    "insert into coaches (legacy_id, name, age, pool_role) "
                    "values (:lid, :name, :age, cast(:pool_role as coach_role)) "
                    "on conflict (legacy_id) do update set "
                    "name = excluded.name, age = excluded.age, pool_role = excluded.pool_role "
                    "returning id"
                ),
                {"lid": coach.id, "name": coach.name, "age": coach.age,
                 "pool_role": coach.pool_role},
            ).scalar_one()

            # Full replace: a coach's tenure list is small, and clearing first lets
            # the open-tenure partial unique indexes never collide with stale rows.
            conn.execute(text("delete from coach_tenures where coach_id = :cid"), {"cid": cid})

            for ord_, t in enumerate(coach.tenures):
                team_uuid = conn.execute(
                    text("select id from teams where slug = :s"), {"s": t.team_id},
                ).scalar_one_or_none()
                if team_uuid is None:
                    raise CoachRepositoryError(
                        f"coach {coach.id!r}: tenure references unknown team {t.team_id!r}"
                    )
                conn.execute(
                    text(
                        "insert into coach_tenures "
                        "(coach_id, team_id, role, start_season, end_season, ord) "
                        "values (:cid, :tid, cast(:role as coach_role), :start, :end, :ord)"
                    ),
                    {"cid": cid, "tid": team_uuid, "role": t.role,
                     "start": t.start_season, "end": t.end_season, "ord": ord_},
                )
