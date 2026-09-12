"""
NHA write API. Endpoints:

    GET  /health
    PUT  /teams/{slug}/arrangement      manager sets their lineup (validated)
    POST /trades                        propose a trade
    POST /trades/{id}/accept            receiving manager accepts
    POST /trades/{id}/reject            receiving manager rejects
    POST /trades/{id}/cancel            proposing manager cancels
    POST /trades/{id}/approve           commissioner approves + commits
    GET  /teams/{slug}/cap              cap standing + roster room + offer ceilings
    POST /signings                      manager signs a free agent (pool, 1yr/$0)
    GET  /free-agency/state             the offseason market, from this manager's side
    POST /free-agency/periods           commissioner opens the market
    POST /free-agency/offers            manager offers a free agent a contract
    POST /free-agency/rounds/close      commissioner closes the sealed offer round
    POST /free-agency/auctions/{id}/... match / decline / bid / force-forfeit / award
    GET  /standings                     the league table, ranked (points/H2H/GD)
    GET  /playoffs/bracket              the postseason bracket + champion
    POST /playoffs/start                commissioner seeds it from the final standings
    POST /playoffs/rounds/run           commissioner runs the next round (background)
    POST /playoffs/reset                commissioner rolls back a failed round

Domain rules are reused, not reimplemented: arrangement edits go through
Team.apply_arrangement (which runs domain.validate), trades through trade_service,
and signings through signing_service (which enforces salary_cap). Authorization is by
manager↔team ownership + the commissioner role.
"""
from __future__ import annotations

import os
import threading
import urllib.request

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text

from handball import contract_admin
from handball import free_agency as fa
from handball import league_structure
from handball import offseason
from handball import playoffs
from handball import postseason
from handball import schedule_repository as sched_repo
from handball import season_readiness
from handball import signing_service as sign
from handball import simulation_vars
from handball import standings
from handball import trade_service as ts
from handball.domain import ArrangementError
from handball.league import build_production_league_pg
from handball.league_views import TeamArrangement
from handball.pg_repository import PostgresTeamRepository
from handball.season import PERIODS

from api.auth import Manager, get_current_manager
from api.contracts import router as contracts_router
from api.deps import (
    active_season,
    engine,
    queue_clear,
    require_commissioner,
    require_owns,
    require_owns_strict,
    team_uuid,
)
from api.draft import router as draft_router
from api.hall_of_fame import router as hall_of_fame_router
from api.voting import router as voting_router

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

repo = PostgresTeamRepository(engine)

# The feature routers. Each is owned by one Phase 1 agent and is empty until then;
# they are mounted now so nobody has to come back and edit this file to add a route.
app.include_router(draft_router)
app.include_router(voting_router)
app.include_router(contracts_router)
app.include_router(hall_of_fame_router)


# -- request bodies --------------------------------------------------------
class ArrangementBody(BaseModel):
    starters: dict[str, list[str]]
    bench: dict[str, list[str]]
    reserves: list[str]


class PickAssetBody(BaseModel):
    """A pick asset that carries a protection agreed at trade time (handball/
    hall_of_fame agent's trade_service addition). round-1-only and 1-32 are
    re-checked in trade_service, which has the pick row; the bounds here only
    keep nonsense out of the request."""
    pick_id: str
    protection_top_n: int | None = Field(default=None, ge=1, le=32)


class TradeBody(BaseModel):
    from_team: str
    to_team: str
    players_out: list[str] = Field(default_factory=list)
    players_in: list[str] = Field(default_factory=list)
    # A pick entry is either a plain draft_picks id (unprotected, as before) or
    # {"pick_id", "protection_top_n"} for a protected round-1 pick.
    picks_out: list[str | PickAssetBody] = Field(default_factory=list)
    picks_in: list[str | PickAssetBody] = Field(default_factory=list)


class RetirementBody(BaseModel):
    player_ids: list[str] = Field(default_factory=list)


class OfferBody(BaseModel):
    """A sealed contract offer in a free-agency round. The bounds here only keep
    nonsense out of the query; the league's contract and cap rules are enforced in
    handball/free_agency_rules.py."""
    team: str
    player_id: str
    term: int = Field(ge=1)
    value: int = Field(ge=0)


