"""
Voting endpoints: award ballots, the All-Star ballot, and the tallies.

Empty by design. Phase 1's `voting` agent fills this in (submit a ballot, read the
open ballot back, commissioner tally, and playing the All-Star exhibition), over
alembic 0015's ballots / award_tallies / voting_status / all_star_games. Rules
belong in handball/voting_rules.py + handball/voting.py + handball/all_star.py.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
