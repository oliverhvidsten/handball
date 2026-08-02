"""
The free-agent signing write path (handball/signing_service.py) against the dev
Postgres: eligibility, cap enforcement with and without Bird rights, roster limits,
and what the signing does to the row + the lineup. The pure rules are covered
DB-free in tests/test_signing_rules.py. Skips when Postgres is unavailable; local DB
only (truncates).
"""
import pytest
from sqlalchemy import text

from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.league_views import DEFAULT_RULES
from handball.pg_repository import PostgresTeamRepository
from handball.signing_service import (
    FREE_AGENT_CONTRACT_SALARY,
    FREE_AGENT_CONTRACT_YEARS,
    SigningError,
    sign_free_agent,
    team_cap_report,
)
from handball.simulation_vars import HARD_CAP, SALARY_CAP

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets")


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield


def _team(team_id: str) -> Team:
    """A legal 19-player roster (10 starters + 7 bench + 2 reserves), every contract
    $0 -- so payroll starts at 0 and there are 2 open roster spots."""
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


def _free_agent(legacy_id: str, position: str = "Forward", *, rights: str | None = None) -> None:
    """Insert a player with no team -- the free-agent pool is just team_id is null.
    `rights` is a team slug holding the player's Bird rights (as offseason expiry
    would have stamped it)."""
    with _engine.begin() as c:
        c.execute(
            text("insert into players (legacy_id, name, position, age, years_in_league, "
                 "offense, defense, goalie_skill, max_offense, max_defense, max_goalie_skill, "
                 "variance, peak_age, decline_age, decline_rate, is_injured, "
                 "contract_term, contract_value, years_remaining, amount_paid, "
                 "rookie_contract, restricted_free_agent, retired, rights_team_id) "
                 "values (:lid, :lid, cast(:pos as player_position), 26, 4, "
                 "6.0, 6.0, 0.1, 9.0, 9.0, 0.1, 0.5, 27, 30, 0.15, false, "
                 "0, 0, 0, 0, false, true, false, "
                 "(select id from teams where slug = :rights))"),
            {"lid": legacy_id, "pos": position, "rights": rights},
        )


def _row(legacy_id: str):
    with _engine.connect() as c:
        return c.execute(
            text("select p.team_id, p.rights_team_id, p.contract_term, p.contract_value, "
                 "p.years_remaining, p.rookie_contract, p.restricted_free_agent, "
                 "p.slot_group::text as slot_group, t.slug as team_slug "
                 "from players p left join teams t on t.id = p.team_id "
                 "where p.legacy_id = :lid"),
            {"lid": legacy_id},
        ).mappings().one()


def _set_contract(legacy_id: str, value: int, term: int = 3) -> None:
    with _engine.begin() as c:
        c.execute(
            text("update players set contract_value = :v, contract_term = :t, "
                 "years_remaining = :t where legacy_id = :lid"),
            {"v": value, "t": term, "lid": legacy_id},
        )


# -- the happy paths ---------------------------------------------------------
def test_signing_a_free_agent_puts_them_on_the_roster(league):
    _free_agent("fa-forward")

    result = sign_free_agent(_engine, "Boston", "fa-forward")

    # every free-agent deal is the same fixed league-minimum contract
    assert (result["term"], result["value"]) == (FREE_AGENT_CONTRACT_YEARS,
                                                 FREE_AGENT_CONTRACT_SALARY)
    assert result["payroll"] == 0 and result["roster_size"] == 20
    row = _row("fa-forward")
    assert row["team_slug"] == "Boston"
    assert (row["contract_term"], row["contract_value"], row["years_remaining"]) == (1, 0, 1)
    # a signed contract is no longer a rookie deal, and the player is no longer restricted
    assert row["rookie_contract"] is False and row["restricted_free_agent"] is False
    # Bird rights are consumed by the signing (this contract hasn't expired yet)
    assert row["rights_team_id"] is None
    # ...and the lineup was re-derived, so the new player holds a real slot
    assert result["placed"] is True and row["slot_group"] is not None
    assert len(league.load("Boston").roster()) == 20


def test_a_signing_that_reorders_the_depth_chart(league):
    """The rebuild rewrites slots one row at a time, and `players` has a unique index
    on (team_id, slot_group, slot_position, slot_order). A signing that displaces the
    top forward moves EVERY forward down one, so each write lands on a slot its
    previous occupant has not vacated yet -- the layout must be cleared first."""
    _free_agent("fa-star")
    with _engine.begin() as c:                      # better than Boston's best forward
        c.execute(text("update players set offense = 9.5 where legacy_id = 'fa-star'"))

    result = sign_free_agent(_engine, "Boston", "fa-star")

    assert result["placed"] is True
    with _engine.connect() as c:
        slots = c.execute(
            text("select p.legacy_id, p.slot_group::text, p.slot_position::text, p.slot_order "
                 "from players p join teams t on t.id = p.team_id "
                 "where t.slug = 'Boston' and p.slot_group is not null"),
            ).all()
    placed = {r[0]: (r[1], r[2], r[3]) for r in slots}
    assert placed["fa-star"] == ("starters", "Forward", 0)      # the new best forward
    assert placed["boston-f1"] != ("starters", "Forward", 0)    # incumbent moved down
    assert len(set(placed.values())) == len(placed)             # no two share a slot


def test_a_signing_costs_no_cap_space(league):
    _set_contract("boston-d1", SALARY_CAP + 30)          # deep into the luxury tax
    _free_agent("fa-forward")

    sign_free_agent(_engine, "Boston", "fa-forward")

    assert team_cap_report(_engine, "Boston")["payroll"] == SALARY_CAP + 30   # unmoved


