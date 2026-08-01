"""
The offseason market's write path (handball/free_agency.py) against the dev Postgres:
a whole period, end to end -- open, sealed offers, close, restricted resolution,
sequential bidding, deadlock, repeat rounds, close. The rules themselves are covered
DB-free in tests/test_free_agency_rules.py.

Skips when Postgres is unavailable; local DB only (truncates).
"""
import pytest
from sqlalchemy import text

from handball import free_agency as fa
from handball import offseason, season_readiness
from handball import signing_service as signing
from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.pg_repository import PostgresTeamRepository

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from fa_periods limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(
    not _PG_OK, reason="Postgres dev DB not available/migrated (needs alembic 0011)")

_TABLES = ("teams players injuries awards games player_game_lines draft_picks managers "
           "trades trade_assets fa_periods fa_rounds fa_auctions fa_offers "
           "fa_auction_seats fa_actions")
_SEASON = 2027


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield


def _team(team_id: str) -> Team:
    """A legal 19-player roster: 2 open spots, $0 payroll."""
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
    for slug in ("Boston", "Denver", "Austin"):
        repo.save(_team(slug))
    return repo


def _free_agent(legacy_id: str, position: str = "Forward", *,
                rights: str | None = None, restricted: bool = False) -> None:
    with _engine.begin() as c:
        c.execute(
            text("insert into players (legacy_id, name, position, age, years_in_league, "
                 "offense, defense, goalie_skill, max_offense, max_defense, max_goalie_skill, "
                 "variance, peak_age, decline_age, decline_rate, is_injured, contract_term, "
                 "contract_value, years_remaining, amount_paid, rookie_contract, "
                 "restricted_free_agent, retired, rights_team_id) "
                 "values (:lid, :lid, cast(:pos as player_position), 26, 4, 6.0, 6.0, 0.1, "
                 "9.0, 9.0, 0.1, 0.5, 27, 30, 0.15, false, 0, 0, 0, 0, false, :restricted, "
                 "false, (select id from teams where slug = :rights))"),
            {"lid": legacy_id, "pos": position, "restricted": restricted, "rights": rights},
        )


def _payroll(slug: str) -> int:
    return signing.team_cap_report(_engine, slug)["payroll"]


def _set_contract(legacy_id: str, value: int) -> None:
    with _engine.begin() as c:
        c.execute(text("update players set contract_value = :v where legacy_id = :lid"),
                  {"v": value, "lid": legacy_id})


def _player(legacy_id: str) -> dict:
    with _engine.connect() as c:
        return dict(c.execute(
            text("select p.contract_term, p.contract_value, p.years_remaining, "
                 "p.restricted_free_agent, p.rights_team_id, t.slug as team "
                 "from players p left join teams t on t.id = p.team_id "
                 "where p.legacy_id = :lid"),
            {"lid": legacy_id},
        ).mappings().one())


def _auction_of(player_id: str) -> dict:
    with _engine.connect() as c:
        return dict(c.execute(
            text("select a.id, a.status, a.outcome, a.turn_team_id::text as turn_team_id, "
                 "a.no_raise_streak, t.slug as turn_team "
                 "from fa_auctions a join players p on p.id = a.player_id "
                 "left join teams t on t.id = a.turn_team_id "
                 "where p.legacy_id = :lid order by a.id desc limit 1"),
            {"lid": player_id},
        ).mappings().one())


def _seats(auction_id: int) -> list[tuple[str, str]]:
    with _engine.connect() as c:
        return [(r[0], r[1]) for r in c.execute(
            text("select t.slug, s.state from fa_auction_seats s "
                 "join teams t on t.id = s.team_id where s.auction_id = :a "
                 "order by s.turn_order"),
            {"a": auction_id},
        ).all()]


def _open(season: int = _SEASON):
    return fa.open_period(_engine, season)


# -- period and round lifecycle ---------------------------------------------
def test_opening_a_period_starts_round_one(league):
    period = _open()
    assert period["round_number"] == 1 and period["status"] == "offers"
    state = fa.free_agency_state(_engine)
    assert state["period"]["season"] == _SEASON
    assert state["round"]["round_number"] == 1


