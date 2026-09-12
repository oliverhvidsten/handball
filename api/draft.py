"""
Draft endpoints: the lottery, the prospect class, and the live draft room.

    GET  /draft/state          the room -- order, board, whose turn, clock
    POST /draft/lottery        commissioner draws picks 1..M (+ resolves protections)
    POST /draft/prospects      commissioner uploads the class (file or pasted text)
    POST /draft/open           commissioner opens the room
    POST /draft/pick           the team on the clock picks (or the commissioner does)

Rules live in handball/draft.py + handball/draft_rules.py; this file is
authorization, request shape, and error translation, nothing else. Every rule
violation comes back as 409 carrying the service's own sentence, which is written
to be shown to the manager who tried it -- the convention free agency established.

Authorization on a pick is worth spelling out. The body names a prospect, not a
team: whose turn it is, is a FACT the league already knows, not something a caller
gets to assert. So the endpoint reads the team on the clock and demands ownership of
THAT team, with `require_owns_strict` -- no commissioner bypass, because a
commissioner is also a manager with teams of their own and "I picked for you"
should be an explicit, logged act rather than a side effect of being able to. The
commissioner's own path is the same endpoint with `force`, which is how they pick
for an absent manager, and it records the pick as theirs by making it a real pick.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from handball import draft
from handball.draft_rules import DraftRulesError

from api.auth import Manager, get_current_manager
from api.deps import active_season, engine, require_commissioner, require_owns_strict

router = APIRouter()


class LotteryBody(BaseModel):
    """An explicit seed replays a known draw -- the commissioner never needs one,
    but a contested lottery does: it is what makes "here is the draw that happened"
    checkable rather than something the league takes on trust."""
    seed: int | None = Field(default=None, ge=1, le=2**31 - 2)


class PickBody(BaseModel):
    prospect_id: str | None = None
    # The commissioner picking for a team that is not answering. Separate from
    # ownership on purpose: forcing a pick is a different act from making your own.
    force: bool = False


def _guard(fn, *args, **kwargs):
    """Run a draft action, translating its refusals into the 409 the website
    already knows how to render."""
    try:
        return fn(*args, **kwargs)
    except (draft.DraftError, DraftRulesError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/draft/state")
def draft_state(mgr: Manager = Depends(get_current_manager)):
    """The whole draft room in one document, for the active season.

    This read is also the CLOCK: it sweeps any turn that has run out before it
    answers (handball/draft.py), because there is no scheduler in this deployment and
    the room polls this endpoint. `swept` reports what the read itself did, so the
    page can say "the clock picked for Denver" rather than silently showing a pick
    nobody made."""
    season = active_season()
    state = draft.draft_state(engine, season)
    return {**state, "is_commissioner": mgr.is_commissioner,
            "your_teams": sorted(mgr.owned_team_ids)}


@router.post("/draft/lottery", status_code=201)
def run_lottery(body: LotteryBody | None = None,
                mgr: Manager = Depends(get_current_manager)):
    """Draw every lottery slot, number round 1's first picks, and resolve every
    protection the draw just decided. Refused once the room has opened."""
    require_commissioner(mgr)
    seed = body.seed if body else None
    return _guard(draft.run_lottery, engine, active_season(), seed=seed)


@router.post("/draft/prospects", status_code=201)
async def upload_prospects(request: Request, mgr: Manager = Depends(get_current_manager)):
    """Upload the prospect class for the active season, replacing what is on the
    board. Takes either a multipart file upload (field `file`) or a JSON body
    `{"text": "...", "filename": "class.csv"}` -- the commissioner may have a file or
    may have a list in a chat window, and both are the same names file underneath.

    The FORMAT is draft_simulator.load_prospect_names': one name per line, or a CSV
    with a `Name` column and an optional `Position`. `filename` is read only for its
    extension, which is what chooses between the two."""
    require_commissioner(mgr)
    ctype = (request.headers.get("content-type") or "").lower()

    if ctype.startswith("multipart/form-data"):
        try:
            form = await request.form()
        except Exception as e:  # noqa: BLE001 - python-multipart may not be installed
            raise HTTPException(
                status_code=400,
                detail=f"could not read the upload ({e}); paste the names as JSON "
                       f'{{"text": "...", "filename": "class.csv"}} instead')
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="no `file` in the upload")
        raw = await upload.read()
        content = raw.decode("utf-8", errors="replace")
        filename = getattr(upload, "filename", None)
    else:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail='send a names file as multipart `file`, or JSON '
                       '{"text": "...", "filename": "class.csv"}')
        content = body.get("text") or ""
        filename = body.get("filename")

    if not content.strip():
        raise HTTPException(status_code=400, detail="that file is empty")
    return _guard(draft.upload_prospects, engine, active_season(), content,
                  filename=filename)


@router.post("/draft/open", status_code=201)
def open_draft(mgr: Manager = Depends(get_current_manager)):
    """Open the room and start the clock on pick 1. Needs a drawn lottery and a board
    with at least one prospect per pick."""
    require_commissioner(mgr)
    return _guard(draft.open_draft, engine, active_season())


@router.post("/draft/pick", status_code=201)
def make_pick(body: PickBody, mgr: Manager = Depends(get_current_manager)):
    """Make the pick that is on the clock. With no `prospect_id` the league's own
    best-available rule chooses, which is what the clock does and what forcing a pick
    for an absent manager usually means.

    The team is not in the body: the league knows whose turn it is. Ownership of that
    team is demanded strictly (no commissioner bypass); a commissioner picking for
    somebody else sends `force`."""
    season = active_season()
    clock = draft.on_the_clock(engine, season)
    if clock is None:
        raise HTTPException(
            status_code=409,
            detail=f"the {season} draft is not on the clock")

    if body.force:
        require_commissioner(mgr)
        team_slug = None            # the service re-reads whose turn it is
    else:
        require_owns_strict(mgr, clock["team"])
        team_slug = clock["team"]

    return _guard(draft.make_pick, engine, season, body.prospect_id,
                  team_slug=team_slug)
