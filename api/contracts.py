"""
Contract endpoints: extensions, and the windows that open and close around them.

    GET  /contracts/windows              is the extension window open / has the
                                         trade deadline passed
    GET  /contracts/extensions/eligible  who a team may extend, and for how much
    POST /contracts/extensions           sign one (the team's own manager, binding)

The rules all live in handball/extensions.py, over alembic 0016's players.ext_*
columns; this file is authorization, shape and status codes and nothing else. A rule
violation comes back as 409 with the service's own sentence, which is written to be
shown to the manager who tried it -- the convention the signing and free-agency
endpoints in api/main.py already follow.

Note that `GET /contracts/audit` and `POST /contracts/bulk` (the commissioner's bulk
contract repair) predate this router and stay in api/main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from handball import extensions
from handball.simulation_vars import (
    MAX_CONTRACT_VALUE,
    MAX_CONTRACT_YEARS,
    MIN_CONTRACT_VALUE,
)

from api.auth import Manager, get_current_manager
from api.deps import engine, require_owns, require_owns_strict

router = APIRouter()


class ExtensionBody(BaseModel):
    """A proposed extension. The bounds here are the league's absolute contract
    limits, so a nonsense request is a 422 rather than reaching the rules; the real
    ceiling (term against the years still to run, value against next season's
    projected payroll) is per-player and belongs to handball/extensions.py."""
    team: str
    player_id: str
    term: int = Field(ge=1, le=MAX_CONTRACT_YEARS)
    value: int = Field(ge=MIN_CONTRACT_VALUE, le=MAX_CONTRACT_VALUE)


@router.get("/contracts/windows")
def contract_windows(mgr: Manager = Depends(get_current_manager)):
    """Where the league calendar sits on the two contract dates. Readable by any
    authenticated manager: both are league-wide facts, and every page that offers a
    trade or an extension has to know them before it renders a form."""
    return extensions.windows(engine)


@router.get("/contracts/extensions/eligible")
def eligible_extensions(team: str, mgr: Manager = Depends(get_current_manager)):
    """Which of this team's players may be extended, with the maximum term and value
    available to each, plus the extensions already signed and the projected next-season
    payroll they add up to. Read-only, so the commissioner may look (require_owns, not
    _strict)."""
    require_owns(mgr, team)
    try:
        return extensions.eligible_players(engine, team)
    except extensions.ExtensionError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/contracts/extensions", status_code=201)
def post_extension(body: ExtensionBody, mgr: Manager = Depends(get_current_manager)):
    """Sign an extension. BINDING: there is no cancel endpoint, and the rollover will
    put the player on the deal.

    Ownership is STRICT -- the commissioner is also a manager with teams of their own,
    and committing another team's cap space for the next five years is not an
    administrative convenience. The same reasoning as a free-agency offer."""
    require_owns_strict(mgr, body.team)
    try:
        return extensions.offer_extension(
            engine, body.team, body.player_id, body.term, body.value)
    except extensions.ExtensionError as e:
        raise HTTPException(status_code=409, detail=str(e))
