"""
Shared API dependencies: the engine, and the handful of questions every router asks
before it does anything.

These lived as private helpers in api/main.py while main.py was the only router. It
isn't any more -- draft, voting, contracts and the hall of fame each own a router
file -- and four copies of "is this manager allowed to act for this team?" is four
chances to get authorization subtly wrong. So they live here, and main.py keeps thin
private aliases (`_require_owns` and friends) so no existing call site moves.

Nothing in here is a FastAPI `Depends`: authentication is (api/auth.py's
`get_current_manager`), but authorization needs the Manager AND the team named in the
path or body, so it stays an explicit call at the top of each endpoint where it is
visible in the handler that relies on it.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import text

from handball.db import get_engine

from api.auth import Manager

engine = get_engine()

# Season the run controls manage. "Advance season" (a NEW season year) is out of
# scope, so the active season is simply the one season_state knows about, else the
# latest season with games, else a sensible default for a fresh league.
DEFAULT_SEASON = 2026


def team_uuid(slug: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(text("select id from teams where slug = :s"), {"s": slug}).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no team {slug!r}")
    return str(row[0])


def require_owns(mgr: Manager, slug: str) -> None:
    if mgr.is_commissioner:
        return
    if not mgr.owns(team_uuid(slug)):
        raise HTTPException(status_code=403, detail="not your team")


def require_commissioner(mgr: Manager) -> None:
    if not mgr.is_commissioner:
        raise HTTPException(status_code=403, detail="commissioner only")


def require_owns_strict(mgr: Manager, slug: str) -> None:
    """Ownership WITHOUT the commissioner bypass require_owns grants. In a sealed-bid
    auction the commissioner is also a manager with teams of their own; letting them
    submit offers or bid as anybody would be a hole, not a convenience. Their powers
    over the market are the explicit ones -- close a round, force a forfeit, award a
    deadlock -- each of which is logged as a commissioner action."""
    if not mgr.owns(team_uuid(slug)):
        raise HTTPException(status_code=403, detail="not your team")


def active_season() -> int:
    with engine.connect() as conn:
        row = conn.execute(text("select max(season) from season_state")).first()
        if row and row[0] is not None:
            return int(row[0])
        row = conn.execute(text("select max(season) from games")).first()
    return int(row[0]) if row and row[0] is not None else DEFAULT_SEASON


def queue_clear() -> bool:
    """No accepted-but-unapproved trades are sitting in the commissioner queue."""
    with engine.connect() as conn:
        n = conn.execute(
            text("select count(*) from trades where status = 'accepted'")
        ).scalar_one()
    return n == 0
