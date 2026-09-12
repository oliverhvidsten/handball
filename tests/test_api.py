"""
Phase 6 verification: the FastAPI write endpoints against the dev Postgres, with
the auth dependency overridden (no Supabase needed). Skips when Postgres is
unavailable.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.pg_repository import PostgresTeamRepository
from handball.simulation_vars import FA_TURN_LIMIT_HOURS as _FA_TURN_LIMIT_HOURS

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

# import the app only when PG is available (it builds an engine at import)
if _PG_OK:
    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets fa_periods fa_rounds "
           "fa_auctions fa_offers fa_auction_seats fa_actions playoff_series "
           "ballots voting_status award_tallies all_star_games "
           "draft_lotteries draft_state draft_prospects hall_of_fame")


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield
    app.dependency_overrides.clear()


def _team(team_id: str) -> Team:
    def p(pid, name, pos, off=5.0, deff=5.0, gk=0.1):
        return Player(id=f"{team_id.lower()}-{pid}", name=f"{team_id} {name}", position=pos,
                      offense=off, defense=deff, goalie_skill=gk, variance=0.5)

    return Team(
        id=team_id, name=team_id, coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "F1", "Forward", off=7), p("f2", "F2", "Forward", off=6), p("f3", "F3", "Forward", off=6)],
            "Midfielder": [p("m1", "M1", "Midfielder"), p("m2", "M2", "Midfielder"), p("m3", "M3", "Midfielder")],
            "Defense": [p("d1", "D1", "Defense", deff=7), p("d2", "D2", "Defense", deff=7), p("d3", "D3", "Defense", deff=6)],
            "Goalie": [p("g1", "G1", "Goalie", off=0.1, deff=0.1, gk=6.0)],
        },
        bench={
            "Forward": [p("f4", "F4", "Forward"), p("f5", "F5", "Forward")],
            "Midfielder": [p("m4", "M4", "Midfielder"), p("m5", "M5", "Midfielder")],
            "Defense": [p("d4", "D4", "Defense"), p("d5", "D5", "Defense")],
            "Goalie": [p("g2", "G2", "Goalie", off=0.1, deff=0.1, gk=5.0)],
        },
        reserves=[p("r1", "R1", "Forward"), p("r2", "R2", "Defense")],
    )


def _uuid_of(slug: str) -> str:
    with _engine.connect() as c:
        return str(c.execute(text("select id from teams where slug=:s"), {"s": slug}).scalar_one())


def _as_manager(*slugs: str, role: str = "manager"):
    """Override auth to act as a manager owning the given team slug(s) (or a
    commissioner when none are given). Seeds the managers row (so trades.proposed_by
    resolves) and sets teams.owner_id for each owned team."""
    owned = [_uuid_of(s) for s in slugs if s]
    user_id = str(uuid.uuid4())
    with _engine.begin() as c:
        c.execute(
            text("insert into managers (user_id, role) values (cast(:u as uuid), :r)"),
            {"u": user_id, "r": role},
        )
        for tid in owned:
            c.execute(
                text("update teams set owner_id = cast(:u as uuid) where id = cast(:t as uuid)"),
                {"u": user_id, "t": tid},
            )
    mgr = Manager(user_id=user_id, owned_team_ids=owned, role=role)
    app.dependency_overrides[get_current_manager] = lambda: mgr


def _arrangement_payload(team: Team) -> dict:
    arr = team.arrangement()
    return {
        "starters": {pos: list(ids) for pos, ids in arr.starters.items()},
        "bench": {pos: list(ids) for pos, ids in arr.bench.items()},
        "reserves": list(arr.reserves),
    }


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def two_teams():
    repo = PostgresTeamRepository(_engine)
    repo.save(_team("Boston"))
    repo.save(_team("Denver"))
    return repo


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_put_valid_arrangement(client, two_teams):
    repo = two_teams
    team = repo.load("Boston")
    payload = _arrangement_payload(team)
    payload["starters"]["Forward"] = list(reversed(payload["starters"]["Forward"]))  # reorder

    _as_manager("Boston")
    r = client.put("/teams/Boston/arrangement", json=payload)
    assert r.status_code == 200, r.text
    assert [p.id for p in repo.load("Boston").starters["Forward"]] == payload["starters"]["Forward"]


def test_put_invalid_arrangement_returns_problems(client, two_teams):
    repo = two_teams
    payload = _arrangement_payload(repo.load("Boston"))
    payload["reserves"].append(payload["starters"]["Forward"][0])  # duplicate -> illegal

    _as_manager("Boston")
    r = client.put("/teams/Boston/arrangement", json=payload)
    assert r.status_code == 400
    assert "problems" in r.json()["detail"]


def test_put_other_teams_arrangement_forbidden(client, two_teams):
    repo = two_teams
    payload = _arrangement_payload(repo.load("Boston"))
    _as_manager("Denver")                       # acting as Denver, editing Boston
    r = client.put("/teams/Boston/arrangement", json=payload)
    assert r.status_code == 403


def test_trade_flow_propose_accept_approve(client, two_teams):
    repo = two_teams

    _as_manager("Boston")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    assert r.status_code == 200, r.text
    trade_id = r.json()["trade_id"]

    _as_manager("Denver")
    assert client.post(f"/trades/{trade_id}/accept").json()["status"] == "accepted"

    _as_manager("", role="commissioner")
    assert client.post(f"/trades/{trade_id}/approve").json()["status"] == "committed"

    # swap landed
    assert repo.load("Denver").get("boston-r1") is not None
    assert repo.load("Boston").get("denver-f5") is not None


def test_non_commissioner_cannot_approve(client, two_teams):
    _as_manager("Boston")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    trade_id = r.json()["trade_id"]
    _as_manager("Denver")
    client.post(f"/trades/{trade_id}/accept")

    _as_manager("Denver")                       # a plain manager, not commissioner
    assert client.post(f"/trades/{trade_id}/approve").status_code == 403


def test_multi_team_owner_can_edit_each_owned_team(client, two_teams):
    repo = two_teams
    _as_manager("Boston", "Denver")             # one manager owns BOTH teams
    for slug in ("Boston", "Denver"):
        payload = _arrangement_payload(repo.load(slug))
        payload["starters"]["Forward"] = list(reversed(payload["starters"]["Forward"]))
        r = client.put(f"/teams/{slug}/arrangement", json=payload)
        assert r.status_code == 200, r.text


# -- free-agent signing endpoints ------------------------------------------
def _free_agent(legacy_id: str, position: str = "Forward") -> None:
    """A player with no team -- which is all a free agent is."""
    with _engine.begin() as c:
        c.execute(
            text("insert into players (legacy_id, name, position, age, years_in_league, "
                 "offense, defense, goalie_skill, max_offense, max_defense, max_goalie_skill, "
                 "variance, peak_age, decline_age, decline_rate, is_injured, contract_term, "
                 "contract_value, years_remaining, amount_paid, rookie_contract, "
                 "restricted_free_agent, retired) "
                 "values (:lid, :lid, cast(:pos as player_position), 26, 4, 6.0, 6.0, 0.1, "
                 "9.0, 9.0, 0.1, 0.5, 27, 30, 0.15, false, 0, 0, 0, 0, false, true, false)"),
            {"lid": legacy_id, "pos": position},
        )


def test_signing_a_free_agent(client, two_teams):
    repo = two_teams
    _free_agent("fa-forward")
    _as_manager("Boston")

    r = client.post("/signings", json={"team": "Boston", "player_id": "fa-forward"})
    assert r.status_code == 200, r.text
    body = r.json()
    # the deal is fixed: 1 year at the league minimum, so payroll doesn't move
    assert (body["term"], body["value"]) == (1, 0)
    assert repo.load("Boston").get("fa-forward") is not None
    assert repo.load("Boston").total_salaries == 0


def test_signing_for_a_team_you_dont_own_is_forbidden(client, two_teams):
    _free_agent("fa-forward")
    _as_manager("Denver")                        # acting as Denver, signing for Boston
    r = client.post("/signings", json={"team": "Boston", "player_id": "fa-forward"})
    assert r.status_code == 403


def test_signing_by_a_team_over_the_hard_cap_is_a_conflict(client, two_teams):
    _free_agent("fa-forward")
    with _engine.begin() as c:                   # past the $250M hard cap (rookie-deal exemption)
        c.execute(text("update players set contract_value = 260 where legacy_id='boston-d1'"))
    _as_manager("Boston")
    r = client.post("/signings", json={"team": "Boston", "player_id": "fa-forward"})
    assert r.status_code == 409
    assert "hard cap" in r.json()["detail"]


def test_signing_requires_a_player(client, two_teams):
    _as_manager("Boston")
    r = client.post("/signings", json={"team": "Boston"})
    assert r.status_code == 422                  # body validation, before any DB work


def test_team_cap_endpoint(client, two_teams):
    with _engine.begin() as c:
        c.execute(text("update players set contract_value = 70 where legacy_id='boston-d1'"))
    _as_manager("Denver")                        # any authenticated manager may read
    r = client.get("/teams/Boston/cap")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payroll"] == 70
    assert body["roster_size"] == 19
    assert body["roster_spots"] == 2
    assert body["limits"]["hard_cap"] == 250


def test_team_cap_endpoint_unknown_team(client, two_teams):
    _as_manager("Boston")
    assert client.get("/teams/Nowhere/cap").status_code == 404


# -- free agency endpoints -------------------------------------------------
def test_only_the_commissioner_opens_free_agency(client, two_teams):
    _as_manager("Boston")
    assert client.post("/free-agency/periods").status_code == 403
    _as_manager("", role="commissioner")
    assert client.post("/free-agency/periods").status_code == 201


def test_the_commissioner_may_not_bid_for_somebody_elses_team(client, two_teams):
    """_require_owns lets a commissioner act as any team, which is fine for trades and
    wrong for a sealed auction: they would be bidding against the league they referee.
    The free-agency endpoints use the strict check instead."""
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")

    r = client.post("/free-agency/offers",
                    json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 10})
    assert r.status_code == 403


def test_a_manager_offers_and_withdraws(client, two_teams):
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")

    _as_manager("Boston")
    r = client.post("/free-agency/offers",
                    json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 10})
    assert r.status_code == 201, r.text
    assert r.json()["value"] == 10

    state = client.get("/free-agency/state").json()
    mine = [t for t in state["teams"] if t["slug"] == "Boston"][0]
    assert [o["player_id"] for o in mine["offers"]] == ["fa-1"]

    assert client.post("/free-agency/offers/withdraw",
                       json={"team": "Boston", "player_id": "fa-1"}).status_code == 200


def test_an_unaffordable_offer_is_a_conflict(client, two_teams):
    _free_agent("fa-1")
    with _engine.begin() as c:                       # capped out
        c.execute(text("update players set contract_value = 250 where legacy_id='boston-d1'"))
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")
    _as_manager("Boston")
    r = client.post("/free-agency/offers",
                    json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 10})
    assert r.status_code == 409
    assert "hard cap" in r.json()["detail"]


def test_bidding_out_of_turn_is_a_conflict(client, two_teams):
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")
    _as_manager("Boston")
    client.post("/free-agency/offers",
                json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 20})
    _as_manager("Denver")
    client.post("/free-agency/offers",
                json={"team": "Denver", "player_id": "fa-1", "term": 3, "value": 10})
    _as_manager("", role="commissioner")
    client.post("/free-agency/rounds/close")

    # Denver bid worst, so Denver is on the clock; Boston acting now is out of turn.
    _as_manager("Boston")
    auction_id = client.get("/free-agency/state").json()["auctions"][0]["id"]
    r = client.post(f"/free-agency/auctions/{auction_id}/bid",
                    json={"team": "Boston", "action": "match"})
    assert r.status_code == 409
    assert "not your turn" in r.json()["detail"]


def _two_offer_board(client) -> int:
    """Boston $20M / Denver $10M on fa-1, round closed -> Denver on the clock."""
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")
    _as_manager("Boston")
    client.post("/free-agency/offers",
                json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 20})
    _as_manager("Denver")
    client.post("/free-agency/offers",
                json={"team": "Denver", "player_id": "fa-1", "term": 3, "value": 10})
    _as_manager("", role="commissioner")
    client.post("/free-agency/rounds/close")
    return client.get("/free-agency/state").json()["auctions"][0]["id"]


def test_a_rival_cannot_read_sealed_offers_from_the_state_document(client, two_teams):
    """The sealed round is the whole mechanism. A board is created by the first offer,
    so it exists while the round is still collecting -- and must not be served."""
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")
    _as_manager("Boston")
    client.post("/free-agency/offers",
                json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 20})

    _as_manager("Denver")                            # a rival, mid-round
    state = client.get("/free-agency/state").json()
    assert state["auctions"] == []
    assert [t["offers"] for t in state["teams"]] == [[]]      # and none of Boston's


def test_reading_the_state_sweeps_an_expired_turn(client, two_teams):
    """There is no scheduler: the poll is the clock. A manager who has sat on their
    turn past the limit is forfeited by the next read, whoever makes it."""
    auction_id = _two_offer_board(client)
    with _engine.begin() as c:
        c.execute(text("update fa_auctions set waiting_since = "
                       "now() - make_interval(hours => :h) where id = :a"),
                  {"h": _FA_TURN_LIMIT_HOURS + 1, "a": auction_id})

    _as_manager("Boston")                            # not the stalling team
    state = client.get("/free-agency/state").json()
    assert [s["team"] for s in state["swept"]] == ["Denver"]
    # One team left, so the board resolved to Boston rather than waiting.
    assert state["auctions"] == []
    with _engine.connect() as c:
        assert c.execute(text("select t.slug from players p join teams t on t.id=p.team_id "
                              "where p.legacy_id='fa-1'")).scalar() == "Boston"


def test_a_turn_inside_the_limit_survives_a_read(client, two_teams):
    auction_id = _two_offer_board(client)
    _as_manager("Boston")
    state = client.get("/free-agency/state").json()
    assert state["swept"] == []
    board = [a for a in state["auctions"] if a["id"] == auction_id][0]
    assert board["turn_team"] == "Denver"
    assert board["turn_seconds_left"] > 0


def test_history_serves_the_closed_round_to_any_manager(client, two_teams):
    auction_id = _two_offer_board(client)
    _as_manager("Denver")
    client.post(f"/free-agency/auctions/{auction_id}/bid",
                json={"team": "Denver", "action": "forfeit"})

    _as_manager("Boston")
    body = client.get("/free-agency/history").json()
    assert len(body["boards"]) == 1
    entry = body["boards"][0]
    assert entry["winning_team"] == "Boston" and entry["signed_value"] == 20
    assert {o["team"]: o["value"] for o in entry["offers"]} == {"Boston": 20, "Denver": 10}


def test_history_is_empty_while_the_round_is_still_sealed(client, two_teams):
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")
    _as_manager("Boston")
    client.post("/free-agency/offers",
                json={"team": "Boston", "player_id": "fa-1", "term": 3, "value": 20})
    _as_manager("Denver")
    assert client.get("/free-agency/history").json()["boards"] == []


def test_the_pool_is_closed_while_free_agency_runs(client, two_teams):
    _free_agent("fa-1")
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")
    _as_manager("Boston")
    r = client.post("/signings", json={"team": "Boston", "player_id": "fa-1"})
    assert r.status_code == 409
    assert "free agency is open" in r.json()["detail"]


def test_state_is_empty_when_no_market_is_open(client, two_teams):
    _as_manager("Boston")
    state = client.get("/free-agency/state").json()
    assert state["period"] is None and state["your_turn_count"] == 0


# -- season simulation endpoints -------------------------------------------
from handball import schedule_repository as sched_repo  # noqa: E402

# _active_season() falls back to DEFAULT_SEASON when no games/season_state exist.
_SEASON = 2026


@pytest.fixture(autouse=True)
def _clean_season_tables():
    """season_state / schedule_games aren't in the shared truncate list; clear them
    around each test so _active_season() and the run cursor start fresh."""
    with _engine.begin() as c:
        c.execute(text("truncate season_state, schedule_games restart identity cascade"))
    yield
    with _engine.begin() as c:
        c.execute(text("truncate season_state, schedule_games restart identity cascade"))


def _seed_one_week_schedule(season: int = _SEASON):
    """Persist a single-week Boston@Denver fixture + a generated season_state, as if
    /schedule/generate had run -- without invoking the heavy OR-Tools generator."""
    sched_repo.init_season_state(_engine, season, schedule_seed=season, injury_seed=season)
    sched_repo.save_schedule(
        _engine, season,
        {"weeks": [[{"team1": "Boston", "team2": "Denver", "matchup_type": "div"}]]},
    )
    sched_repo.mark_schedule_generated(_engine, season, season)


def test_season_state_reports_cursor(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("Boston")                        # any authenticated manager may read
    s = client.get("/season/state").json()
    assert s["season"] == _SEASON
    assert s["schedule_generated"] is True
    assert s["periods_run"] == 0
    assert s["next_period"] == 1
    assert s["total_periods"] == 5
    assert s["queue_clear"] is True


def test_run_period_persists_games_and_advances_cursor(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("", role="commissioner")
    # The run is a background job; the TestClient runs it to completion before the
    # 202 returns, so by the next line the period has finished.
    r = client.post("/periods/run")
    assert r.status_code == 202, r.text
    assert r.json()["run_status"] == "running"

    with _engine.connect() as c:
        n_games = c.execute(text("select count(*) from games where season=:s"), {"s": _SEASON}).scalar_one()
        played = c.execute(text("select coalesce(sum(wins+losses+ties),0) from teams")).scalar_one()
    assert n_games == 1                          # only the one persisted fixture
    assert played == 2                          # one game => two team participations

    state = client.get("/season/state").json()
    assert state["run_status"] == "done"
    assert state["periods_run"] == 1
    assert state["next_period"] == 2

    # a game row carries its week (regression guard: week used to land NULL)
    with _engine.connect() as c:
        assert c.execute(text("select week from games where season=:s"), {"s": _SEASON}).scalar_one() == 1


def test_signing_is_refused_while_a_period_is_simulating(client, two_teams):
    """A roster addition mid-run would land in a season whose games are half-played."""
    _seed_one_week_schedule()
    _free_agent("fa-forward")
    with _engine.begin() as c:
        # updated_at is the heartbeat: now() == a live run, not a stale one.
        c.execute(text("update season_state set run_status='running', updated_at=now() "
                       "where season=:s"), {"s": _SEASON})
    _as_manager("Boston")
    r = client.post("/signings", json={"team": "Boston", "player_id": "fa-forward"})
    assert r.status_code == 409
    assert "simulating" in r.json()["detail"]


def test_an_open_free_agency_blocks_the_first_period(client, two_teams):
    """The readiness registry gates period 1 only, and the UI renders whatever comes
    back -- so a new check needs no API or frontend change to take effect."""
    _seed_one_week_schedule()
    _as_manager("", role="commissioner")
    client.post("/free-agency/periods")

    state = client.get("/season/state").json()
    assert state["season_ready"] is False
    assert any(b["check"] == "free_agency_open" for b in state["season_blockers"])
    assert state["readiness_gates_next_period"] is True

    r = client.post("/periods/run")
    assert r.status_code == 409
    assert any("Free agency" in p for p in r.json()["detail"]["problems"])


# -- bulk contract administration ------------------------------------------
def _expire_all_contracts(years: int = -6, term: int = 3, value: int = 8) -> None:
    """The state this tool exists for: counters that were never initialised and have
    been ticked down every offseason since."""
    with _engine.begin() as c:
        c.execute(text("update players set years_remaining = :y, contract_term = :t, "
                       "contract_value = :v where team_id is not null"),
                  {"y": years, "t": term, "v": value})


def test_contracts_audit_reports_what_the_rollover_would_do(client, two_teams):
    _expire_all_contracts()
    _as_manager("", role="commissioner")
    body = client.get("/contracts/audit").json()
    assert body["rostered"] == 38                       # 19 per team
    assert body["expiring_next_rollover"] == 38         # ...every one of them
    assert body["restart_runnable"] is True
    assert body["restart_would_change"] == 38
    assert body["restart_expiring_next_rollover"] == 0  # 3-year deals, so none


def test_contracts_audit_requires_a_commissioner(client, two_teams):
    _as_manager("Boston")
    assert client.get("/contracts/audit").status_code == 403


def test_bulk_contracts_dry_run_writes_nothing(client, two_teams):
    _expire_all_contracts()
    _as_manager("", role="commissioner")
    r = client.post("/contracts/bulk", json={})          # dry_run defaults to true
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and r.json()["changed"] == 38

    with _engine.connect() as c:
        assert c.execute(text("select count(*) from players where years_remaining > 0")
                         ).scalar() == 0                 # nothing written


def test_bulk_contracts_restarts_every_expired_counter(client, two_teams):
    _expire_all_contracts(years=-6, term=3, value=8)
    _as_manager("", role="commissioner")
    r = client.post("/contracts/bulk", json={"dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 38

    with _engine.connect() as c:
        rows = c.execute(text("select years_remaining, contract_term, contract_value "
                              "from players where team_id is not null")).all()
    assert {r[0] for r in rows} == {3}                   # restarted at the term
    assert {(r[1], r[2]) for r in rows} == {(3, 8)}      # deal itself untouched


def test_bulk_contracts_stagger_previews_a_seed_and_applies_it_verbatim(client, two_teams):
    """A stagger draws random counters. The dry run picks the seed and echoes it; the
    apply that passes it back must write exactly the plan that was shown."""
    _expire_all_contracts(years=-6, term=4, value=8)
    _as_manager("", role="commissioner")
    preview = client.post("/contracts/bulk", json={"strategy": "stagger_expired"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True and body["strategy"] == "stagger_expired"
    assert isinstance(body["seed"], int) and body["changed"] == 38
    shown = {c["player_id"]: c["years_remaining"]["after"] for c in body["changes"]}
    assert all(1 <= y <= 4 for y in shown.values())

    applied = client.post("/contracts/bulk", json={
        "strategy": "stagger_expired", "seed": body["seed"], "dry_run": False,
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["seed"] == body["seed"]

    with _engine.connect() as c:
        rows = c.execute(text("select legacy_id, years_remaining, contract_term, "
                              "contract_value from players where team_id is not null")).all()
    assert {r[0]: r[1] for r in rows} == shown           # what was shown is what landed
    assert {(r[2], r[3]) for r in rows} == {(4, 8)}      # deal itself untouched


def test_bulk_contracts_does_not_disturb_rookie_or_restricted_status(client, two_teams):
    """A repair fixes a counter. update_contract clears both flags when told a deal
    is not a rookie one, so the bulk path must not let that leak."""
    _expire_all_contracts()
    with _engine.begin() as c:
        c.execute(text("update players set rookie_contract = true, "
                       "restricted_free_agent = true where legacy_id = 'boston-f1'"))
    _as_manager("", role="commissioner")
    assert client.post("/contracts/bulk", json={"dry_run": False}).status_code == 200

    with _engine.connect() as c:
        row = c.execute(text("select rookie_contract, restricted_free_agent, "
                             "years_remaining from players where legacy_id='boston-f1'")
                        ).one()
    assert row == (True, True, 3)


def test_bulk_contracts_applies_an_override(client, two_teams):
    _expire_all_contracts()
    _as_manager("", role="commissioner")
    r = client.post("/contracts/bulk", json={
        "dry_run": False,
        "overrides": [{"player_id": "boston-f1", "term": 5, "value": 30}],
    })
    assert r.status_code == 200, r.text
    with _engine.connect() as c:
        assert c.execute(text("select contract_term, contract_value, years_remaining "
                              "from players where legacy_id='boston-f1'")).one() == (5, 30, 5)


def test_bulk_contracts_rejects_an_illegal_override_and_writes_nothing(client, two_teams):
    _expire_all_contracts()
    _as_manager("", role="commissioner")
    r = client.post("/contracts/bulk", json={
        "dry_run": False,
        "overrides": [{"player_id": "nobody", "term": 2, "value": 5}],
    })
    assert r.status_code == 400
    assert any("no such player" in p for p in r.json()["detail"]["problems"])
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from players where years_remaining > 0")
                         ).scalar() == 0                 # the whole plan was refused


def test_bulk_contracts_requires_a_commissioner(client, two_teams):
    _as_manager("Boston")
    assert client.post("/contracts/bulk", json={}).status_code == 403


def test_bulk_contracts_refused_while_a_period_is_simulating(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:
        c.execute(text("update season_state set run_status='running', updated_at=now() "
                       "where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/contracts/bulk", json={})
    assert r.status_code == 409 and "simulating" in r.json()["detail"]


def test_a_repaired_league_survives_the_rollover(client, two_teams):
    """The end-to-end point of the tool: with counters restarted, the offseason
    rollover leaves the rosters alone instead of emptying them."""
    from handball import offseason

    _expire_all_contracts(years=-6, term=3, value=8)
    _as_manager("", role="commissioner")
    assert client.post("/contracts/bulk", json={"dry_run": False}).status_code == 200

    with _engine.begin() as c:
        freed = offseason._process_free_agency(c)
    assert freed == 0
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from players where team_id is not null")
                         ).scalar() == 38


def test_roster_legality_reads_a_real_league_as_ready(client, two_teams):
    """The positive half of the roster check, and the only thing that proves
    load_league_state() actually reads rosters: two fully-stocked, fully-placed
    teams must produce no blocker -- an empty read would pass a weaker assertion."""
    from handball.season_readiness import load_league_state

    from handball.league_views import DEFAULT_RULES

    state = load_league_state(_engine, _SEASON)
    assert {r.name for r in state.rosters} == {"Boston", "Denver"}
    for roster in state.rosters:
        # 3 start + 2 bench everywhere, +1 reserve at Forward and Defense; 1+1 Goalie.
        assert roster.by_position == {"Forward": 6, "Midfielder": 5,
                                      "Defense": 6, "Goalie": 2}
        assert roster.shortfalls(DEFAULT_RULES) == []
        assert roster.reserves(DEFAULT_RULES) == 2
        assert roster.unplaced == 0

    _seed_one_week_schedule()
    _as_manager("", role="commissioner")
    assert client.get("/season/state").json()["season_ready"] is True


def test_a_team_short_a_position_blocks_the_first_period(client, two_teams):
    """Retiring both goalies is the ordinary end-of-offseason hole: the roster is
    still there, it just can't be arranged. Retirement clears team_id, so the
    players leave the roster count entirely."""
    with _engine.begin() as c:
        c.execute(text("update players set retired = true, team_id = null, "
                       "slot_group = null, slot_position = null, slot_order = null "
                       "where position = 'Goalie' and legacy_id like 'boston-%'"))
    _seed_one_week_schedule()
    _as_manager("", role="commissioner")

    state = client.get("/season/state").json()
    assert state["season_ready"] is False
    found = [b for b in state["season_blockers"] if b["check"] == "roster_legality"]
    assert len(found) == 1 and found[0]["subject"] == "Boston"
    assert found[0]["detail"]["shortfalls"] == [{"position": "Goalie", "have": 0, "needs": 2}]

    r = client.post("/periods/run")
    assert r.status_code == 409
    assert any("cannot field a legal lineup" in p for p in r.json()["detail"]["problems"])


def test_an_unplaced_player_blocks_the_first_period(client, two_teams):
    """A signing into an incomplete roster stays unplaced (try_rebuild_layout is
    best-effort); once the roster is whole, that player is still not in the lineup."""
    with _engine.begin() as c:
        c.execute(text("update players set slot_group = null, slot_position = null, "
                       "slot_order = null where legacy_id = 'boston-f5'"))
    _seed_one_week_schedule()
    _as_manager("", role="commissioner")

    found = [b for b in client.get("/season/state").json()["season_blockers"]
             if b["check"] == "roster_legality"]
    assert len(found) == 1 and found[0]["subject"] == "Boston"
    assert found[0]["detail"]["unplaced"] == 1
    assert "not in its lineup" in found[0]["message"]


def test_run_period_rejects_concurrent_run(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:
        c.execute(text("update season_state set run_status='running' where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "already running" in r.json()["detail"].lower()


def test_run_period_requires_commissioner(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("Boston")                        # plain manager
    assert client.post("/periods/run").status_code == 403


def test_run_period_blocked_without_schedule(client, two_teams):
    _as_manager("", role="commissioner")         # no schedule seeded
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "schedule" in r.json()["detail"].lower()


def test_run_period_blocked_when_trade_queue_dirty(client, two_teams):
    _seed_one_week_schedule()
    # an accepted-but-unapproved trade must block a run
    boston, denver = _uuid_of("Boston"), _uuid_of("Denver")
    with _engine.begin() as c:
        c.execute(
            text("insert into trades (from_team_id, to_team_id, status) "
                 "values (cast(:f as uuid), cast(:t as uuid), 'accepted')"),
            {"f": boston, "t": denver},
        )
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "queue" in r.json()["detail"].lower()


def test_run_period_blocked_when_season_complete(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run = 5 where season = :s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "complete" in r.json()["detail"].lower()


def test_save_schedule_rejects_unknown_team(two_teams):
    with pytest.raises(sched_repo.ScheduleError):
        sched_repo.save_schedule(
            _engine, _SEASON,
            {"weeks": [[{"team1": "Boston", "team2": "Atlantis", "matchup_type": "div"}]]},
        )


def test_stale_running_blocks_run_until_reset(client, two_teams):
    _seed_one_week_schedule()
    # a 'running' row whose heartbeat is 10 min old == a dead worker
    with _engine.begin() as c:
        c.execute(
            text("update season_state set run_status='running', run_period=1, "
                 "updated_at = now() - interval '10 minutes' where season=:s"),
            {"s": _SEASON},
        )
    _as_manager("", role="commissioner")

    assert client.get("/season/state").json()["run_stale"] is True
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "reset" in r.json()["detail"].lower()

    assert client.post("/periods/reset").json()["run_status"] == "idle"
    assert client.get("/season/state").json()["run_status"] == "idle"


def test_reset_rolls_back_partial_period_games_and_records(client, two_teams):
    _seed_one_week_schedule()
    boston, denver = _uuid_of("Boston"), _uuid_of("Denver")
    # simulate a partial run: one period-1 game written + records moved, then died
    with _engine.begin() as c:
        c.execute(
            text("insert into games (season, week, home_team_id, away_team_id, home_score, away_score) "
                 "values (:s, 1, cast(:h as uuid), cast(:a as uuid), 30, 20)"),
            {"s": _SEASON, "h": boston, "a": denver},
        )
        c.execute(text("update teams set wins=1 where id=cast(:h as uuid)"), {"h": boston})
        c.execute(text("update teams set losses=1 where id=cast(:a as uuid)"), {"a": denver})
        c.execute(
            text("update season_state set run_status='error', run_period=1, run_error='boom' where season=:s"),
            {"s": _SEASON},
        )
    _as_manager("", role="commissioner")

    r = client.post("/periods/reset")
    assert r.status_code == 200, r.text
    assert r.json()["rolled_back_games"] == 1

    with _engine.connect() as c:
        assert c.execute(text("select count(*) from games where season=:s"), {"s": _SEASON}).scalar_one() == 0
        rec = c.execute(text("select wins, losses, ties from teams where id=cast(:h as uuid)"), {"h": boston}).first()
        assert tuple(rec) == (0, 0, 0)         # the win was rolled back
        rec = c.execute(text("select wins, losses, ties from teams where id=cast(:a as uuid)"), {"a": denver}).first()
        assert tuple(rec) == (0, 0, 0)         # the loss was rolled back
    assert client.get("/season/state").json()["run_status"] == "idle"


def test_reset_rejected_while_genuinely_running(client, two_teams):
    _seed_one_week_schedule()
    with _engine.begin() as c:   # fresh heartbeat => a live run, not stale
        c.execute(
            text("update season_state set run_status='running', run_period=1, updated_at=now() where season=:s"),
            {"s": _SEASON},
        )
    _as_manager("", role="commissioner")
    r = client.post("/periods/reset")
    assert r.status_code == 409
    assert "in progress" in r.json()["detail"].lower()


# -- standings endpoint ----------------------------------------------------
def test_standings_ranks_by_points(client, two_teams):
    """Denver finishes on more POINTS despite fewer wins -- the key that also seeds
    the bracket. Any authenticated manager may read the table."""
    with _engine.begin() as c:
        c.execute(text("update teams set wins=30, losses=20, ties=5 where slug='Boston'"))
        c.execute(text("update teams set wins=29, losses=15, ties=11 where slug='Denver'"))
    _as_manager("Boston")

    rows = client.get("/standings").json()["teams"]
    assert [r["slug"] for r in rows] == ["Denver", "Boston"]
    assert [r["points"] for r in rows] == [29 * 3 + 11, 30 * 3 + 5]
    assert [r["rank"] for r in rows] == [1, 2]


def test_standings_carries_conference_and_division(client, two_teams):
    _as_manager("Boston")
    rows = {r["slug"]: r for r in client.get("/standings").json()["teams"]}
    assert (rows["Boston"]["conference"], rows["Boston"]["division"]) == ("Eastern", "Mid-Atlantic")
    assert rows["Denver"]["conference"] == "Western"
    # Each leads its division, and playoff_seed is a PROJECTION -- where the team
    # would be seeded if the season ended now, over whatever field exists (seeding
    # the real bracket still demands a full eight per conference).
    assert rows["Boston"]["division_leader"] and rows["Denver"]["division_leader"]
    assert rows["Boston"]["playoff_seed"] == 1 and rows["Denver"]["playoff_seed"] == 1


def test_standings_head_to_head_outranks_goal_difference(client, two_teams):
    """Level on points: Boston took the season series 2-1 but was blown out in the
    one it lost, so its goal difference is far worse. Head-to-head comes first."""
    with _engine.begin() as c:
        c.execute(text("update teams set wins=10, losses=5, ties=0"))
        ids = dict(c.execute(text("select slug, id from teams")).all())
        for week, (home, away, hs, as_) in enumerate((
            ("Boston", "Denver", 1, 0),
            ("Boston", "Denver", 1, 0),
            ("Denver", "Boston", 10, 0),
        ), start=1):
            c.execute(
                text("insert into games (season, week, home_team_id, away_team_id, "
                     "home_score, away_score, is_playoff) "
                     "values (:s, :w, :h, :a, :hs, :as_, false)"),
                {"s": _SEASON, "w": week, "h": ids[home], "a": ids[away],
                 "hs": hs, "as_": as_},
            )
    _as_manager("Boston")

    rows = client.get("/standings").json()["teams"]
    assert [r["slug"] for r in rows] == ["Boston", "Denver"]
    assert rows[0]["goal_diff"] == -8 and rows[1]["goal_diff"] == 8   # GD says Denver


def test_standings_counts_only_regular_season_goals(client, two_teams):
    """Goal difference is a tiebreaker, so a playoff blowout must not feed it."""
    with _engine.begin() as c:
        ids = dict(c.execute(text("select slug, id from teams")).all())
        for is_playoff, hs, as_ in ((False, 3, 1), (True, 40, 0)):
            c.execute(
                text("insert into games (season, week, home_team_id, away_team_id, "
                     "home_score, away_score, is_playoff) "
                     "values (:s, :w, :h, :a, :hs, :as_, :p)"),
                {"s": _SEASON, "w": None if is_playoff else 1, "h": ids["Boston"],
                 "a": ids["Denver"], "hs": hs, "as_": as_, "p": is_playoff},
            )
    _as_manager("Boston")

    rows = {r["slug"]: r for r in client.get("/standings").json()["teams"]}
    assert (rows["Boston"]["goals_for"], rows["Boston"]["goals_against"]) == (3, 1)
    assert rows["Boston"]["goal_diff"] == 2      # not 42


# -- postseason endpoints --------------------------------------------------
def _crown_champion(winner: str = "Boston", loser: str = "Denver", season: int = _SEASON):
    """A finished bracket, the shortest one that exists: a single decided Final.
    handball/playoffs reads the champion off the last round's only series, so this is
    all the postseason state the offseason gate cares about."""
    with _engine.begin() as c:
        c.execute(
            text("insert into playoff_series (season, round, conference, label, "
                 "high_seed_team_id, low_seed_team_id, high_seed, low_seed, winner_team_id) "
                 "select :s, 1, null, 'Final', "
                 "(select id from teams where slug = :w), "
                 "(select id from teams where slug = :l), 1, 1, "
                 "(select id from teams where slug = :w)"),
            {"s": season, "w": winner, "l": loser},
        )


def test_playoff_bracket_is_readable_by_any_manager(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    _as_manager("Boston")                        # a plain manager, not commissioner
    b = client.get("/playoffs/bracket").json()
    assert b["started"] is False and b["series"] == []
    assert b["champion"] is None and b["total_rounds"] == 4

    _crown_champion()
    b = client.get("/playoffs/bracket").json()
    assert b["started"] and b["complete"] and b["champion"] == "Boston"


def test_playoff_actions_are_commissioner_only(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    _as_manager("Boston")
    assert client.post("/playoffs/start").status_code == 403
    assert client.post("/playoffs/rounds/run").status_code == 403
    assert client.post("/playoffs/reset").status_code == 403


def test_playoffs_start_requires_a_finished_regular_season(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=3 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/playoffs/start")
    assert r.status_code == 409
    assert "regular season" in r.json()["detail"].lower()


def test_playoffs_start_reports_an_unseedable_league(client, two_teams):
    """Two teams cannot fill an 8-per-conference bracket; the seeding rule says so
    rather than quietly building a two-team postseason."""
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/playoffs/start")
    assert r.status_code == 409
    assert "8 teams" in r.json()["detail"]


def test_running_a_round_needs_a_bracket(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/playoffs/rounds/run")
    assert r.status_code == 409 and "seed the bracket" in r.json()["detail"]


def test_running_a_round_is_refused_once_complete(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    _crown_champion()
    _as_manager("", role="commissioner")
    r = client.post("/playoffs/rounds/run")
    assert r.status_code == 409 and "complete" in r.json()["detail"]


def test_period_reset_refuses_a_failed_playoff_round(client, two_teams):
    """One run slot, two phases: recovering a failed playoff round through the
    period path would delete a week range of regular-season games."""
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    sched_repo.set_run_status(_engine, _SEASON, "error", period=1, kind="playoff", error="boom")
    _as_manager("", role="commissioner")
    r = client.post("/periods/reset")
    assert r.status_code == 409 and "/playoffs/reset" in r.json()["detail"]


def test_season_state_carries_the_postseason_cursor(client, two_teams):
    _seed_one_week_schedule()
    _as_manager("Boston")
    s = client.get("/season/state").json()
    assert s["playoffs_started"] is False and s["playoffs_complete"] is False
    assert s["playoff_total_rounds"] == 4 and s["champion"] is None

    _crown_champion()
    s = client.get("/season/state").json()
    assert s["playoffs_started"] and s["playoffs_complete"]
    assert s["champion"] == "Boston"


# -- offseason endpoints ---------------------------------------------------
def test_advance_requires_a_champion(client, two_teams):
    """The rollover zeroes the records the bracket was seeded from, so it waits for
    the postseason to finish."""
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/season/advance")
    assert r.status_code == 409
    assert "champion" in r.json()["detail"].lower()


def test_advance_requires_complete_season(client, two_teams):
    # season_state exists but the regular season isn't finished
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=2 where season=:s"), {"s": _SEASON})
    _as_manager("", role="commissioner")
    r = client.post("/season/advance")
    assert r.status_code == 409
    assert "regular season" in r.json()["detail"].lower()


def test_advance_requires_commissioner(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _as_manager("Boston")  # plain manager
    assert client.post("/season/advance").status_code == 403
    assert client.get("/retirement/candidates").status_code == 403


def test_advance_season_happy_path(client, two_teams):
    sched_repo.init_season_state(_engine, _SEASON, schedule_seed=_SEASON, injury_seed=_SEASON)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=5 where season=:s"), {"s": _SEASON})
    _crown_champion()                            # the postseason is over too
    _as_manager("", role="commissioner")

    r = client.post("/season/advance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_season"] == _SEASON + 1
    # records zeroed, new season opened, players aged
    assert client.get("/season/state").json()["season"] == _SEASON + 1
    with _engine.connect() as c:
        assert c.execute(text("select 1 from season_state where season=:s"), {"s": _SEASON + 1}).first() is not None


def test_lineup_save_does_not_wipe_awards(client, two_teams):
    # an award written for a player must survive a routine roster save() of their team
    repo = two_teams
    boston = repo.load("Boston")
    pid = boston.starters["Forward"][0].id
    with _engine.begin() as c:
        puid = c.execute(text("select id from players where legacy_id=:l"), {"l": pid}).scalar_one()
        c.execute(text("insert into awards (player_id, season, award) values (:p, :s, 'League MVP')"),
                  {"p": puid, "s": _SEASON})
    repo.save(repo.load("Boston"))   # routine save (e.g. a lineup edit)
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from awards where player_id=:p"), {"p": puid}).scalar_one() == 1


def test_internal_trade_auto_accepts_then_commissioner_commits(client, two_teams):
    repo = two_teams
    # one manager owns both teams -> trade between them is "internal"
    _as_manager("Boston", "Denver")
    r = client.post("/trades", json={"from_team": "Boston", "to_team": "Denver",
                                     "players_out": ["boston-r1"], "players_in": ["denver-f5"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["internal"] is True and body["status"] == "accepted"   # skipped counterparty
    trade_id = body["trade_id"]

    # still needs commissioner approval to commit
    _as_manager(role="commissioner")
    assert client.post(f"/trades/{trade_id}/approve").json()["status"] == "committed"
    assert repo.load("Denver").get("boston-r1") is not None
    assert repo.load("Boston").get("denver-f5") is not None
