"""
Draft endpoints: the lottery, the prospect class, and the live draft room.

Empty by design. Phase 1's `draft` agent fills this in (the lottery draw and
protection resolution, prospect upload, `POST /draft/open` / `/draft/pick`, and the
`GET /draft/state` read that also sweeps the auto-pick clock), over alembic 0014's
draft_lotteries / draft_state / draft_prospects and the new draft_picks columns.
Rules belong in handball/draft.py + handball/draft_rules.py, not here.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