def test_signing_leaves_the_other_team_alone(league):
    _free_agent("fa-forward")
    sign_free_agent(_engine, "Boston", "fa-forward")
    assert len(league.load("Denver").roster()) == 19
    assert team_cap_report(_engine, "Denver")["payroll"] == 0


# -- Bird rights -------------------------------------------------------------
def test_signing_your_own_expired_player_is_reported_as_a_bird_rights_signing(league):
    """Rights don't change the deal here (it's fixed), but they're recognised and
    consumed -- the re-signing process is what prices them."""
    _free_agent("boston-expired", rights="Boston")

    result = sign_free_agent(_engine, "Boston", "boston-expired")

    assert result["bird_rights"] is True
    assert _row("boston-expired")["rights_team_id"] is None


def test_rights_do_not_stop_another_team_signing_your_free_agent(league):
    """Bird rights are a cap privilege for the old team, not an exclusive window: once
    a player is in the pool, anyone with a roster spot may sign them."""
    _free_agent("boston-expired", rights="Boston")

    result = sign_free_agent(_engine, "Denver", "boston-expired")

    assert result["bird_rights"] is False
    assert _row("boston-expired")["team_slug"] == "Denver"


# -- rejections --------------------------------------------------------------
def test_a_team_over_the_hard_cap_may_not_add_players(league):
    """Only rookie draft deals can push a team past the hard cap; from there it may
    shed salary, not accumulate players -- the same rule trades follow."""
    _set_contract("boston-d1", HARD_CAP + 10)
    _free_agent("fa-forward")

    with pytest.raises(SigningError, match="hard cap"):
        sign_free_agent(_engine, "Boston", "fa-forward")

    assert _row("fa-forward")["team_slug"] is None               # nothing written


def test_a_team_exactly_at_the_hard_cap_may_still_sign(league):
    _set_contract("boston-d1", HARD_CAP)
    _free_agent("fa-forward")
    sign_free_agent(_engine, "Boston", "fa-forward")
    assert _row("fa-forward")["team_slug"] == "Boston"


def test_a_rostered_player_cannot_be_signed(league):
    with pytest.raises(SigningError, match="not a free agent"):
        sign_free_agent(_engine, "Denver", "boston-r1")
    with pytest.raises(SigningError, match="already under contract"):
        sign_free_agent(_engine, "Boston", "boston-r1")


def test_two_teams_cannot_both_sign_the_same_free_agent(league):
    _free_agent("fa-forward")
    sign_free_agent(_engine, "Boston", "fa-forward")
    with pytest.raises(SigningError, match="not a free agent"):
        sign_free_agent(_engine, "Denver", "fa-forward")


def test_a_full_roster_blocks_a_signing(league):
    for i in range(3):
        _free_agent(f"fa-{i}")
    sign_free_agent(_engine, "Boston", "fa-0")            # 20/21
    sign_free_agent(_engine, "Boston", "fa-1")            # 21/21
    with pytest.raises(SigningError, match="roster is full"):
        sign_free_agent(_engine, "Boston", "fa-2")


def test_unknown_team_or_player_is_reported(league):
    _free_agent("fa-forward")
    with pytest.raises(SigningError, match="no team"):
        sign_free_agent(_engine, "Nowhere", "fa-forward")
    with pytest.raises(SigningError, match="no player"):
        sign_free_agent(_engine, "Boston", "nobody")


def test_a_retired_player_cannot_be_signed(league):
    _free_agent("fa-forward")
    with _engine.begin() as c:
        c.execute(text("update players set retired = true where legacy_id = 'fa-forward'"))
    with pytest.raises(SigningError, match="retired"):
        sign_free_agent(_engine, "Boston", "fa-forward")


# -- signing into an incomplete roster --------------------------------------
def test_a_signing_into_a_roster_that_cannot_be_arranged_still_goes_through(league):
    """Mid-offseason a team can be short a position (retirements, expiries) -- signing
    is how it fills back up, so an unarrangeable lineup must not reject the signing.
    The new player just stays unplaced until the roster is whole again."""
    with _engine.begin() as c:                       # Boston loses a goalie: 1 < 2 needed
        c.execute(text("delete from players where legacy_id = 'boston-g2'"))

    _free_agent("fa-forward")                        # signing a forward doesn't fix that
    result = sign_free_agent(_engine, "Boston", "fa-forward")

    assert result["placed"] is False
    row = _row("fa-forward")
    assert row["team_slug"] == "Boston" and row["slot_group"] is None
    assert row["contract_term"] == FREE_AGENT_CONTRACT_YEARS   # the contract is real regardless


# -- the cap report (what the UI reads) --------------------------------------
def test_team_cap_report_numbers(league):
    _set_contract("boston-d1", 40)
    _set_contract("boston-f1", 30)

    report = team_cap_report(_engine, "Boston")

    assert report["payroll"] == 70
    assert report["cap_room"] == SALARY_CAP - 70
    assert report["over_cap"] is False
    assert (report["roster_size"], report["max_roster"]) == (19, DEFAULT_RULES.max_roster)
    assert report["roster_spots"] == DEFAULT_RULES.max_roster - 19
    assert report["max_own_offer"] >= report["max_outside_offer"]
    assert report["limits"]["hard_cap"] == HARD_CAP


def test_team_cap_report_tracks_a_signing(league):
    _free_agent("fa-forward")
    before = team_cap_report(_engine, "Boston")
    sign_free_agent(_engine, "Boston", "fa-forward")
    after = team_cap_report(_engine, "Boston")

    assert after["payroll"] == before["payroll"]          # a $0 deal moves no money
    assert after["roster_size"] == before["roster_size"] + 1
    assert after["roster_spots"] == before["roster_spots"] - 1
