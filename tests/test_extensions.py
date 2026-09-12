"""
Contract extensions (handball/extensions.py): the pure rules DB-free, then the write
path, the window, the rollover and the audit against the dev Postgres.

The pure half runs everywhere. The Postgres half is local-DB only (it truncates) and
skips when the dev database isn't available. season_state is truncated before AND
after every test in here: this module drives the league clock (the extension window is
a period count), and a leftover cursor would move the window -- or the trade deadline
-- under a later test module.
"""
import uuid

import pytest
from sqlalchemy import text

from handball import contract_admin, offseason
from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.extensions import (
    ExtensionCandidate,
    ExtensionContext,
    ExtensionError,
    check_extension,
    eligible_players,
    is_eligible,
    max_extension_term,
    max_extension_value,
    offer_extension,
    projected_next_payroll,
    windows,
)
from handball.pg_repository import PostgresTeamRepository
from handball.salary_cap import can_sign
from handball.signing_service import team_cap_report
from handball.simulation_vars import (
    EXTENSION_WINDOW_AFTER_PERIOD,
    HARD_CAP,
    MAX_CONTRACT_YEARS,
    SALARY_CAP,
)
from handball.trade_service import accept_trade, approve_trade, propose_trade

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

# Only the database half skips; the rules below need no database at all.
needs_pg = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

_SEASON = 2026


# ============================================================================
# the rules, DB-free
# ============================================================================
def _cand(pid, *, years=1, value=10, term=3, ext_term=None, ext_value=None):
    return ExtensionCandidate(
        player_id=pid, name=pid.title(), position="Forward",
        contract_term=term, contract_value=value, years_remaining=years,
        ext_term=ext_term, ext_value=ext_value,
        ext_signed_season=None if ext_term is None else _SEASON,
    )


def _ctx(*roster, window_open=True):
    return ExtensionContext(team_id="team-uuid", team_name="Boston", season=_SEASON,
                            window_open=window_open, roster=tuple(roster))


# -- eligibility -------------------------------------------------------------
def test_only_a_player_in_the_last_year_is_eligible():
    assert is_eligible(_cand("a", years=1))
    assert not is_eligible(_cand("b", years=2))
    assert not is_eligible(_cand("c", years=0))


def test_a_player_who_already_signed_one_is_not_eligible_again():
    assert not is_eligible(_cand("a", years=1, ext_term=3, ext_value=10))


def test_the_window_is_checked_before_anything_else():
    ctx = _ctx(_cand("a"), window_open=False)
    with pytest.raises(ExtensionError, match="extension window is closed"):
        check_extension(ctx, "a", 3, 10)


def test_a_player_on_another_roster_cannot_be_extended():
    with pytest.raises(ExtensionError, match="not on Boston's roster"):
        check_extension(_ctx(_cand("a")), "somebody-else", 3, 10)


def test_extending_twice_is_refused():
    ctx = _ctx(_cand("a", ext_term=2, ext_value=8))
    with pytest.raises(ExtensionError, match="already signed an extension"):
        check_extension(ctx, "a", 3, 10)


def test_a_player_with_years_left_is_refused():
    ctx = _ctx(_cand("a", years=3))
    with pytest.raises(ExtensionError, match="only a player in the last year"):
        check_extension(ctx, "a", 2, 10)


# -- the five-year total -----------------------------------------------------
def test_the_term_ceiling_is_the_five_year_total():
    # The extension tacks onto the year still being played, so 1 + 4 = the league max.
    c = _cand("a", years=1)
    assert max_extension_term(c) == MAX_CONTRACT_YEARS - 1 == 4
    check_extension(_ctx(c), "a", 4, 10)                    # exactly five in total: fine


def test_a_term_past_the_five_year_total_is_refused():
    ctx = _ctx(_cand("a", years=1))
    with pytest.raises(ExtensionError, match=r"out of range 1\.\.4"):
        check_extension(ctx, "a", 5, 10)
    with pytest.raises(ExtensionError, match=r"out of range 1\.\.4"):
        check_extension(ctx, "a", 0, 10)


# -- the projection ----------------------------------------------------------
def test_the_projection_counts_next_season_not_this_one():
    roster = [
        _cand("leaving", years=1, value=40),       # expires; counts for nothing
        _cand("staying", years=3, value=20),       # still under contract next season
        _cand("extended", years=1, value=40, ext_term=3, ext_value=15),  # new deal
    ]
    # $20M of contract + $15M of extension. The two $40M salaries being paid THIS
    # season are irrelevant: neither is on the books next season.
    assert projected_next_payroll(roster) == 35
    assert projected_next_payroll(roster, extending="leaving", value=12) == 47