class WithdrawBody(BaseModel):
    team: str
    player_id: str


class BidBody(BaseModel):
    """One turn of sequential bidding. `term`/`value` are required for a raise and
    ignored otherwise -- a match copies the leader's contract by definition."""
    team: str
    action: str = Field(pattern="^(match|raise|forfeit)$")
    term: int | None = Field(default=None, ge=1)
    value: int | None = Field(default=None, ge=0)


class TeamActionBody(BaseModel):
    """A commissioner intervention aimed at one team (force-forfeit, award)."""
    team: str
    reason: str | None = None


class ContractOverrideBody(BaseModel):
    """One explicit (re)assignment in a bulk contract change. Bounds here only keep
    nonsense out of the query; the league limits are salary_cap.validate_contract."""
    player_id: str
    term: int = Field(ge=1)
    value: int = Field(ge=0)


class BulkContractBody(BaseModel):
    """A commissioner bulk contract change (handball/contract_admin.py). Defaults to
    a dry run: the plan comes back either way, and only `dry_run: false` writes it."""
    strategy: str = Field(default=contract_admin.RESTART)
    overrides: list[ContractOverrideBody] = Field(default_factory=list)
    dry_run: bool = True


class SigningBody(BaseModel):
    """A free-agent signing: who, and onto which team. No contract fields -- every
    free-agent deal is the same fixed league-minimum contract
    (signing_service.FREE_AGENT_CONTRACT_YEARS/SALARY); term and value are negotiated
    only when re-signing your own expiring players."""
    team: str
    player_id: str


# -- helpers ---------------------------------------------------------------
# The shared ones live in api/deps.py so the feature routers can use them too; these
# aliases keep every call site in this file (and the tests) reading as it always did.
_team_uuid = team_uuid
_require_owns = require_owns
_require_commissioner = require_commissioner
_require_owns_strict = require_owns_strict
_active_season = active_season
_queue_clear = queue_clear


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


def _owned_teams(mgr: Manager) -> list[dict]:
    """The manager's teams as {id, slug, name}. A manager may own several, so every
    "is it my turn?" question is asked across all of them."""
    if not mgr.owned_team_ids:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text("select id::text as id, slug, name from teams "
                 "where id = any(cast(:ids as uuid[])) order by name"),
            {"ids": [str(t) for t in mgr.owned_team_ids]},
        ).mappings().all()
    return [dict(r) for r in rows]


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


def _pick_arg(p: str | PickAssetBody) -> str | dict:
    return p if isinstance(p, str) else {"pick_id": p.pick_id, "protection_top_n": p.protection_top_n}


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
            picks_out=[_pick_arg(p) for p in body.picks_out],
            picks_in=[_pick_arg(p) for p in body.picks_in],
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


# -- free-agent signing ----------------------------------------------------
@app.get("/teams/{slug}/cap")
def team_cap(slug: str, mgr: Manager = Depends(get_current_manager)):
    """A team's salary-cap standing: payroll, cap room, luxury-tax thresholds, MLE,
    hard-cap headroom, roster room, and the two offer ceilings (own free agent vs
    outside). Readable by any authenticated manager -- payroll is public information
    (it's already in Team.public_view) and the free-agent page needs it for every team
    the user can act as."""
    try:
        return sign.team_cap_report(engine, slug)
    except sign.SigningError as e:
        raise HTTPException(status_code=404, detail=str(e))


# -- bulk contract administration ------------------------------------------
# The commissioner's repair path for rosters that predate the contract model. See
# handball/contract_admin.py for why a league can need it at all.
@app.get("/contracts/audit")
def contracts_audit(mgr: Manager = Depends(get_current_manager)):
    """What the league's contracts look like, and what the next rollover would do to
    them. Read-only, and the thing to look at before deciding whether a bulk change
    is needed. Commissioner-only: it is a whole-league view of every deal."""
    _require_commissioner(mgr)
    return contract_admin.audit(engine)