def test_only_one_period_may_be_open(league):
    _open()
    with pytest.raises(fa.FreeAgencyError, match="already open"):
        _open()


def test_actions_need_an_open_period(league):
    _free_agent("fa-1")
    with pytest.raises(fa.FreeAgencyError, match="no free-agency period"):
        fa.submit_offer(_engine, "Boston", "fa-1", 3, 10)


# -- offers ------------------------------------------------------------------
def test_an_offer_is_recorded_and_can_be_edited_and_withdrawn(league):
    _open()
    _free_agent("fa-1")

    first = fa.submit_offer(_engine, "Boston", "fa-1", 3, 10)
    assert first["replaced"] is False

    second = fa.submit_offer(_engine, "Boston", "fa-1", 4, 12)
    assert second["replaced"] is True                     # supersedes, not edits in place
    live = fa.team_offers(_engine, "Boston")
    assert len(live) == 1 and (live[0]["term"], live[0]["value"]) == (4, 12)

    fa.withdraw_offer(_engine, "Boston", "fa-1")
    assert fa.team_offers(_engine, "Boston") == []


def test_offers_are_append_only(league):
    """The superseded row survives, so the audit trail shows what was offered when."""
    _open()
    _free_agent("fa-1")
    fa.submit_offer(_engine, "Boston", "fa-1", 3, 10)
    fa.submit_offer(_engine, "Boston", "fa-1", 3, 20)
    with _engine.connect() as c:
        rows = c.execute(
            text("select value, status from fa_offers order by id")).all()
    assert [(r[0], r[1]) for r in rows] == [(10, "superseded"), (20, "open")]


def test_a_team_may_not_promise_more_bodies_than_it_has_spots(league):
    _open()
    for i in range(3):
        _free_agent(f"fa-{i}")
    fa.submit_offer(_engine, "Boston", "fa-0", 1, 1)       # 19 rostered, 2 spots
    fa.submit_offer(_engine, "Boston", "fa-1", 1, 1)
    with pytest.raises(fa.OfferError, match="roster spot"):
        fa.submit_offer(_engine, "Boston", "fa-2", 1, 1)


def test_a_team_may_not_promise_more_money_than_it_has(league):
    _set_contract("boston-d1", 100)                        # $60M of outside room
    _open()
    _free_agent("fa-0")
    _free_agent("fa-1")
    fa.submit_offer(_engine, "Boston", "fa-0", 3, 45)
    with pytest.raises(fa.OfferError, match="outside free agents"):
        fa.submit_offer(_engine, "Boston", "fa-1", 3, 45)  # 90 > 60


def test_a_manager_may_back_only_one_of_their_teams_per_player(league):
    """Two teams under one owner bidding on the same player is a free option: park the
    second team, drive a rival up, forfeit at no cost."""
    with _engine.begin() as c:
        owner = c.execute(
            text("insert into managers (user_id, role) values (gen_random_uuid(), 'manager') "
                 "returning user_id")).scalar_one()
        c.execute(text("update teams set owner_id = :o where slug in ('Boston','Denver')"),
                  {"o": owner})
    _open()
    _free_agent("fa-1")
    fa.submit_offer(_engine, "Boston", "fa-1", 3, 10)
    with pytest.raises(fa.OfferError, match="only one of their teams"):
        fa.submit_offer(_engine, "Denver", "fa-1", 3, 20)


def test_an_offer_to_a_rostered_player_is_refused(league):
    _open()
    with pytest.raises(fa.OfferError, match="not a free agent"):
        fa.submit_offer(_engine, "Denver", "boston-r1", 3, 10)


