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
import threading
import urllib.request

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from handball import offseason
from handball import schedule_repository as sched_repo
from handball import trade_service as ts
from handball.db import get_engine
from handball.domain import ArrangementError
from handball.league import build_production_league_pg
from handball.league_views import TeamArrangement
from handball.pg_repository import PostgresTeamRepository
from handball.season import PERIODS

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


class RetirementBody(BaseModel):
    player_ids: list[str] = Field(default_factory=list)


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


# Season the run controls manage. "Advance season" (a NEW season year) is out of
# scope, so the active season is simply the one season_state knows about, else the
# latest season with games, else a sensible default for a fresh league.
DEFAULT_SEASON = 2026


def _active_season() -> int:
    with engine.connect() as conn:
        row = conn.execute(text("select max(season) from season_state")).first()
        if row and row[0] is not None:
            return int(row[0])
        row = conn.execute(text("select max(season) from games")).first()
    return int(row[0]) if row and row[0] is not None else DEFAULT_SEASON


def _queue_clear() -> bool:
    """No accepted-but-unapproved trades are sitting in the commissioner queue."""
    with engine.connect() as conn:
        n = conn.execute(
            text("select count(*) from trades where status = 'accepted'")
        ).scalar_one()
    return n == 0


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


# -- season simulation -----------------------------------------------------
@app.get("/season/state")
def season_state(mgr: Manager = Depends(get_current_manager)):
    """Current run cursor + gating flags for the Commissioner page. Any
    authenticated manager may read it; only the commissioner can act on it. The
    page polls this while a period runs (run_status == 'running')."""
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    periods_run = state["periods_run"] if state else 0
    run_status = state["run_status"] if state else "idle"
    return {
        "season": season,
        "periods_run": periods_run,
        "next_period": periods_run + 1,
        "total_periods": PERIODS,
        "schedule_generated": bool(state and state["schedule_generated"]),
        "queue_clear": _queue_clear(),
        "run_status": run_status,
        "run_period": state["run_period"] if state else None,
        "run_error": state["run_error"] if state else None,
        "run_stale": _run_stale(state),
        "regular_season_complete": periods_run >= PERIODS,
    }


def _run_stale(state: dict | None) -> bool:
    """A 'running' row whose heartbeat has gone quiet -- the worker died mid-run."""
    return bool(
        state
        and state["run_status"] == "running"
        and (state.get("run_age_seconds") or 0) > STALE_RUN_SECONDS
    )


@app.post("/schedule/generate")
def generate_schedule(mgr: Manager = Depends(get_current_manager)):
    """Generate + persist the season's fixture list. Only valid before any period
    has run; the seed is the season year so the schedule is reproducible."""
    _require_commissioner(mgr)
    season = _active_season()
    seed = season
    sched_repo.init_season_state(engine, season, schedule_seed=seed, injury_seed=seed)
    state = sched_repo.get_season_state(engine, season)
    if state and state["periods_run"] > 0:
        raise HTTPException(
            status_code=409,
            detail="season already in progress; cannot regenerate the schedule",
        )

    # Lazy import: the OR-Tools generator is heavy and only needed here.
    from handball.schedule_generator import ScheduleGenerator

    gen = ScheduleGenerator(seed=seed)
    try:
        n = sched_repo.save_schedule(engine, season, gen.to_json_serializable())
    except sched_repo.ScheduleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    sched_repo.mark_schedule_generated(engine, season, seed)
    return {"season": season, "fixtures": n}


# A 'running' row whose heartbeat is older than this is treated as a dead worker
# (the web instance was spun down / restarted mid-run). The keep-alive heartbeats
# every 60s, so 5 missed beats is a confident "it died".
STALE_RUN_SECONDS = 300
_KEEPALIVE_PERIOD_SECONDS = 60


def _keepalive_loop(season: int, stop: threading.Event) -> None:
    """While a period runs, every minute: heartbeat the run (so a live-but-slow run
    isn't mistaken for dead) and ping our own public URL (an inbound request resets
    the free-tier idle timer, so the instance isn't spun down mid-run). Both are
    best-effort; the public URL comes from Render's $RENDER_EXTERNAL_URL (or an
    explicit $NHA_SELF_PING_URL), and is simply skipped locally where it's unset."""
    base = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("NHA_SELF_PING_URL")
    health = base.rstrip("/") + "/health" if base else None
    while not stop.wait(_KEEPALIVE_PERIOD_SECONDS):
        try:
            sched_repo.heartbeat(engine, season)
        except Exception:  # noqa: BLE001 - never let keep-alive kill the run
            pass
        if health:
            try:
                urllib.request.urlopen(health, timeout=10).read()
            except Exception:  # noqa: BLE001
                pass


