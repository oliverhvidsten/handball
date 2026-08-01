"""
Assign a Head Coach / Offensive Coordinator / Defensive Coordinator to every team
from a role-segregated roster, recording each as an OPEN coach tenure for the given
season. Coaches are assigned WITHIN their role list (a Head-Coach-list name only
becomes an HC, etc.). Leftover names in each list are stored as a free-agent pool:
coach rows with no tenure, kept with everyone else (their `pool_role` records which
list they came from). Ages are stored when numeric; non-numeric ages ("Unknown",
"[Age]") store NULL.

There is no central season counter in the sim, so --season is REQUIRED.

    python -m scripts.seed_coaches --season 2026 --roster scripts/coach_roster_2026.txt --seed 1

Roster file format: a section header line for each role, followed by "Name, Age"
lines (age optional / may be non-numeric):

    Head Coaches
    Jane Doe, 54
    ...
    Offensive Coordinators
    ...
    Defensive Coordinators
    ...

Idempotent for a fixed --seed: re-running with the same roster + season is a no-op
(Coach.assign short-circuits the unchanged post; coach rows upsert by legacy_id).
The whole run is one transaction, so any error (e.g. a role with too few names)
writes nothing.
"""
from __future__ import annotations

import argparse
import random
import sys

from sqlalchemy import text

from handball.db import get_engine
from handball.domain import Coach
from handball.league_views import COACH_ROLES
from handball.postseason import _slug

# Section header (lowercased) -> role code.
_HEADER_ROLE = {
    "head coaches": "HC",
    "offensive coordinators": "OC",
    "defensive coordinators": "DC",
}


def parse_roster(path: str) -> dict[str, list[tuple[str, int | None]]]:
    """Parse the role-sectioned roster file into {role: [(name, age|None), ...]}."""
    out: dict[str, list[tuple[str, int | None]]] = {r: [] for r in COACH_ROLES}
    role: str | None = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            header = _HEADER_ROLE.get(line.lower())
            if header is not None:
                role = header
                continue
            if role is None:
                continue  # stray line before any header
            name, _, age_str = line.rpartition(",")
            if not name:                      # no comma -> whole line is the name
                name, age_str = line, ""
            age_str = age_str.strip()
            age = int(age_str) if age_str.isdigit() else None
            out[role].append((name.strip(), age))
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Assign HC/OC/DC to every team from a role roster.")
    ap.add_argument("--season", type=int, required=True, help="season the assignments start in")
    ap.add_argument("--roster", required=True, help="role-sectioned roster file (see module docstring)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for a reproducible shuffle")
    args = ap.parse_args(argv)

    roster = parse_roster(args.roster)
    engine = get_engine()

    with engine.begin() as conn:
        team_slugs = [r[0] for r in conn.execute(
            text("select slug from teams order by slug")).all()]
        if not team_slugs:
            print("no teams in database"); return 1
        n_teams = len(team_slugs)

        # Validate every role has enough names BEFORE writing anything.
        short = {role: len(roster[role]) for role in COACH_ROLES if len(roster[role]) < n_teams}
        if short:
            for role, have in short.items():
                print(f"{role}: need {n_teams} names, got {have} -- provide {n_teams - have} more")
            return 1

        rng = random.Random(args.seed)
        used_ids: set[str] = set()
        cache: dict[str, dict[str, str]] = {s: {} for s in team_slugs}
        assigned = {r: 0 for r in COACH_ROLES}
        extras = {r: 0 for r in COACH_ROLES}

        for role in COACH_ROLES:
            people = roster[role][:]
            rng.shuffle(people)
            for idx, (name, age) in enumerate(people):
                cid = _unique_legacy_id(name, used_ids)
                coach = _load_or_build(conn, cid, name, age, role)
                if idx < n_teams:
                    slug = team_slugs[idx]
                    coach.assign(slug, role, args.season)
                    cache[slug][role] = name
                    assigned[role] += 1
                else:
                    extras[role] += 1
                _save_in_txn(conn, coach)

        # Refresh the denormalized [HC, OC, DC] cache on each team.
        for slug, names in cache.items():
            conn.execute(
                text("update teams set coaches = :c where slug = :s"),
                {"c": [names[r] for r in COACH_ROLES], "s": slug},
            )

    print(f"season {args.season}: assigned {sum(assigned.values())} coaches across {n_teams} teams "
          f"(HC {assigned['HC']}, OC {assigned['OC']}, DC {assigned['DC']}).")
    total_extra = sum(extras.values())
    if total_extra:
        print(f"free-agent pool: {total_extra} unassigned "
              f"(HC {extras['HC']}, OC {extras['OC']}, DC {extras['DC']}).")
    return 0


def _unique_legacy_id(name: str, used: set[str]) -> str:
    """Deterministic slug id, collision-suffixed so coaches who share a display
    name still get distinct ids."""
    base = _slug(name) or "coach"
    cid, n = base, 2
    while cid in used:
        cid, n = f"{base}-{n}", n + 1
    used.add(cid)
    return cid


def _load_or_build(conn, cid: str, name: str, age: int | None, role: str) -> Coach:
    """Load an existing coach (to extend history on re-runs) or build a fresh one.
    Refreshes name/age/pool_role from the roster either way."""
    row = conn.execute(
        text("select id from coaches where legacy_id = :lid"), {"lid": cid},
    ).scalar_one_or_none()
    if row is None:
        return Coach(id=cid, name=name, age=age, pool_role=role)
    tenures = conn.execute(
        text("select t.slug as team_slug, ct.role::text as role, ct.start_season, ct.end_season "
             "from coach_tenures ct join teams t on t.id = ct.team_id "
             "where ct.coach_id = :cid order by ct.ord"),
        {"cid": row},
    ).mappings().all()
    from handball.league_views import CoachTenure
    coach = Coach(id=cid, name=name, age=age, pool_role=role, tenures=[
        CoachTenure(t["team_slug"], t["role"], t["start_season"], t["end_season"]) for t in tenures
    ])
    return coach


def _save_in_txn(conn, coach: Coach) -> None:
    """Persist a coach + its tenures on an EXISTING connection/transaction so the
    whole seed run is atomic. Mirrors PostgresCoachRepository.save's SQL."""
    cid = conn.execute(
        text("insert into coaches (legacy_id, name, age, pool_role) "
             "values (:lid, :name, :age, cast(:pool_role as coach_role)) "
             "on conflict (legacy_id) do update set "
             "name = excluded.name, age = excluded.age, pool_role = excluded.pool_role "
             "returning id"),
        {"lid": coach.id, "name": coach.name, "age": coach.age, "pool_role": coach.pool_role},
    ).scalar_one()
    conn.execute(text("delete from coach_tenures where coach_id = :cid"), {"cid": cid})
    for ord_, t in enumerate(coach.tenures):
        tid = conn.execute(
            text("select id from teams where slug = :s"), {"s": t.team_id},
        ).scalar_one()
        conn.execute(
            text("insert into coach_tenures "
                 "(coach_id, team_id, role, start_season, end_season, ord) "
                 "values (:cid, :tid, cast(:role as coach_role), :start, :end, :ord)"),
            {"cid": cid, "tid": tid, "role": t.role,
             "start": t.start_season, "end": t.end_season, "ord": ord_},
        )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