# -- closing the round -------------------------------------------------------
def test_a_sole_offer_signs_when_the_round_closes(league):
    _open()
    _free_agent("fa-1")
    fa.submit_offer(_engine, "Boston", "fa-1", 3, 12)

    summary = fa.close_offer_round(_engine)

    assert summary["signed"] == 1 and summary["offers"] == 1
    row = _player("fa-1")
    assert row["team"] == "Boston"
    assert (row["contract_term"], row["contract_value"], row["years_remaining"]) == (3, 12, 3)
    assert row["rights_team_id"] is None                   # rights consumed by signing
    assert _payroll("Boston") == 12
    assert summary["round_complete"] is True               # nothing left to resolve


def test_two_offers_open_a_bidding_board_worst_first(league):
    _open()
    _free_agent("fa-1")
    fa.submit_offer(_engine, "Boston", "fa-1", 3, 20)
    fa.submit_offer(_engine, "Denver", "fa-1", 3, 10)
    fa.submit_offer(_engine, "Austin", "fa-1", 3, 15)

    fa.close_offer_round(_engine)

    board = _auction_of("fa-1")
    assert board["status"] == "bidding"
    assert board["turn_team"] == "Denver"                  # the worst offer acts first
    assert [s[0] for s in _seats(board["id"])] == ["Denver", "Austin", "Boston"]


def test_a_player_nobody_offered_on_is_simply_left_unsigned(league):
    _open()
    _free_agent("fa-1")
    summary = fa.close_offer_round(_engine)
    assert summary == {**summary, "signed": 0, "offers": 0}
    assert _player("fa-1")["team"] is None


def test_closing_twice_is_refused(league):
    _open()
    fa.close_offer_round(_engine)
    with pytest.raises(fa.FreeAgencyError, match="not taking offers"):
        fa.close_offer_round(_engine)