def _run_period_job(season: int, period: int, injury_seed: int | None) -> None:
    """Background worker: simulate one period and persist results, then advance the
    cursor. A period is ~150 games / minutes against a remote DB, so it runs off the
    request thread; status/errors land in season_state for the website to poll. A
    keep-alive thread heartbeats + pings the instance warm for the duration."""
    stop = threading.Event()
    keepalive = threading.Thread(target=_keepalive_loop, args=(season, stop), daemon=True)
    keepalive.start()
    try:
        schedule = sched_repo.load_schedule(engine, season)
        league = build_production_league_pg(
            year=season, seed=injury_seed, schedule=schedule
        )
        league.run_period(period)
        sched_repo.bump_periods_run(engine, season)
        sched_repo.set_run_status(engine, season, "done", period=period)
    except Exception as e:  # noqa: BLE001 - surface any failure to the operator
        sched_repo.set_run_status(engine, season, "error", period=period, error=str(e))
    finally:
        stop.set()


@app.post("/periods/run", status_code=202)
def run_period(
    background: BackgroundTasks, mgr: Manager = Depends(get_current_manager)
):
    """Kick off simulation of the next period in the background and return 202. The
    Commissioner page polls /season/state for completion. Commissioner-only; the
    trade queue must be clear and no run may already be in flight."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state or not state["schedule_generated"]:
        raise HTTPException(status_code=409, detail="generate a schedule first")
    if state["run_status"] == "running":
        if _run_stale(state):
            raise HTTPException(
                status_code=409,
                detail="the previous run was interrupted; reset it before running again",
            )
        raise HTTPException(status_code=409, detail="a period is already running")

    next_period = state["periods_run"] + 1
    if next_period > PERIODS:
        raise HTTPException(status_code=409, detail="regular season already complete")
    if not _queue_clear():
        raise HTTPException(
            status_code=409,
            detail="clear the trade approval queue before running a period",
        )

    # Flip to 'running' synchronously (so a double-click is rejected above), then
    # hand the heavy work to a background task that runs after the response.
    sched_repo.set_run_status(engine, season, "running", period=next_period, error=None)
    background.add_task(_run_period_job, season, next_period, state["injury_seed"])
    return {"season": season, "period": next_period, "run_status": "running"}


@app.post("/periods/reset")
def reset_run(mgr: Manager = Depends(get_current_manager)):
    """Recover an interrupted (stale 'running') or failed ('error') run: roll back
    the partial period's games + record deltas and clear the status so a clean retry
    is possible. Rejected for a run that is genuinely still in progress."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state:
        raise HTTPException(status_code=409, detail="no season to reset")
    if state["run_status"] == "running" and not _run_stale(state):
        raise HTTPException(status_code=409, detail="a period is currently in progress")
    if state["run_status"] not in ("running", "error"):
        raise HTTPException(status_code=409, detail="nothing to reset")

    rolled = sched_repo.reset_run(engine, season, state["run_period"])
    return {"season": season, "rolled_back_games": rolled, "run_status": "idle"}


# -- offseason: retirement + advance season --------------------------------
@app.get("/retirement/candidates")
def retirement_candidates(mgr: Manager = Depends(get_current_manager)):
    """Players the commissioner may retire during the offseason (older than the
    candidate age, not already retired). Commissioner-only."""
    _require_commissioner(mgr)
    return {"candidates": offseason.retirement_candidates(engine)}


@app.post("/retirement")
def retire(body: RetirementBody, mgr: Manager = Depends(get_current_manager)):
    """Retire the selected players: flag them and remove them from rosters (the row
    is kept for career history). Commissioner-only."""
    _require_commissioner(mgr)
    n = offseason.retire_players(engine, body.player_ids, _active_season())
    return {"retired": n}


@app.post("/season/advance")
def advance_season(mgr: Manager = Depends(get_current_manager)):
    """Offseason rollover to the next season. Commissioner-only; the regular season
    must be complete and the trade queue clear. Runs synchronously in ONE transaction
    (no game simulation, so it's quick): assign awards, seed the next draft order from
    the final standings, age every non-retired player, move expired contracts to free
    agency, zero records, and open the new season. A failure rolls everything back."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state or state["periods_run"] < PERIODS:
        raise HTTPException(status_code=409, detail="finish the regular season first")
    if not _queue_clear():
        raise HTTPException(
            status_code=409, detail="clear the trade approval queue before advancing"
        )

    # Standings (best->worst) read before the rollover zeroes records.
    ranked = build_production_league_pg(year=season).ranked_team_ids()
    return offseason.advance_season(engine, season, ranked)
