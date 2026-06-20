"""
Name: migrate_json_to_pg.py
Description: One-time (idempotent) migration of the per-team JSON files
    (datafiles_v2/*.json) into Postgres via PostgresTeamRepository. Reuses
    JsonTeamRepository.load_all() to read and the PG repo to write, so all the
    Player/InjuryReport reconstruction is shared with the rest of the stack.

    Faithfulness: team/player STATE migrates exactly -- rosters/arrangement,
    visible+hidden ratings, contracts, injuries, awards. The one thing NOT
    carried is any in-progress current_season_log (per-game counters): the
    relational model keeps those in player_game_lines, which require game-level
    rows that the legacy per-player logs cannot reconstruct (no opponent/game
    linkage was ever recorded). Those counters rebuild as new games simulate;
    verify() therefore compares ignoring current_season_log.

    Seeding of managers (Supabase auth users) and draft_picks is deliberately NOT
    done here -- managers need real auth.users ids (Phase 7) and draft picks need
    source data; both are separate, later steps.

Usage:
    python -m handball.migrate_json_to_pg              # migrate default datafiles_v2
    python -m handball.migrate_json_to_pg --verify     # migrate, then verify round-trip
Author: relational backend
"""
from __future__ import annotations

import argparse
from pathlib import Path

from handball.db import get_engine
from handball.repository import JsonTeamRepository, team_to_dict
from handball.pg_repository import PostgresTeamRepository

DEFAULT_DATAFILES = Path(__file__).parent / "datafiles_v2"


def _normalized(team) -> dict:
    """team_to_dict snapshot with the relocated current_season_log dropped, so
    JSON and Postgres teams compare on the state the relational model owns."""
    d = team_to_dict(team)
    for p in d["players"].values():
        p.pop("current_season_log", None)
    return d


def migrate(datafiles_dir=DEFAULT_DATAFILES, engine=None) -> int:
    """Load every team from JSON and upsert into Postgres. Idempotent (the PG
    repo upserts on slug / legacy_id). Returns the number of teams migrated."""
    engine = engine or get_engine()
    src = JsonTeamRepository(datafiles_dir)
    dst = PostgresTeamRepository(engine)
    teams = src.load_all()
    for team in teams:
        dst.save(team)
    return len(teams)


def verify(datafiles_dir=DEFAULT_DATAFILES, engine=None) -> list[str]:
    """Return the ids of any teams whose Postgres state differs from JSON
    (ignoring current_season_log). Empty list == clean migration."""
    engine = engine or get_engine()
    src = JsonTeamRepository(datafiles_dir)
    dst = PostgresTeamRepository(engine)
    mismatches: list[str] = []
    for team_id in src.all_team_ids():
        if _normalized(src.load(team_id)) != _normalized(dst.load(team_id)):
            mismatches.append(team_id)
    return mismatches


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate datafiles_v2 JSON into Postgres.")
    ap.add_argument("--datafiles", default=str(DEFAULT_DATAFILES))
    ap.add_argument("--verify", action="store_true", help="verify round-trip after migrating")
    args = ap.parse_args()

    n = migrate(args.datafiles)
    print(f"migrated {n} teams into Postgres")
    if args.verify:
        bad = verify(args.datafiles)
        if bad:
            print(f"VERIFY FAILED for {len(bad)} team(s): {bad}")
            return 1
        print(f"verify OK: all {n} teams round-trip equal (ignoring current_season_log)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