@app.post("/contracts/bulk")
def contracts_bulk(body: BulkContractBody, mgr: Manager = Depends(get_current_manager)):
    """(Re)assign contract terms in bulk. Returns the PLAN -- every before/after, the
    payrolls it moves, and how many players the next rollover would then release --
    and applies it only when `dry_run` is false.

    Refused while a period is simulating, for the same reason a signing is: the
    simulation loads each team once, and rewriting contracts underneath a half-played
    season is not something the run would notice."""
    _require_commissioner(mgr)
    _require_no_run_in_flight()
    overrides = [contract_admin.Override(player_id=o.player_id, term=o.term, value=o.value)
                 for o in body.overrides]
    run = contract_admin.plan if body.dry_run else contract_admin.apply_bulk
    try:
        plan = run(engine, strategy=body.strategy, overrides=overrides)
    except contract_admin.BulkContractError as e:
        raise HTTPException(status_code=400, detail={"problems": e.problems})
    return {"dry_run": body.dry_run, **plan.as_dict()}


@app.post("/signings")
def post_signing(body: SigningBody, mgr: Manager = Depends(get_current_manager)):
    """Sign a free agent to the fixed league-minimum deal (1 year, $0M). No
    commissioner approval: unlike a trade there is no counterparty to collude with,
    the terms aren't negotiable, and eligibility/roster room are checked objectively
    in signing_service."""
    _require_owns(mgr, body.team)
    _require_no_run_in_flight()
    try:
        return sign.sign_free_agent(engine, body.team, body.player_id)
    except sign.SigningError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _require_no_run_in_flight() -> None:
    """Refuse roster additions while a period is simulating. The simulation loads each
    team once and writes results back per game, so a player arriving mid-run would land
    in a season whose games are half-played. A dead run (stale heartbeat) doesn't
    count -- that has to be reset via /periods/reset anyway."""
    state = sched_repo.get_season_state(engine, _active_season())
    if state and state["run_status"] == "running" and not _run_stale(state):
        raise HTTPException(
            status_code=409, detail="a period is currently simulating; try again once it finishes"
        )


