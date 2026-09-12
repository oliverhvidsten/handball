"""
Hall of Fame endpoints: induct, rescind, and read the Hall.

Over alembic 0017's hall_of_fame table, with the career totals aggregated at read
time from player_game_lines. Rules live in handball/hall_of_fame.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from handball import hall_of_fame as hof

from api.auth import Manager, get_current_manager
from api.deps import engine, require_commissioner

router = APIRouter()


class InductBody(BaseModel):
    player_id: str                      # players.legacy_id, per the SigningBody/RetirementBody convention
    season: int
    citation: str | None = Field(default=None)


@router.get("/hall-of-fame")
def get_hall_of_fame(mgr: Manager = Depends(get_current_manager)):
    """Every inductee, newest class first, with regular-season and playoff career
    lines attached. Readable by any authenticated manager -- the Hall is public."""
    return {"inductees": hof.inductees(engine)}


@router.get("/hall-of-fame/eligible")
def get_eligible_retirees(season: int, q: str | None = None, mgr: Manager = Depends(get_current_manager)):
    """Retired players not yet inducted, for the commissioner's induction picker:
    `season`'s own retirees sort first, then every other retiree; `q` narrows by
    name over the whole retired pool. Commissioner-only -- this is the induction
    workflow's input, not something the Hall page itself needs."""
    require_commissioner(mgr)
    return {"retirees": hof.eligible_retirees(engine, season, q)}


@router.post("/hall-of-fame")
def post_hall_of_fame(body: InductBody, mgr: Manager = Depends(get_current_manager)):
    """Induct a retired player. Commissioner-only; inducting a player who isn't
    retired (or is already in the Hall) is a 409."""
    require_commissioner(mgr)
    try:
        return hof.induct(engine, body.player_id, body.season, body.citation)
    except hof.HallOfFameError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/hall-of-fame/{legacy_id}")
def delete_hall_of_fame(legacy_id: str, mgr: Manager = Depends(get_current_manager)):
    """Rescind an induction (a hard delete -- see 0017 for why). Commissioner-only."""
    require_commissioner(mgr)
    try:
        hof.rescind(engine, legacy_id)
    except hof.HallOfFameError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok", "legacy_id": legacy_id}