def test_the_hard_cap_binds_the_projection_not_todays_payroll():
    # Next season's committed payroll is $30M short of the hard cap.
    roster = [_cand("locked", years=4, value=HARD_CAP - 30), _cand("a", years=1, value=45)]
    ctx = _ctx(*roster)
    assert max_extension_value(roster, "a") == 30
    check_extension(ctx, "a", 3, 30)                        # exactly to the cap: fine
    with pytest.raises(ExtensionError, match="hard cap"):
        check_extension(ctx, "a", 3, 31)


def test_extensions_already_signed_count_against_the_next_one():
    # Same team, but $10M of the $30M has already been promised to somebody else.
    roster = [
        _cand("locked", years=4, value=HARD_CAP - 30),
        _cand("promised", years=1, value=45, ext_term=2, ext_value=10),
        _cand("a", years=1, value=45),
    ]
    ctx = _ctx(*roster)
    assert max_extension_value(roster, "a") == 20
    check_extension(ctx, "a", 3, 20)
    with pytest.raises(ExtensionError, match="hard cap"):
        check_extension(ctx, "a", 3, 30)   # the deal that fit before the other promise


def test_bird_rights_mean_the_soft_cap_does_not_bind_an_extension():
    # A team a long way over the soft cap, with $40M of room under the hard one.
    payroll = HARD_CAP - 40
    assert payroll > SALARY_CAP
    roster = [_cand("locked", years=4, value=payroll), _cand("a", years=1, value=45)]
    offer = HARD_CAP - payroll
    # An OUTSIDE signing of the same money is impossible at this payroll...
    assert not can_sign(payroll, offer, own_player=False)
    # ...but re-upping your own player is bounded by the hard cap alone.
    check_extension(_ctx(*roster), "a", 4, offer)
    with pytest.raises(ExtensionError, match="hard cap"):
        check_extension(_ctx(*roster), "a", 4, offer + 1)


def test_the_contract_limits_still_apply():
    with pytest.raises(ExtensionError, match="out of range"):
        check_extension(_ctx(_cand("a")), "a", 3, 10_000)


# ============================================================================
# the database side
# ============================================================================
_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets season_state")


@pytest.fixture(autouse=True)
def _clean_db():
    if not _PG_OK:
        yield
        return
    _truncate()
    yield
    _truncate()               # season_state is the league clock: never leave one set
    if _PG_OK:
        from api.main import app
        app.dependency_overrides.clear()


def _truncate():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))


def _team(team_id: str) -> Team:
    """A legal 19-player roster (10 starters + 7 bench + 2 reserves), every contract
    $0 with no years on it -- the tests set the contracts they care about."""
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


@pytest.fixture
def league():
    repo = PostgresTeamRepository(_engine)
    repo.save(_team("Boston"))
    repo.save(_team("Denver"))
    return repo


def _periods(n: int, *, run_status: str = "idle", season: int = _SEASON) -> None:
    """Put the league clock at `n` periods run of `season`."""
    with _engine.begin() as c:
        c.execute(
            text("insert into season_state (season, periods_run, run_status) "
                 "values (:s, :n, :st) on conflict (season) do update set "
                 "periods_run = :n, run_status = :st"),
            {"s": season, "n": n, "st": run_status},
        )


def _window_open() -> None:
    _periods(EXTENSION_WINDOW_AFTER_PERIOD)


def _contract(legacy_id: str, term: int, value: int, years: int | None = None) -> None:
    with _engine.begin() as c:
        c.execute(
            text("update players set contract_term = :t, contract_value = :v, "
                 "years_remaining = :y where legacy_id = :lid"),
            {"t": term, "v": value, "y": term if years is None else years, "lid": legacy_id},
        )


def _row(legacy_id: str):
    with _engine.connect() as c:
        return c.execute(
            text("select p.ext_term, p.ext_value, p.ext_signed_season, p.contract_term, "
                 "p.contract_value, p.years_remaining, p.rookie_contract, "
                 "p.restricted_free_agent, t.slug as team_slug "
                 "from players p left join teams t on t.id = p.team_id "
                 "where p.legacy_id = :lid"),
            {"lid": legacy_id},
        ).mappings().one()