# -- free agency: the offseason market -------------------------------------
# Commissioner controls the phases; managers act inside them. Every rule violation
# comes back as 409 with the service's own sentence, which is written to be shown to
# the manager who tried it.
@app.post("/free-agency/periods", status_code=201)
def open_free_agency(mgr: Manager = Depends(get_current_manager)):
    """Open the offseason market for the active season, with its first offer round."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if state and state["periods_run"] > 0:
        raise HTTPException(
            status_code=409,
            detail="the season is already under way; free agency belongs to the offseason")
    return _fa_action(fa.open_period, engine, season, actor=mgr.user_id)


@app.post("/free-agency/rounds/close")
def close_offer_round(mgr: Manager = Depends(get_current_manager)):
    """Shut the sealed offer window and resolve every board at once."""
    _require_commissioner(mgr)
    return _fa_action(fa.close_offer_round, engine, actor=mgr.user_id)


@app.post("/free-agency/rounds", status_code=201)
def open_next_round(mgr: Manager = Depends(get_current_manager)):
    """Open another offer round on whoever is still unsigned."""
    _require_commissioner(mgr)
    return _fa_action(fa.open_next_round, engine, actor=mgr.user_id)


@app.post("/free-agency/close")
def close_free_agency(mgr: Manager = Depends(get_current_manager)):
    """End the market. Everyone unsigned returns to the ordinary 1yr/$0 pool."""
    _require_commissioner(mgr)
    return _fa_action(fa.close_period, engine, actor=mgr.user_id)


@app.post("/free-agency/offers", status_code=201)
def submit_offer(body: OfferBody, mgr: Manager = Depends(get_current_manager)):
    """Offer a free agent a contract, replacing any offer this team already has on
    them. Sealed until the commissioner closes the round."""
    _require_owns_strict(mgr, body.team)
    return _fa_action(fa.submit_offer, engine, body.team, body.player_id,
                      body.term, body.value, actor=mgr.user_id)


@app.post("/free-agency/offers/withdraw")
def withdraw_offer(body: WithdrawBody, mgr: Manager = Depends(get_current_manager)):
    """Pull one of your live offers while the round is still open."""
    _require_owns_strict(mgr, body.team)
    return _fa_action(fa.withdraw_offer, engine, body.team, body.player_id,
                      actor=mgr.user_id)


@app.post("/free-agency/auctions/{auction_id}/match")
def rfa_match(auction_id: int, body: TeamActionBody,
              mgr: Manager = Depends(get_current_manager)):
    """Restricted free agency: match the top offer exactly and keep your player."""
    _require_owns_strict(mgr, body.team)
    return _fa_action(fa.match_offer, engine, auction_id, body.team, actor=mgr.user_id)


@app.post("/free-agency/auctions/{auction_id}/decline")
def rfa_decline(auction_id: int, body: TeamActionBody,
                mgr: Manager = Depends(get_current_manager)):
    """Restricted free agency: pass, and let the player go to the open market."""
    _require_owns_strict(mgr, body.team)
    return _fa_action(fa.decline_match, engine, auction_id, body.team, actor=mgr.user_id)


@app.post("/free-agency/auctions/{auction_id}/bid")
def place_bid(auction_id: int, body: BidBody, mgr: Manager = Depends(get_current_manager)):
    """One turn of sequential bidding: match, raise or forfeit."""
    _require_owns_strict(mgr, body.team)
    return _fa_action(fa.place_bid, engine, auction_id, body.team, body.action,
                      term=body.term, value=body.value, actor=mgr.user_id)


@app.post("/free-agency/auctions/{auction_id}/force-forfeit")
def force_forfeit(auction_id: int, body: TeamActionBody,
                  mgr: Manager = Depends(get_current_manager)):
    """Commissioner: drop a team that is stalling a board."""
    _require_commissioner(mgr)
    return _fa_action(fa.force_forfeit, engine, auction_id, body.team,
                      actor=mgr.user_id, reason=body.reason or "commissioner")


@app.post("/free-agency/auctions/{auction_id}/award")
def award_auction(auction_id: int, body: TeamActionBody,
                  mgr: Manager = Depends(get_current_manager)):
    """Commissioner: break a no-raise deadlock by awarding the player."""
    _require_commissioner(mgr)
    return _fa_action(fa.award_auction, engine, auction_id, body.team,
                      actor=mgr.user_id, reason=body.reason)


def _fa_action(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (fa.FreeAgencyError, ArrangementError) as e:
        detail = {"problems": e.problems} if isinstance(e, ArrangementError) else str(e)
        raise HTTPException(status_code=409, detail=detail)


@app.get("/free-agency/state")
def free_agency_state(mgr: Manager = Depends(get_current_manager)):
    """The one document the free-agency page polls: the phase, every live board, and --
    for each team this manager owns -- their cap room, their live offers, and the
    boards waiting on them. `period` is null when no market is open, which is the
    signal to render the ordinary pool page.

    Sealed offers stay sealed: a manager gets their OWN offers here, and a board is
    withheld until the round that produced it has closed (fa.PUBLIC_AUCTION_STATUSES).

    Reading also ADVANCES THE TURN CLOCK. There is no scheduler here, and the page
    polls this endpoint while a board is live, so the sweep rides along with it: any
    team that has sat on its turn past the limit is forfeited before the state is
    read, which is what stops one unresponsive manager halting the round. It is a
    single indexed lookup when nothing is overdue."""
    swept = fa.sweep_expired_turns(engine)
    state = fa.free_agency_state(engine)
    teams = []
    waiting = 0
    for team in _owned_teams(mgr):
        actions = [
            {"auction_id": a["id"], "player_id": a["player_id"],
             "player_name": a["player_name"],
             "kind": "rfa_match" if a["status"] == "matching" else "bid",
             "waiting_since": a["waiting_since"]}
            for a in state.get("auctions", [])
            if (a["status"] == "bidding" and a["turn_team_id"] == team["id"])
            or (a["status"] == "matching" and a["rights_team_id"] == team["id"])
        ]
        waiting += len(actions)
        teams.append({
            **team,
            "cap": sign.team_cap_report(engine, team["slug"]),
            "offers": fa.team_offers(engine, team["slug"]) if state["period"] else [],
            "action_required": actions,
        })
    return {**state, "teams": teams, "your_turn_count": waiting,
            "is_commissioner": mgr.is_commissioner, "swept": swept}


@app.get("/free-agency/history")
def free_agency_history(season: int | None = None,
                        mgr: Manager = Depends(get_current_manager)):
    """Who bid what on every board that has finished -- the public record of a closed
    round, losing offers included. Readable by any authenticated manager: a board only
    appears here once it has resolved, by which time nothing on it is sealed."""
    return fa.period_history(engine, season)


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
        "run_kind": state["run_kind"] if state else "period",
        "run_stale": _run_stale(state),
        "regular_season_complete": periods_run >= PERIODS,
        # Postseason cursor. The Commissioner page needs it to know whether the next
        # action is "seed the bracket", "run round N", or "advance the season".
        **_playoff_fields(season, periods_run),
        # Season-start readiness (season_readiness.py). Only gates the FIRST period
        # -- once the season is under way these are no longer blockers -- but it is
        # always reported so the page can show what's outstanding during the
        # offseason. The registry can grow; the page renders whatever comes back.
        **_readiness_fields(season, periods_run),
    }


def _playoff_fields(season: int, periods_run: int) -> dict:
    """The postseason's contribution to /season/state. `playoffs_started` is what
    the page gates the "seed the bracket" button on; `playoffs_complete` is what
    gates advancing the season."""
    data = playoffs.bracket(engine, season)
    return {
        "playoffs_started": data["started"],
        "playoffs_complete": data["complete"],
        "playoff_next_round": data["next_round"],
        "playoff_total_rounds": data["total_rounds"],
        "champion": data["champion"],
    }


def _readiness_fields(season: int, periods_run: int) -> dict:
    report = season_readiness.readiness_report(engine, season)
    return {
        "season_ready": report["ready"],
        "season_blockers": report["blockers"],
        "readiness_checks": report["checks"],
        # what actually gates the button: readiness only binds before period 1.
        "readiness_gates_next_period": periods_run == 0 and not report["ready"],
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
        sched_repo.set_run_status(engine, season, "done", period=period, kind="period")
    except Exception as e:  # noqa: BLE001 - surface any failure to the operator
        sched_repo.set_run_status(
            engine, season, "error", period=period, kind="period", error=str(e)
        )
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
    # Starting the season (period 1) has preconditions the rest of the season does
    # not -- e.g. no team may open above the hard cap after signing its draft picks.
    # The whole list lives in season_readiness; every blocker is reported at once.
    if next_period == 1:
        try:
            season_readiness.assert_season_can_start(engine, season)
        except season_readiness.SeasonNotReady as e:
            raise HTTPException(status_code=409, detail={"problems": e.problems})

    # Flip to 'running' synchronously (so a double-click is rejected above), then
    # hand the heavy work to a background task that runs after the response.
    sched_repo.set_run_status(
        engine, season, "running", period=next_period, kind="period", error=None
    )
    background.add_task(_run_period_job, season, next_period, state["injury_seed"])
    return {"season": season, "period": next_period, "run_status": "running"}


@app.post("/periods/reset")
def reset_run(mgr: Manager = Depends(get_current_manager)):
    """Recover an interrupted (stale 'running') or failed ('error') run: roll back
    the partial period's games + record deltas and clear the status so a clean retry
    is possible. Rejected for a run that is genuinely still in progress.

    Only for a REGULAR-SEASON run; a failed playoff round resets through
    /playoffs/reset, which rolls back a round rather than a week range."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state:
        raise HTTPException(status_code=409, detail="no season to reset")
    if state["run_status"] == "running" and not _run_stale(state):
        raise HTTPException(status_code=409, detail="a period is currently in progress")
    if state["run_status"] not in ("running", "error"):
        raise HTTPException(status_code=409, detail="nothing to reset")
    if state["run_kind"] == "playoff":
        raise HTTPException(
            status_code=409,
            detail="the failed run was a playoff round; reset it with /playoffs/reset",
        )

    rolled = sched_repo.reset_run(engine, season, state["run_period"])
    return {"season": season, "rolled_back_games": rolled, "run_status": "idle"}


