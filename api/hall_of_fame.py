"""
Hall of Fame endpoints: induct, rescind, and read the Hall.

Empty by design. Phase 1's `hof` agent fills this in, over alembic 0017's
hall_of_fame table, with the career totals aggregated at read time from
player_game_lines. Rules belong in handball/hall_of_fame.py.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