# -- the window --------------------------------------------------------------
@needs_pg
def test_the_window_opens_and_shuts_with_the_period_count(league):
    _contract("boston-f4", 3, 10, years=1)

    _periods(0)
    assert windows(_engine)["extension_window_open"] is False
    with pytest.raises(ExtensionError, match="extension window is closed"):
        offer_extension(_engine, "Boston", "boston-f4", 3, 10)

    _window_open()
    assert windows(_engine)["extension_window_open"] is True

    _periods(EXTENSION_WINDOW_AFTER_PERIOD + 1)
    assert windows(_engine)["extension_window_open"] is False
    with pytest.raises(ExtensionError, match="extension window is closed"):
        offer_extension(_engine, "Boston", "boston-f4", 3, 10)


@needs_pg
def test_the_window_is_shut_while_a_period_is_simulating(league):
    _contract("boston-f4", 3, 10, years=1)
    _periods(EXTENSION_WINDOW_AFTER_PERIOD, run_status="running")
    assert windows(_engine)["extension_window_open"] is False
    with pytest.raises(ExtensionError, match="extension window is closed"):
        offer_extension(_engine, "Boston", "boston-f4", 3, 10)


@needs_pg
def test_a_league_that_has_never_run_a_period_has_no_windows():
    assert windows(_engine) == {
        "season": None, "periods_run": 0,
        "extension_window_open": False, "trade_deadline_passed": False,
        "extension_window_after_period": EXTENSION_WINDOW_AFTER_PERIOD,
        "trade_deadline_after_period": 4,
    }


# -- signing one -------------------------------------------------------------
@needs_pg
def test_offering_an_extension_writes_the_three_columns(league):
    _contract("boston-f4", 3, 12, years=1)
    _window_open()

    result = offer_extension(_engine, "Boston", "boston-f4", 4, 20)

    assert (result["term"], result["value"]) == (4, 20)
    assert result["signed_season"] == _SEASON and result["starts_season"] == _SEASON + 1
    row = _row("boston-f4")
    assert (row["ext_term"], row["ext_value"], row["ext_signed_season"]) == (4, 20, _SEASON)
    # the CURRENT contract is untouched -- this season is still played on the old deal
    assert (row["contract_term"], row["contract_value"], row["years_remaining"]) == (3, 12, 1)


@needs_pg
def test_eligible_players_lists_who_can_be_extended_and_for_how_much(league):
    _contract("boston-f4", 3, 10, years=1)          # last year: eligible
    _contract("boston-f5", 3, HARD_CAP - 30, years=3)   # years left: not eligible
    _window_open()

    report = eligible_players(_engine, "Boston")

    assert report["extension_window_open"] is True
    assert [p["player_id"] for p in report["players"]] == ["boston-f4"]
    p = report["players"][0]
    assert p["max_term"] == MAX_CONTRACT_YEARS - 1
    assert p["max_value"] == 30                      # boston-f5 is next season's payroll
    assert report["projected_next_payroll"] == HARD_CAP - 30

    offer_extension(_engine, "Boston", "boston-f4", 2, 15)
    after = eligible_players(_engine, "Boston")
    assert after["players"] == []                    # extended once, and once only
    assert after["extended"] == [{
        "player_id": "boston-f4", "name": "Boston F4", "position": "Forward",
        "ext_term": 2, "ext_value": 15, "ext_signed_season": _SEASON,
    }]
    assert after["projected_next_payroll"] == HARD_CAP - 15


@needs_pg
def test_the_projection_is_enforced_against_the_real_roster(league):
    _contract("boston-f5", 4, HARD_CAP - 20, years=4)     # $230M committed next season
    _contract("boston-f4", 3, 10, years=1)
    _window_open()

    with pytest.raises(ExtensionError, match="hard cap"):
        offer_extension(_engine, "Boston", "boston-f4", 3, 21)
    assert _row("boston-f4")["ext_term"] is None          # rolled back

    offer_extension(_engine, "Boston", "boston-f4", 3, 20)
    assert _row("boston-f4")["ext_term"] == 3