# -- standings -------------------------------------------------------------
@app.get("/standings")
def standings_table(mgr: Manager = Depends(get_current_manager)):
    """The league table, ranked by the one rule (handball/standings.py): points, then
    head-to-head, then goal difference. Served from the API rather than read straight
    from Supabase because head-to-head cannot be expressed as an ORDER BY -- and a
    table that sorted differently from the bracket it seeds would be worse than no
    table at all.

    Each row carries its conference, division, whether it currently leads that
    division, and the playoff seed it would take if the season ended now."""
    season = _active_season()
    table = standings.load_league_table(engine, season)
    ranked = table.ranked()
    rows = table.by_id()

    with engine.connect() as conn:
        names = dict(conn.execute(text("select slug, name from teams")).all())

    # Teams outside the configured league map (fixtures, a part-built league) still
    # get a row -- they just can't be placed in a conference or a bracket.
    known = [t for t in ranked if t in league_structure.all_teams()]
    seeded = postseason.seed_conferences(
        known, league_structure.get_conference,
        simulation_vars.PLAYOFF_TEAMS_PER_CONFERENCE, league_structure.division_key,
    )
    seed_of = {t: i + 1 for teams in seeded.values() for i, t in enumerate(teams)}
    leaders, led = set(), set()           # `known` is ranked, so first seen leads
    for t in known:
        division = league_structure.division_key(t)
        if division not in led:
            led.add(division)
            leaders.add(t)

    return {
        "season": season,
        "teams": [
            {
                "rank": i + 1,
                "slug": slug,
                "name": names.get(slug, slug),
                "wins": rows[slug].wins,
                "losses": rows[slug].losses,
                "ties": rows[slug].ties,
                "points": rows[slug].points,
                "goals_for": rows[slug].goals_for,
                "goals_against": rows[slug].goals_against,
                "goal_diff": rows[slug].goal_diff,
                "conference": _safe(league_structure.get_conference, slug),
                "division": _safe(league_structure.get_division, slug),
                "division_leader": slug in leaders,
                "playoff_seed": seed_of.get(slug),
            }
            for i, slug in enumerate(ranked)
        ],
    }


