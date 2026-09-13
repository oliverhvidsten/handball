"""
Team identity endpoints: the nickname, abbreviation and logo a manager may edit.

Over alembic 0018. Rules live in handball/team_settings.py. The logo READ is the one
unauthenticated route in the API: an <img> tag cannot send a bearer token, the bytes
are a server-re-encoded PNG rather than anything a manager uploaded verbatim, and a
team's logo is not a secret. It is cached hard because the URL carries the version.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from handball import team_settings as ts

from api.auth import Manager, get_current_manager
from api.deps import engine, require_owns

router = APIRouter()


class TeamSettingsBody(BaseModel):
    nickname: str | None = Field(default=None, max_length=200)
    abbr: str | None = Field(default=None, max_length=16)


def _team_or_404(slug: str, fn, *args, **kwargs):
    try:
        return fn(engine, slug, *args, **kwargs)
    except ts.TeamSettingsConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ts.TeamSettingsError as e:
        if str(e).startswith("no team "):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/teams/{slug}/settings")
def get_team_settings(slug: str, mgr: Manager = Depends(get_current_manager)):
    """Any manager may read a team's identity; only its owner may change it."""
    return _team_or_404(slug, ts.get_settings)


@router.put("/teams/{slug}/settings")
def put_team_settings(slug: str, body: TeamSettingsBody, mgr: Manager = Depends(get_current_manager)):
    """Set the nickname and abbreviation. Send null (or blank) to clear either: the
    city then stands alone / the abbreviation goes back to being derived."""
    require_owns(mgr, slug)
    return _team_or_404(slug, ts.update_settings, nickname=body.nickname, abbr=body.abbr)


@router.put("/teams/{slug}/logo")
async def put_team_logo(slug: str, file: UploadFile = File(...), mgr: Manager = Depends(get_current_manager)):
    """Upload a logo (multipart field `file`). Re-encoded to a bounded PNG server-side."""
    require_owns(mgr, slug)
    data = await file.read(ts.LOGO_MAX_BYTES + 1)
    if len(data) > ts.LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"the logo must be under {ts.LOGO_MAX_BYTES // (1024 * 1024)} MB")
    return _team_or_404(slug, ts.set_logo, data)


@router.delete("/teams/{slug}/logo")
def delete_team_logo(slug: str, mgr: Manager = Depends(get_current_manager)):
    require_owns(mgr, slug)
    return _team_or_404(slug, ts.clear_logo)


@router.get("/teams/{slug}/logo")
def get_team_logo(slug: str, v: int | None = None):
    """The logo image itself. No auth (see the module docstring). `v` is ignored
    server-side; it exists so a new version gets a new URL and the old one can be
    cached forever."""
    found = ts.get_logo(engine, slug)
    if found is None:
        raise HTTPException(status_code=404, detail="no logo")
    mime, data, _version = found
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