@needs_pg
def test_an_extension_you_cannot_afford_after_the_first_one(league):
    _contract("boston-f5", 4, HARD_CAP - 30, years=4)
    _contract("boston-f4", 3, 10, years=1)
    _contract("boston-r1", 3, 10, years=1)
    _window_open()

    offer_extension(_engine, "Boston", "boston-f4", 2, 25)
    with pytest.raises(ExtensionError, match="hard cap"):
        offer_extension(_engine, "Boston", "boston-r1", 2, 10)     # only $5M left
    offer_extension(_engine, "Boston", "boston-r1", 2, 5)


@needs_pg
def test_a_player_on_another_team_cannot_be_extended(league):
    _contract("denver-f4", 3, 10, years=1)
    _window_open()
    with pytest.raises(ExtensionError, match="not on Boston's roster"):
        offer_extension(_engine, "Boston", "denver-f4", 3, 10)


# -- what an extension survives ---------------------------------------------
@needs_pg
def test_an_extension_travels_with_the_player_in_a_trade(league):
    _contract("boston-r1", 3, 10, years=1)
    _window_open()
    offer_extension(_engine, "Boston", "boston-r1", 3, 18)

    # The deadline hasn't passed at period 1, so the trade goes through normally.
    tid = propose_trade(_engine, "Boston", "Denver",
                        players_out=["boston-r1"], players_in=["denver-f5"])
    accept_trade(_engine, tid)
    approve_trade(_engine, tid)

    row = _row("boston-r1")
    assert row["team_slug"] == "Denver"
    # the promise is the player's, not the team's: Denver inherits it
    assert (row["ext_term"], row["ext_value"]) == (3, 18)
    assert eligible_players(_engine, "Denver")["projected_next_payroll"] == 18
    assert eligible_players(_engine, "Boston")["projected_next_payroll"] == 0


# -- the rollover ------------------------------------------------------------
@needs_pg
def test_the_rollover_applies_the_extension_and_the_player_stays(league):
    _contract("boston-f4", 3, 10, years=1)          # extended
    _contract("boston-f5", 3, 10, years=1)          # not extended: leaves
    _window_open()
    offer_extension(_engine, "Boston", "boston-f4", 4, 25)

    summary = offseason.advance_season(_engine, _SEASON, ["Boston", "Denver"])

    assert summary["extensions_applied"] == 1
    extended = _row("boston-f4")
    assert extended["team_slug"] == "Boston"                     # never hit the pool
    assert (extended["contract_term"], extended["contract_value"]) == (4, 25)
    assert extended["years_remaining"] == 4                      # a fresh deal
    assert extended["ext_term"] is None and extended["ext_value"] is None
    assert extended["ext_signed_season"] is None                 # the queue is drained
    # the player who wasn't extended did exactly what they always did
    assert _row("boston-f5")["team_slug"] is None
    assert summary["new_free_agents"] >= 1


@needs_pg
def test_the_rollover_clears_the_restricted_and_rookie_flags(league):
    # A rookie deal running out: extending it makes them a signed veteran, not a
    # restricted free agent -- they are not a free agent at all.
    _contract("boston-f4", 3, 10, years=1)
    with _engine.begin() as c:
        c.execute(text("update players set rookie_contract = true, "
                       "restricted_free_agent = true where legacy_id = 'boston-f4'"))
    _window_open()
    offer_extension(_engine, "Boston", "boston-f4", 3, 15)

    offseason.advance_season(_engine, _SEASON, ["Boston", "Denver"])

    row = _row("boston-f4")
    assert row["rookie_contract"] is False and row["restricted_free_agent"] is False


@needs_pg
def test_an_extension_signed_for_a_player_with_years_left_waits(league):
    """The rollover only applies an extension when the old deal has actually run out.
    (Reaching this state takes a bulk contract repair after the extension was signed;
    the rules never hand out an extension to a player with 2 years left.)"""
    _contract("boston-f4", 3, 10, years=1)
    _window_open()
    offer_extension(_engine, "Boston", "boston-f4", 3, 15)
    _contract("boston-f4", 3, 10, years=3)          # repaired afterwards

    offseason.advance_season(_engine, _SEASON, ["Boston", "Denver"])

    row = _row("boston-f4")
    assert row["years_remaining"] == 2 and row["contract_value"] == 10
    assert row["ext_term"] == 3                     # still waiting for the deal to end


