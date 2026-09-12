"""
Contract endpoints: extensions, and the windows that open and close around them.

Empty by design. Phase 1's `contracts` agent fills this in (offer an extension, list
who is eligible and for how much, and report the extension window / trade deadline),
over alembic 0016's players.ext_* columns. Rules belong in handball/extensions.py.

Note that `GET /contracts/audit` and `POST /contracts/bulk` (the commissioner's bulk
contract repair) predate this router and stay in api/main.py.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