def _safe(fn, team: str):
    """Conference/division for a team that may not be in the league map."""
    try:
        return fn(team)
    except KeyError:
        return None


# -- postseason ------------------------------------------------------------
@app.get("/playoffs/bracket")
def playoff_bracket(mgr: Manager = Depends(get_current_manager)):
    """The season's bracket -- every matchup created so far, its result if played,
    and the champion once there is one. Readable by any authenticated manager; it is
    the one document the Playoffs page renders."""
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    data = playoffs.bracket(engine, season)
    return {
        **data,
        "rounds_run": state["playoff_rounds_run"] if state else 0,
        "regular_season_complete": bool(state and state["periods_run"] >= PERIODS),
        "run_status": state["run_status"] if state else "idle",
        "run_kind": state["run_kind"] if state else "period",
        "run_error": state["run_error"] if state else None,
        "run_stale": _run_stale(state),
        "is_commissioner": mgr.is_commissioner,
    }


@app.post("/playoffs/start", status_code=201)
def start_playoffs(mgr: Manager = Depends(get_current_manager)):
    """Seed the bracket from the final standings. Commissioner-only; the regular
    season must be complete and the trade queue clear -- the seeding IS the finished
    standings, so a pending trade must land before or after it, never during."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state or state["periods_run"] < PERIODS:
        raise HTTPException(status_code=409, detail="finish the regular season first")
    if state["run_status"] == "running" and not _run_stale(state):
        raise HTTPException(status_code=409, detail="a run is already in progress")
    if not _queue_clear():
        raise HTTPException(
            status_code=409,
            detail="clear the trade approval queue before seeding the bracket",
        )
    # The same ranking advance_season seeds the draft from.
    ranked = build_production_league_pg(year=season).ranked_team_ids()
    try:
        return playoffs.start_playoffs(engine, season, ranked)
    except playoffs.PlayoffError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _run_playoff_round_job(season: int, round_num: int, injury_seed: int | None) -> None:
    """Background worker for one playoff round -- the postseason twin of
    _run_period_job, sharing the same keep-alive and the same status row."""
    stop = threading.Event()
    keepalive = threading.Thread(target=_keepalive_loop, args=(season, stop), daemon=True)
    keepalive.start()
    try:
        playoffs.run_round(engine, season, injury_seed=injury_seed)
        sched_repo.set_run_status(engine, season, "done", period=round_num, kind="playoff")
    except Exception as e:  # noqa: BLE001 - surface any failure to the operator
        sched_repo.set_run_status(
            engine, season, "error", period=round_num, kind="playoff", error=str(e)
        )
    finally:
        stop.set()


@app.post("/playoffs/rounds/run", status_code=202)
def run_playoff_round(
    background: BackgroundTasks, mgr: Manager = Depends(get_current_manager)
):
    """Simulate the next playoff round in the background and return 202; the page
    polls /playoffs/bracket for completion. Commissioner-only. Managers may edit
    lineups between rounds, which is the point of running one round at a time."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state:
        raise HTTPException(status_code=409, detail="no season to run")
    if state["run_status"] == "running":
        if _run_stale(state):
            raise HTTPException(
                status_code=409,
                detail="the previous run was interrupted; reset it before running again",
            )
        raise HTTPException(status_code=409, detail="a run is already in progress")

    data = playoffs.bracket(engine, season)
    if not data["started"]:
        raise HTTPException(status_code=409, detail="seed the bracket first")
    if data["next_round"] is None:
        raise HTTPException(status_code=409, detail="the postseason is complete")
    if not _queue_clear():
        raise HTTPException(
            status_code=409,
            detail="clear the trade approval queue before running a round",
        )

    round_num = data["next_round"]
    sched_repo.set_run_status(
        engine, season, "running", period=round_num, kind="playoff", error=None
    )
    background.add_task(
        _run_playoff_round_job, season, round_num, state["injury_seed"]
    )
    return {"season": season, "round": round_num, "run_status": "running"}