# -- the audit ---------------------------------------------------------------
@needs_pg
def test_the_audit_does_not_count_an_extended_player_as_expiring(league):
    _contract("boston-f4", 3, 10, years=1)
    _contract("boston-f5", 3, 10, years=1)
    _window_open()

    before = contract_admin.audit(_engine)
    offer_extension(_engine, "Boston", "boston-f4", 3, 15)
    after = contract_admin.audit(_engine)

    assert after["expiring_next_rollover"] == before["expiring_next_rollover"] - 1
    # ...and the cohort is unchanged: the player still has one year on the old deal,
    # they just aren't leaving at the end of it.
    assert after["expiry_cohorts"] == before["expiry_cohorts"]


# -- the cap report ----------------------------------------------------------
@needs_pg
def test_the_cap_report_carries_the_projection_and_the_window(league):
    _contract("boston-f4", 3, 40, years=1)          # expiring: not next season's money
    _contract("boston-f5", 3, 30, years=3)
    _window_open()

    report = team_cap_report(_engine, "Boston")
    assert report["payroll"] == 70                  # what is being paid today
    assert report["projected_next_payroll"] == 30   # what is owed next season
    assert report["extension_window_open"] is True

    offer_extension(_engine, "Boston", "boston-f4", 2, 25)
    assert team_cap_report(_engine, "Boston")["projected_next_payroll"] == 55


# -- through the API ---------------------------------------------------------
def _as_manager(*slugs: str, role: str = "manager"):
    """Override auth to act as a manager owning the given team slug(s) (or a
    commissioner when none are given). Mirrors tests/test_api.py."""
    from api.auth import Manager, get_current_manager
    from api.main import app

    with _engine.connect() as c:
        owned = [str(c.execute(text("select id from teams where slug=:s"),
                               {"s": s}).scalar_one()) for s in slugs if s]
    user_id = str(uuid.uuid4())
    with _engine.begin() as c:
        c.execute(text("insert into managers (user_id, role) values (cast(:u as uuid), :r)"),
                  {"u": user_id, "r": role})
        for tid in owned:
            c.execute(text("update teams set owner_id = cast(:u as uuid) "
                           "where id = cast(:t as uuid)"), {"u": user_id, "t": tid})
    app.dependency_overrides[get_current_manager] = lambda: Manager(
        user_id=user_id, owned_team_ids=owned, role=role)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app
    return TestClient(app)


@needs_pg
def test_api_windows_and_extension_round_trip(client, league):
    _contract("boston-f4", 3, 10, years=1)
    _window_open()
    _as_manager("Boston")

    assert client.get("/contracts/windows").json()["extension_window_open"] is True

    r = client.get("/contracts/extensions/eligible", params={"team": "Boston"})
    assert r.status_code == 200, r.text
    assert [p["player_id"] for p in r.json()["players"]] == ["boston-f4"]

    r = client.post("/contracts/extensions",
                    json={"team": "Boston", "player_id": "boston-f4", "term": 3, "value": 20})
    assert r.status_code == 201, r.text
    assert _row("boston-f4")["ext_term"] == 3


@needs_pg
def test_api_refuses_an_extension_outside_the_window(client, league):
    _contract("boston-f4", 3, 10, years=1)
    _periods(3)
    _as_manager("Boston")
    r = client.post("/contracts/extensions",
                    json={"team": "Boston", "player_id": "boston-f4", "term": 3, "value": 20})
    assert r.status_code == 409 and "window is closed" in r.json()["detail"]


@needs_pg
def test_api_refuses_an_extension_for_a_team_you_do_not_own(client, league):
    _contract("denver-f4", 3, 10, years=1)
    _window_open()
    _as_manager("Boston")
    r = client.post("/contracts/extensions",
                    json={"team": "Denver", "player_id": "denver-f4", "term": 3, "value": 20})
    assert r.status_code == 403


@needs_pg
def test_api_refuses_a_term_past_the_league_maximum(client, league):
    _contract("boston-f4", 3, 10, years=1)
    _window_open()
    _as_manager("Boston")
    # 6 years is not a contract at all -> 422 from the body model, before the rules
    assert client.post("/contracts/extensions",
                       json={"team": "Boston", "player_id": "boston-f4",
                             "term": 6, "value": 20}).status_code == 422
    # 5 is a legal contract but not a legal EXTENSION for a player with a year left
    r = client.post("/contracts/extensions",
                    json={"team": "Boston", "player_id": "boston-f4", "term": 5, "value": 20})
    assert r.status_code == 409 and "out of range" in r.json()["detail"]
