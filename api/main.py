"""
NHA write API. Endpoints:

    GET  /health
    PUT  /teams/{slug}/arrangement      manager sets their lineup (validated)
    POST /trades                        propose a trade
    POST /trades/{id}/accept            receiving manager accepts
    POST /trades/{id}/reject            receiving manager rejects
    POST /trades/{id}/cancel            proposing manager cancels
    POST /trades/{id}/approve           commissioner approves + commits

Domain rules are reused, not reimplemented: arrangement edits go through
Team.apply_arrangement (which runs domain.validate) and trades through
trade_service. Authorization is by manager↔team ownership + the commissioner role.
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from handball import trade_service as ts
from handball.db import get_engine
from handball.domain import ArrangementError
from handball.league_views import TeamArrangement
from handball.pg_repository import PostgresTeamRepository

from api.auth import Manager, get_current_manager

app = FastAPI(title="NHA API")

# The browser frontend is served from a different origin (GitHub Pages / Vite dev
# server) than this API, so cross-origin requests need CORS. Allowed origins come
# from $NHA_CORS_ORIGINS (comma-separated); default covers local Vite dev ports.
_origins = os.environ.get(
    "NHA_CORS_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:5180",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

engine = get_engine()
repo = PostgresTeamRepository(engine)


# -- request bodies --------------------------------------------------------
class ArrangementBody(BaseModel):
    starters: dict[str, list[str]]
    bench: dict[str, list[str]]
    reserves: list[str]


class TradeBody(BaseModel):
    from_team: str
    to_team: str
    players_out: list[str] = Field(default_factory=list)
    players_in: list[str] = Field(default_factory=list)
    picks_out: list[str] = Field(default_factory=list)
    picks_in: list[str] = Field(default_factory=list)


# -- helpers ---------------------------------------------------------------
def _team_uuid(slug: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(text("select id from teams where slug = :s"), {"s": slug}).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no team {slug!r}")
    return str(row[0])


def _trade_team_slugs(trade_id: str) -> tuple[str, str]:
    with engine.connect() as conn:
        row = conn.execute(
            text("select tf.slug as f, tt.slug as t from trades tr "
                 "join teams tf on tf.id = tr.from_team_id "
                 "join teams tt on tt.id = tr.to_team_id "
                 "where tr.id = cast(:id as uuid)"),
            {"id": trade_id},
        ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no trade {trade_id!r}")
    return row["f"], row["t"]


def _require_owns(mgr: Manager, slug: str) -> None:
    if mgr.is_commissioner:
        return
    if not mgr.owns(_team_uuid(slug)):
        raise HTTPException(status_code=403, detail="not your team")


def _require_commissioner(mgr: Manager) -> None:
    if not mgr.is_commissioner:
        raise HTTPException(status_code=403, detail="commissioner only")


# -- endpoints -------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.put("/teams/{slug}/arrangement")
def put_arrangement(slug: str, body: ArrangementBody, mgr: Manager = Depends(get_current_manager)):
    _require_owns(mgr, slug)
    try:
        team = repo.load(slug)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no team {slug!r}")

    arr = TeamArrangement(
        starters={pos: tuple(ids) for pos, ids in body.starters.items()},
        bench={pos: tuple(ids) for pos, ids in body.bench.items()},
        reserves=tuple(body.reserves),
    )
    try:
        team.apply_arrangement(arr)        # validates; atomic
    except ArrangementError as e:
        raise HTTPException(status_code=400, detail={"problems": e.problems})
    repo.save(team)
    return {"status": "ok", "team": slug}


@app.post("/trades")
def post_trade(body: TradeBody, mgr: Manager = Depends(get_current_manager)):
    _require_owns(mgr, body.from_team)
    # internal trade: the proposer LITERALLY owns both teams (teams.owner_id), so
    # there's no counterparty to accept. Based on real ownership, not the
    # commissioner authz-bypass.
    internal = mgr.owns(_team_uuid(body.from_team)) and mgr.owns(_team_uuid(body.to_team))
    try:
        trade_id = ts.propose_trade(
            engine, body.from_team, body.to_team,
            players_out=body.players_out, players_in=body.players_in,
            picks_out=body.picks_out, picks_in=body.picks_in,
            proposed_by=mgr.user_id, internal=internal,
        )
    except ts.TradeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"trade_id": trade_id, "status": "accepted" if internal else "proposed", "internal": internal}


@app.post("/trades/{trade_id}/accept")
def accept(trade_id: str, mgr: Manager = Depends(get_current_manager)):
    _, to_slug = _trade_team_slugs(trade_id)
    _require_owns(mgr, to_slug)
    return _run_transition(ts.accept_trade, trade_id)


@app.post("/trades/{trade_id}/reject")
def reject(trade_id: str, mgr: Manager = Depends(get_current_manager)):
    _, to_slug = _trade_team_slugs(trade_id)
    _require_owns(mgr, to_slug)
    return _run_transition(ts.reject_trade, trade_id)


@app.post("/trades/{trade_id}/cancel")
def cancel(trade_id: str, mgr: Manager = Depends(get_current_manager)):
    from_slug, _ = _trade_team_slugs(trade_id)
    _require_owns(mgr, from_slug)
    return _run_transition(ts.cancel_trade, trade_id)


@app.post("/trades/{trade_id}/approve")
def approve(trade_id: str, mgr: Manager = Depends(get_current_manager)):
    _require_commissioner(mgr)
    try:
        ts.approve_trade(engine, trade_id)
    except (ts.TradeError, ArrangementError) as e:
        detail = {"problems": e.problems} if isinstance(e, ArrangementError) else str(e)
        raise HTTPException(status_code=409, detail=detail)
    return {"trade_id": trade_id, "status": ts.get_trade_status(engine, trade_id)}


def _run_transition(fn, trade_id: str):
    try:
        fn(engine, trade_id)
    except ts.TradeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"trade_id": trade_id, "status": ts.get_trade_status(engine, trade_id)}