@app.post("/playoffs/reset")
def reset_playoff_round(mgr: Manager = Depends(get_current_manager)):
    """Recover an interrupted or failed playoff round: delete its games, undecide its
    matchups, and drop any round built off it, so it can be run again. Injuries
    rolled during the round stand (see handball/playoffs.reset_round)."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if state and state["run_status"] == "running" and not _run_stale(state):
        raise HTTPException(status_code=409, detail="a run is currently in progress")
    try:
        result = playoffs.reset_round(engine, season)
    except playoffs.PlayoffError as e:
        raise HTTPException(status_code=409, detail=str(e))
    sched_repo.clear_run(engine, season)
    return {**result, "run_status": "idle"}


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
    and the postseason must both be finished and the trade queue clear. Runs
    synchronously in ONE transaction (no game simulation, so it's quick): assign
    awards, seed the next draft order from the final standings, age every non-retired
    player, move expired contracts to free agency, zero records, and open the new
    season. A failure rolls everything back."""
    _require_commissioner(mgr)
    season = _active_season()
    state = sched_repo.get_season_state(engine, season)
    if not state or state["periods_run"] < PERIODS:
        raise HTTPException(status_code=409, detail="finish the regular season first")
    # The rollover zeroes W-L and reseeds the draft off the final standings, so it
    # must not run while a bracket still depends on them (the Final seeds itself
    # from those records).
    if not playoffs.is_complete(engine, season):
        raise HTTPException(
            status_code=409, detail="crown a champion before advancing the season"
        )
    if not _queue_clear():
        raise HTTPException(
            status_code=409, detail="clear the trade approval queue before advancing"
        )

    # Standings (best->worst) read before the rollover zeroes records.
    ranked = build_production_league_pg(year=season).ranked_team_ids()
    return offseason.advance_season(engine, season, ranked)