# -- restricted free agency --------------------------------------------------
def test_a_restricted_player_whose_own_team_leads_simply_stays(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.submit_offer(_engine, "Boston", "bos-rfa", 3, 20)
    fa.submit_offer(_engine, "Denver", "bos-rfa", 3, 10)

    fa.close_offer_round(_engine)

    assert _player("bos-rfa")["team"] == "Boston"
    assert _auction_of("bos-rfa")["outcome"] == "rfa_kept"


def test_the_rights_team_may_match_an_outside_offer_and_keep_the_player(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.submit_offer(_engine, "Denver", "bos-rfa", 4, 25)
    fa.close_offer_round(_engine)

    board = _auction_of("bos-rfa")
    assert board["status"] == "matching"

    result = fa.match_offer(_engine, board["id"], "Boston")

    assert (result["term"], result["value"]) == (4, 25)    # the offer sheet, exactly
    row = _player("bos-rfa")
    assert row["team"] == "Boston" and row["contract_value"] == 25
    assert _auction_of("bos-rfa")["outcome"] == "rfa_matched"


def test_only_the_rights_team_may_match(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.submit_offer(_engine, "Denver", "bos-rfa", 4, 25)
    fa.close_offer_round(_engine)
    board = _auction_of("bos-rfa")
    with pytest.raises(fa.FreeAgencyError, match="rights"):
        fa.match_offer(_engine, board["id"], "Austin")


def test_declining_the_match_hands_the_player_to_the_top_bidder(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.submit_offer(_engine, "Denver", "bos-rfa", 4, 25)
    fa.close_offer_round(_engine)

    fa.decline_match(_engine, _auction_of("bos-rfa")["id"], "Boston")

    assert _player("bos-rfa")["team"] == "Denver"
    assert _auction_of("bos-rfa")["outcome"] == "sole_offer"


def test_declining_with_several_offers_falls_through_to_bidding(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.submit_offer(_engine, "Boston", "bos-rfa", 3, 10)
    fa.submit_offer(_engine, "Denver", "bos-rfa", 3, 25)
    fa.submit_offer(_engine, "Austin", "bos-rfa", 3, 20)
    fa.close_offer_round(_engine)

    fa.decline_match(_engine, _auction_of("bos-rfa")["id"], "Boston")

    board = _auction_of("bos-rfa")
    assert board["status"] == "bidding"
    # the rights team keeps its own seat: its lower offer is still on the table
    assert [s[0] for s in _seats(board["id"])] == ["Boston", "Austin", "Denver"]


def test_a_restricted_player_with_no_offers_is_not_special(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.close_offer_round(_engine)
    assert _player("bos-rfa")["team"] is None


# -- sequential bidding ------------------------------------------------------
@pytest.fixture
def board(league):
    """A live three-team board on fa-1: Denver $10M, Austin $15M, Boston $20M."""
    _open()
    _free_agent("fa-1")
    fa.submit_offer(_engine, "Boston", "fa-1", 3, 20)
    fa.submit_offer(_engine, "Denver", "fa-1", 3, 10)
    fa.submit_offer(_engine, "Austin", "fa-1", 3, 15)
    fa.close_offer_round(_engine)
    return _auction_of("fa-1")["id"]


def test_bidding_out_of_turn_is_refused(board):
    with pytest.raises(fa.BidError, match="not your turn"):
        fa.place_bid(_engine, board, "Austin", fa.BID_MATCH)


def test_a_match_keeps_you_in_and_passes_the_turn(board):
    fa.place_bid(_engine, board, "Denver", fa.BID_MATCH)
    row = _auction_of("fa-1")
    assert row["turn_team"] == "Austin" and row["no_raise_streak"] == 1
    assert [o["value"] for o in fa.team_offers(_engine, "Denver")] == [20]


def test_a_raise_must_beat_the_leader(board):
    with pytest.raises(fa.BidError, match="does not beat"):
        fa.place_bid(_engine, board, "Denver", fa.BID_RAISE, term=3, value=20)


def test_a_raise_takes_the_lead_and_resets_the_streak(board):
    fa.place_bid(_engine, board, "Denver", fa.BID_RAISE, term=3, value=25)
    row = _auction_of("fa-1")
    assert row["no_raise_streak"] == 0 and row["turn_team"] == "Austin"


def test_the_last_team_standing_signs_at_its_own_offer(board):
    fa.place_bid(_engine, board, "Denver", fa.BID_FORFEIT)
    fa.place_bid(_engine, board, "Austin", fa.BID_FORFEIT)

    assert _auction_of("fa-1")["outcome"] == "bid_won"
    row = _player("fa-1")
    assert row["team"] == "Boston" and row["contract_value"] == 20


def test_forfeiting_releases_the_promise(board):
    fa.place_bid(_engine, board, "Denver", fa.BID_FORFEIT)
    assert fa.team_offers(_engine, "Denver") == []
    assert [s for s in _seats(board) if s[0] == "Denver"] == [("Denver", "forfeited")]


def test_a_full_no_raise_cycle_waits_for_the_commissioner(board):
    fa.place_bid(_engine, board, "Denver", fa.BID_MATCH)
    fa.place_bid(_engine, board, "Austin", fa.BID_MATCH)
    fa.place_bid(_engine, board, "Boston", fa.BID_MATCH)      # the leader stands pat

    row = _auction_of("fa-1")
    assert row["status"] == "awaiting_award" and row["turn_team"] is None
    with pytest.raises(fa.FreeAgencyError, match="not open for bidding"):
        fa.place_bid(_engine, board, "Denver", fa.BID_MATCH)


def test_the_commissioner_awards_a_deadlocked_board(board):
    for team in ("Denver", "Austin", "Boston"):
        fa.place_bid(_engine, board, team, fa.BID_MATCH)

    fa.award_auction(_engine, board, "Austin", reason="coin toss")

    row = _player("fa-1")
    assert row["team"] == "Austin" and row["contract_value"] == 20
    assert _auction_of("fa-1")["outcome"] == "commissioner_award"


def test_an_award_needs_a_deadlock(board):
    with pytest.raises(fa.FreeAgencyError, match="not deadlocked"):
        fa.award_auction(_engine, board, "Austin")


def test_the_commissioner_can_force_a_stalling_team_to_forfeit(board):
    fa.force_forfeit(_engine, board, "Denver", reason="a week with no answer")
    row = _auction_of("fa-1")
    assert row["turn_team"] == "Austin"
    assert [s for s in _seats(board) if s[0] == "Denver"] == [("Denver", "forfeited")]


def test_force_forfeit_on_an_rfa_window_declines_it(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.submit_offer(_engine, "Denver", "bos-rfa", 4, 25)
    fa.close_offer_round(_engine)

    fa.force_forfeit(_engine, _auction_of("bos-rfa")["id"], "Boston")

    assert _player("bos-rfa")["team"] == "Denver"


# -- repeat rounds and closing ----------------------------------------------
def test_a_second_round_opens_once_every_board_is_resolved(league):
    _open()
    _free_agent("fa-1")
    _free_agent("fa-2")
    fa.submit_offer(_engine, "Boston", "fa-1", 1, 5)
    fa.submit_offer(_engine, "Boston", "fa-2", 1, 5)
    fa.close_offer_round(_engine)

    second = fa.open_next_round(_engine)

    assert second["round_number"] == 2
    # ...and a player who signed in round 1 is off the market
    with pytest.raises(fa.OfferError, match="not a free agent"):
        fa.submit_offer(_engine, "Denver", "fa-1", 1, 5)


def test_a_new_round_is_refused_while_a_board_is_live(board):
    with pytest.raises(fa.FreeAgencyError, match="still live"):
        fa.open_next_round(_engine)


def test_a_round_that_drew_nothing_ends_free_agency(league):
    _open()
    _free_agent("fa-1")
    fa.close_offer_round(_engine)
    with pytest.raises(fa.FreeAgencyError, match="drew no offers"):
        fa.open_next_round(_engine)


def test_closing_the_period_is_refused_while_a_board_is_live(board):
    with pytest.raises(fa.FreeAgencyError, match="still live"):
        fa.close_period(_engine)


def test_closing_the_period_unrestricts_everyone_still_unsigned(league):
    _open()
    _free_agent("bos-rfa", rights="Boston", restricted=True)
    fa.close_offer_round(_engine)

    result = fa.close_period(_engine)

    assert result["unsigned_unrestricted"] == 1
    row = _player("bos-rfa")
    assert row["restricted_free_agent"] is False           # their team had its window
    assert row["rights_team_id"] is not None               # Bird rights are kept


# -- how free agency interacts with everything else --------------------------
def test_the_pool_is_closed_while_the_market_is_open(league):
    _free_agent("fa-1")
    _open()
    with pytest.raises(signing.SigningError, match="free agency is open"):
        signing.sign_free_agent(_engine, "Boston", "fa-1")

    fa.close_offer_round(_engine)
    fa.close_period(_engine)
    signing.sign_free_agent(_engine, "Boston", "fa-1")     # works again
    assert _player("fa-1")["team"] == "Boston"


def test_retiring_a_player_voids_their_live_auction(board):
    offseason.retire_players(_engine, ["fa-1"], _SEASON)

    row = _auction_of("fa-1")
    assert row["status"] == "void" and row["outcome"] == "player_ineligible"
    # every bidder's promise is released
    assert fa.team_offers(_engine, "Denver") == []
    assert fa.team_offers(_engine, "Boston") == []


def test_an_open_market_blocks_the_season_from_starting(league):
    _open()
    report = season_readiness.readiness_report(_engine, _SEASON)
    assert report["ready"] is False
    assert any(b["check"] == "free_agency_open" for b in report["blockers"])

    fa.close_offer_round(_engine)
    fa.close_period(_engine)
    assert season_readiness.readiness_report(_engine, _SEASON)["ready"] is True


def test_every_action_is_logged_for_the_commissioner(board):
    fa.force_forfeit(_engine, board, "Denver", reason="no response")
    with _engine.connect() as c:
        rows = c.execute(
            text("select action, by_commissioner from fa_actions order by id")).all()
    actions = [r[0] for r in rows]
    assert "period_opened" in actions and "round_closed" in actions
    assert ("bid_forfeit", True) in [(r[0], r[1]) for r in rows]
