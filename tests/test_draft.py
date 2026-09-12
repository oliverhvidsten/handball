"""
The draft's write path (handball/draft.py) and its endpoints (api/draft.py) against
the dev Postgres: a rollover that seeds the order off a real bracket, a lottery that
numbers round 1 and settles the protections it decides, a whole 64-pick draft run to
completion, the turn clock picking for a manager who walked away, undrafted
prospects landing in the free-agent pool, and the phases that wait on all of it.

The rules themselves are covered DB-free in tests/test_draft_rules.py.

Skips when Postgres is unavailable; local DB only (truncates).
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball import draft, offseason, season_readiness
from handball.db import get_engine, is_local_db
from handball.simulation_vars import DRAFT_ROUNDS, DRAFT_TURN_LIMIT_HOURS

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from draft_state limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(
    not _PG_OK, reason="Postgres dev DB not available/migrated (needs alembic 0014)")

if _PG_OK:
    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = ("teams players injuries awards games player_game_lines draft_picks "
           "managers trades trade_assets fa_periods fa_rounds fa_auctions fa_offers "
           "fa_auction_seats fa_actions playoff_series draft_lotteries draft_state "
           "draft_prospects season_state")

_SEASON = 2031               # the season that just finished
_NEXT = _SEASON + 1          # the draft's season
_TEAMS = 32
_PICKS = _TEAMS * DRAFT_ROUNDS


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield
    if _PG_OK:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fixtures: a 32-team league, a finished bracket, a seeded order.
# ---------------------------------------------------------------------------
def _ranked(n: int = _TEAMS) -> list[str]:
    """Team slugs best->worst. Deliberately not real roster fixtures: the draft
    cares about who holds which pick, not who is on the roster, and 32 legal
    19-player rosters would make every test in this file ten times slower."""
    slugs = [f"Team{i:02d}" for i in range(1, n + 1)]
    with _engine.begin() as c:
        for slug in slugs:
            c.execute(
                text("insert into teams (slug, name, coaches) "
                     "values (:s, :s, cast(array['HC','OC','DC'] as text[]))"),
                {"s": slug},
            )
        c.execute(
            text("insert into season_state (season, injury_seed) values (:s, 1) "
                 "on conflict (season) do nothing"),
            {"s": _NEXT},
        )
    return slugs


def _uuid_of(slug: str) -> str:
    with _engine.connect() as c:
        return str(c.execute(text("select id from teams where slug=:s"), {"s": slug}).scalar_one())


def _play_bracket(ranked: list[str], season: int = _SEASON) -> None:
    """A 32-team postseason where the better-ranked team always wins: the top 16
    make it, paired best-vs-worst each round. Four rounds, 15 series, one champion --
    the shape offseason._seed_draft_order reads to order picks 17-32."""
    ids = {s: _uuid_of(s) for s in ranked}
    alive, rnd = list(ranked[:16]), 1
    with _engine.begin() as c:
        while len(alive) > 1:
            pairs = [(alive[i], alive[len(alive) - 1 - i]) for i in range(len(alive) // 2)]
            for seed, (high, low) in enumerate(pairs, start=1):
                c.execute(
                    text("insert into playoff_series (season, round, conference, label, "
                         "high_seed_team_id, low_seed_team_id, high_seed, low_seed, "
                         "winner_team_id) values (:s, :r, null, :lbl, cast(:h as uuid), "
                         "cast(:l as uuid), :hs, :ls, cast(:h as uuid))"),
                    {"s": season, "r": rnd, "lbl": f"Round {rnd}", "h": ids[high],
                     "l": ids[low], "hs": seed, "ls": len(pairs) * 2 + 1 - seed},
                )
            alive = [high for high, _ in pairs]
            rnd += 1


def _seed(ranked: list[str]) -> None:
    with _engine.begin() as c:
        offseason._seed_draft_order(c, ranked, _NEXT, _SEASON)


@pytest.fixture
def league():
    """A finished season: 32 teams, a played bracket, next season's order seeded."""
    ranked = _ranked()
    _play_bracket(ranked)
    _seed(ranked)
    return ranked


def _names(n: int, prefix: str = "Prospect") -> str:
    return "\n".join(f"{prefix} {i:03d}" for i in range(1, n + 1)) + "\n"


def _picks(season: int = _NEXT) -> list[dict]:
    with _engine.connect() as c:
        return [dict(r) for r in c.execute(
            text("select dp.pick_number, dp.round, dp.used, dp.auto_pick, "
                 "dp.protection_top_n, dp.protection_outcome, "
                 "ht.slug as holder, ot.slug as original "
                 "from draft_picks dp join teams ht on ht.id = dp.holder_team_id "
                 "join teams ot on ot.id = dp.original_team_id "
                 "where dp.season = :s order by dp.pick_number nulls last, dp.round"),
            {"s": season},
        ).mappings().all()]


def _trade_pick(season: int, round_num: int, original: str, holder: str,
                protection: int | None = None) -> None:
    with _engine.begin() as c:
        c.execute(
            text("update draft_picks set holder_team_id = (select id from teams where slug=:h), "
                 "protection_top_n = :p "
                 "where season = :s and round = :r "
                 "and original_team_id = (select id from teams where slug=:o)"),
            {"h": holder, "p": protection, "s": season, "r": round_num, "o": original},
        )


def _as_manager(*slugs: str, role: str = "manager"):
    """Override auth to act as a manager owning the given teams (commissioner when
    none are named). Mirrors tests/test_api.py."""
    owned = [_uuid_of(s) for s in slugs if s]
    user_id = str(uuid.uuid4())
    with _engine.begin() as c:
        c.execute(text("insert into managers (user_id, role) values (cast(:u as uuid), :r)"),
                  {"u": user_id, "r": role})
        for tid in owned:
            c.execute(text("update teams set owner_id = cast(:u as uuid) "
                           "where id = cast(:t as uuid)"),
                      {"u": user_id, "t": tid})
    app.dependency_overrides[get_current_manager] = lambda: Manager(
        user_id=user_id, owned_team_ids=owned, role=role)


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# The order the rollover seeds.
# ---------------------------------------------------------------------------
def test_rollover_seeds_the_lottery_pool_unnumbered_and_the_rest_in_full(league):
    rows = _picks()
    assert len(rows) == _PICKS

    round_one = [r for r in rows if r["round"] == 1]
    unnumbered = [r for r in round_one if r["pick_number"] is None]
    # the 16 teams that missed the playoffs hold picks 1-16 -- the lottery's to give
    assert len(unnumbered) == 16
    assert {r["holder"] for r in unnumbered} == set(league[16:])

    numbered = sorted((r for r in round_one if r["pick_number"] is not None),
                      key=lambda r: r["pick_number"])
    assert [r["pick_number"] for r in numbered] == list(range(17, 33))
    assert numbered[-1]["holder"] == league[0]        # champion picks 32nd
    assert numbered[-2]["holder"] == league[1]        # runner-up 31st
    assert numbered[0]["holder"] == league[15]        # worst first-round loser, 17th

    round_two = sorted((r for r in rows if r["round"] == 2),
                       key=lambda r: r["pick_number"])
    assert [r["pick_number"] for r in round_two] == list(range(33, 65))
    # every non-playoff team picks ahead of every playoff team -- picks 33-48 then 49-64
    assert [r["holder"] for r in round_two[:16]] == list(reversed(league[16:]))
    assert [r["holder"] for r in round_two[16:]] == list(reversed(league[:16]))


def test_rollover_records_the_pool_and_opens_a_pending_draft(league):
    with _engine.connect() as c:
        lot = c.execute(text("select standings_order, results, seed from draft_lotteries "
                             "where season = :s"), {"s": _NEXT}).mappings().one()
        st = c.execute(text("select status, current_overall from draft_state where season = :s"),
                       {"s": _NEXT}).mappings().one()
    assert len(lot["standings_order"]) == 16
    assert lot["results"] is None and lot["seed"] is None
    assert st["status"] == "pending" and st["current_overall"] is None
    # the pool is stored worst-first, because the rollover is about to zero the
    # records the odds depend on
    assert lot["standings_order"][0] == _uuid_of(league[-1])


def test_seeding_preserves_a_pick_traded_years_earlier(league):
    _trade_pick(_NEXT, 1, original=league[-1], holder=league[0])
    _seed(league)                                 # re-run the rollover's seeding
    held = [r for r in _picks() if r["original"] == league[-1] and r["round"] == 1]
    assert held[0]["holder"] == league[0]


def test_a_league_with_no_bracket_still_gets_plain_reverse_standings():
    """Leagues that predate the draft -- and every offline fixture -- must not be
    handed a draft phase they never had."""
    ranked = _ranked(4)
    with _engine.begin() as c:
        offseason._seed_draft_order(c, ranked, _NEXT)
    rows = _picks()
    assert [r["pick_number"] for r in rows] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert rows[0]["holder"] == ranked[-1]
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from draft_state")).scalar_one() == 0
    assert draft.is_complete(_engine, _NEXT) is True      # nothing to wait for


# ---------------------------------------------------------------------------
# The lottery.
# ---------------------------------------------------------------------------
def test_lottery_numbers_round_one_and_is_reproducible(league):
    first = draft.run_lottery(_engine, _NEXT, seed=4242)
    assert [r["slot"] for r in first["results"]] == list(range(1, 17))
    assert draft.status(_engine, _NEXT) == "lottery_drawn"

    numbered = sorted((r for r in _picks() if r["round"] == 1),
                      key=lambda r: r["pick_number"])
    assert [r["pick_number"] for r in numbered] == list(range(1, 33))
    assert {r["holder"] for r in numbered[:16]} == set(league[16:])

    # the seed is stored with the draw, and replaying it gives the same draw
    stored = draft.lottery(_engine, _NEXT)
    assert stored["seed"] == 4242
    assert [r["team"] for r in stored["results"]] == [r["team"] for r in first["results"]]

    _seed(league)                                  # reset the numbers
    with _engine.begin() as c:
        c.execute(text("update draft_lotteries set results = null, seed = null where season=:s"),
                  {"s": _NEXT})
        c.execute(text("update draft_state set status = 'pending' where season=:s"), {"s": _NEXT})
    again = draft.run_lottery(_engine, _NEXT, seed=4242)
    assert [r["team"] for r in again["results"]] == [r["team"] for r in first["results"]]


def test_lottery_without_a_seed_records_the_one_it_used(league):
    result = draft.run_lottery(_engine, _NEXT)
    assert result["seed"] >= 1
    assert draft.lottery(_engine, _NEXT)["seed"] == result["seed"]


def test_lottery_is_refused_once_the_room_has_opened(league):
    draft.run_lottery(_engine, _NEXT, seed=1)
    draft.upload_prospects(_engine, _NEXT, _names(_PICKS))
    draft.open_draft(_engine, _NEXT)
    with pytest.raises(draft.DraftError, match="cannot be redrawn"):
        draft.run_lottery(_engine, _NEXT, seed=2)


def test_lottery_needs_a_pool():
    _ranked(4)
    with pytest.raises(draft.DraftError, match="no draft"):
        draft.run_lottery(_engine, _NEXT, seed=1)


# ---------------------------------------------------------------------------
# Protections.
# ---------------------------------------------------------------------------
def _seed_landing(team: str, predicate) -> tuple[int, int]:
    """Find a lottery seed whose draw puts `team` in a slot satisfying `predicate`,
    by replaying the draw through the same pure function run_lottery uses. Searching
    with the rules rather than by re-running the lottery keeps the test's setup out
    of the state it is about to assert on."""
    import random

    from handball import draft_rules

    with _engine.connect() as c:
        pool = c.execute(text("select standings_order from draft_lotteries where season=:s"),
                         {"s": _NEXT}).scalar_one()
        slugs = {str(tid): slug for tid, slug in
                 c.execute(text("select id, slug from teams")).all()}
    pool_slugs = [slugs[str(t)] for t in pool]
    for seed in range(1, 500):
        slot = draft_rules.draw_lottery(pool_slugs, random.Random(seed)).index(team) + 1
        if predicate(slot):
            return seed, slot
    pytest.fail(f"no seed in 500 put {team} in a qualifying slot")   # pragma: no cover


def test_a_protected_pick_reverts_when_the_draw_catches_it(league):
    """Top-5 protected, and the lottery hands the original team a top-5 slot: the
    pick goes home and the obligation ends -- no rollover to a later year."""
    worst = league[-1]                       # the best lottery odds in the league
    _trade_pick(_NEXT, 1, original=worst, holder=league[0], protection=5)
    seed, slot = _seed_landing(worst, lambda n: n <= 5)

    result = draft.run_lottery(_engine, _NEXT, seed=seed)

    row = next(r for r in _picks() if r["original"] == worst and r["round"] == 1)
    assert row["pick_number"] == slot
    assert row["holder"] == worst                       # reverted
    assert row["protection_outcome"] == "reverted"
    assert result["protections"] == [{
        "pick_number": slot, "protection_top_n": 5, "outcome": "reverted",
        "original_team": worst, "holder_team": league[0]}]


def test_a_protected_pick_conveys_when_the_draw_misses_it(league):
    worst = league[-1]
    _trade_pick(_NEXT, 1, original=worst, holder=league[0], protection=1)
    seed, slot = _seed_landing(worst, lambda n: n > 1)

    result = draft.run_lottery(_engine, _NEXT, seed=seed)

    row = next(r for r in _picks() if r["original"] == worst and r["round"] == 1)
    assert row["pick_number"] == slot
    assert row["holder"] == league[0]                   # conveyed, and stays traded
    assert row["protection_outcome"] == "conveyed"
    assert result["protections"][0]["outcome"] == "conveyed"


def test_a_protection_is_resolved_once_and_not_again(league):
    worst = league[-1]
    _trade_pick(_NEXT, 1, original=worst, holder=league[0], protection=5)
    seed, _ = _seed_landing(worst, lambda n: n <= 5)
    draft.run_lottery(_engine, _NEXT, seed=seed)

    # a redraw (the room has not opened) must not re-resolve a settled protection:
    # the pick is back with its original team and has no condition left on it.
    again = draft.run_lottery(_engine, _NEXT, seed=seed + 1)
    assert again["protections"] == []
    row = next(r for r in _picks() if r["original"] == worst and r["round"] == 1)
    assert row["holder"] == worst and row["protection_outcome"] == "reverted"


def test_an_unprotected_traded_pick_is_left_alone(league):
    _trade_pick(_NEXT, 1, original=league[-1], holder=league[0])
    result = draft.run_lottery(_engine, _NEXT, seed=11)
    assert result["protections"] == []
    row = next(r for r in _picks() if r["original"] == league[-1] and r["round"] == 1)
    assert row["holder"] == league[0] and row["protection_outcome"] is None


# ---------------------------------------------------------------------------
# Prospects.
# ---------------------------------------------------------------------------
def test_prospects_are_generated_once_at_upload(league):
    draft.run_lottery(_engine, _NEXT, seed=1)
    out = draft.upload_prospects(_engine, _NEXT, _names(70))
    assert out == {"season": _NEXT, "prospects": 70, "picks": _PICKS, "enough": True}

    board = draft.prospects(_engine, _NEXT)
    assert len(board) == 70
    assert board == sorted(board, key=lambda p: (-p["rating"], p["ord"]))
    # the whole generated player is kept, so the board cannot show ratings the
    # signed player will not have
    with _engine.connect() as c:
        blob = c.execute(text("select player_json from draft_prospects where season=:s and ord=1"),
                         {"s": _NEXT}).scalar_one()
    top = next(p for p in board if p["ord"] == 1)
    assert blob["offense"] == pytest.approx(top["offense"])
    assert blob["id"].startswith(f"draft-{_NEXT}-1-")


def test_a_csv_with_positions_is_read_the_same_way_the_simulator_reads_it(league):
    draft.run_lottery(_engine, _NEXT, seed=1)
    csv = "Name,Position\nAmos Reed,Goalie\nKit Vance,Defense\n"
    draft.upload_prospects(_engine, _NEXT, csv, filename="class.csv")
    board = {p["name"]: p["position"] for p in draft.prospects(_engine, _NEXT)}
    assert board == {"Amos Reed": "Goalie", "Kit Vance": "Defense"}


def test_uploading_again_replaces_the_board(league):
    draft.run_lottery(_engine, _NEXT, seed=1)
    draft.upload_prospects(_engine, _NEXT, _names(70))
    draft.upload_prospects(_engine, _NEXT, _names(64, prefix="Rookie"))
    board = draft.prospects(_engine, _NEXT)
    assert len(board) == 64
    assert all(p["name"].startswith("Rookie") for p in board)


def test_a_bad_position_column_names_the_row(league):
    draft.run_lottery(_engine, _NEXT, seed=1)
    with pytest.raises(draft.DraftError, match="row 2"):
        draft.upload_prospects(_engine, _NEXT,
                               "Name,Position\nA,Forward\nB,Winger\n", filename="c.csv")


def test_an_empty_file_is_refused(league):
    draft.run_lottery(_engine, _NEXT, seed=1)
    with pytest.raises(draft.DraftError, match="no names"):
        draft.upload_prospects(_engine, _NEXT, "\n\n  \n")


# ---------------------------------------------------------------------------
# Opening the room.
# ---------------------------------------------------------------------------
def test_the_room_needs_a_lottery_and_a_full_board(league):
    with pytest.raises(draft.DraftError, match="draw the"):
        draft.open_draft(_engine, _NEXT)

    draft.run_lottery(_engine, _NEXT, seed=1)
    with pytest.raises(draft.DraftError, match="upload at least"):
        draft.open_draft(_engine, _NEXT)

    draft.upload_prospects(_engine, _NEXT, _names(_PICKS - 1))
    with pytest.raises(draft.DraftError, match=f"{_PICKS - 1} prospect"):
        draft.open_draft(_engine, _NEXT)

    draft.upload_prospects(_engine, _NEXT, _names(_PICKS))
    opened = draft.open_draft(_engine, _NEXT)
    assert opened["status"] == "open"
    assert opened["on_the_clock"]["pick_number"] == 1
    with pytest.raises(draft.DraftError, match="already open"):
        draft.open_draft(_engine, _NEXT)


def test_prospects_cannot_be_replaced_once_the_room_is_open(league):
    _open_room(league)
    with pytest.raises(draft.DraftError, match="cannot be replaced"):
        draft.upload_prospects(_engine, _NEXT, _names(_PICKS))


def _open_room(league, *, prospects: int = _PICKS + 6, seed: int = 77):
    draft.run_lottery(_engine, _NEXT, seed=seed)
    draft.upload_prospects(_engine, _NEXT, _names(prospects))
    return draft.open_draft(_engine, _NEXT)


# ---------------------------------------------------------------------------
# Picking.
# ---------------------------------------------------------------------------
def test_a_whole_draft_runs_to_completion_and_prices_every_pick(league):
    _open_room(league)
    made = []
    for expected in range(1, _PICKS + 1):
        clock = draft.on_the_clock(_engine, _NEXT)
        assert clock["pick_number"] == expected
        made.append(draft.make_pick(_engine, _NEXT, team_slug=clock["team"]))

    assert [m["overall"] for m in made] == list(range(1, _PICKS + 1))
    assert made[-1]["complete"] is True
    assert draft.status(_engine, _NEXT) == "complete"
    assert all(not m["auto_pick"] for m in made)

    # every pick priced off the rookie scale
    assert (made[0]["term"], made[0]["value"]) == (5, 5)
    assert (made[10]["term"], made[10]["value"]) == (5, 4)
    assert (made[32]["term"], made[32]["value"]) == (2, 2)
    assert (made[63]["term"], made[63]["value"]) == (2, 1)

    # the board was taken best-first, so each pick is no better than the one before
    with _engine.connect() as c:
        ratings = [float(r[0]) for r in c.execute(
            text("select dp.offense + dp.defense + dp.goalie_skill from draft_prospects dp "
                 "join draft_picks p on p.player_id = dp.player_id "
                 "where p.season = :s order by p.pick_number"), {"s": _NEXT}).all()]
    assert ratings == sorted(ratings, reverse=True)

    rows = _picks()
    assert all(r["used"] for r in rows)
    with _engine.connect() as c:
        # each draftee is a real player on the holder's roster, on a rookie deal
        n = c.execute(text(
            "select count(*) from players p join draft_picks d on d.player_id = p.id "
            "where d.season = :s and p.team_id = d.holder_team_id "
            "and p.rookie_contract and p.retired = false"), {"s": _NEXT}).scalar_one()
        assert n == _PICKS


def test_undrafted_prospects_become_unrestricted_free_agents(league):
    _open_room(league, prospects=_PICKS + 6)
    for _ in range(_PICKS):
        draft.make_pick(_engine, _NEXT)
    assert draft.status(_engine, _NEXT) == "complete"

    with _engine.connect() as c:
        pool = c.execute(text(
            "select p.legacy_id, p.restricted_free_agent, p.contract_term, "
            "p.years_remaining, p.rights_team_id from players p "
            "join draft_prospects dp on dp.player_id = p.id "
            "where dp.season = :s and p.team_id is null"), {"s": _NEXT}).mappings().all()
    assert len(pool) == 6
    assert all(not r["restricted_free_agent"] for r in pool)
    assert all(r["contract_term"] == 0 and r["years_remaining"] == 0 for r in pool)
    assert all(r["rights_team_id"] is None for r in pool)     # nobody ever held them

    # and no prospect is left unaccounted for
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from draft_prospects "
                              "where season = :s and player_id is null"),
                         {"s": _NEXT}).scalar_one() == 0


def test_a_pick_is_refused_out_of_turn_and_after_the_draft(league):
    _open_room(league)
    clock = draft.on_the_clock(_engine, _NEXT)
    other = next(s for s in league if s != clock["team"])
    with pytest.raises(draft.DraftError, match="is on the clock"):
        draft.make_pick(_engine, _NEXT, team_slug=other)

    # the turn did not move
    assert draft.on_the_clock(_engine, _NEXT)["pick_number"] == 1

    for _ in range(_PICKS):
        draft.make_pick(_engine, _NEXT)
    with pytest.raises(draft.DraftError, match="not open"):
        draft.make_pick(_engine, _NEXT)


def test_a_prospect_cannot_be_drafted_twice(league):
    _open_room(league)
    board = draft.prospects(_engine, _NEXT)
    draft.make_pick(_engine, _NEXT, board[0]["id"])
    with pytest.raises(draft.DraftError, match="already been drafted"):
        draft.make_pick(_engine, _NEXT, board[0]["id"])


def test_a_named_pick_takes_that_player_not_the_best_one(league):
    _open_room(league)
    board = draft.prospects(_engine, _NEXT)
    reach = board[-1]                       # the worst prospect on the board
    result = draft.make_pick(_engine, _NEXT, reach["id"])
    assert result["player_name"] == reach["name"]
    assert draft.prospects(_engine, _NEXT)[0]["name"] == board[0]["name"]


def test_the_draft_is_not_complete_until_it_is(league):
    assert draft.is_complete(_engine, _NEXT) is False
    _open_room(league)
    assert draft.is_complete(_engine, _NEXT) is False
    for _ in range(_PICKS):
        draft.make_pick(_engine, _NEXT)
    assert draft.is_complete(_engine, _NEXT) is True
    draft.assert_complete(_engine, _NEXT)        # does not raise


# ---------------------------------------------------------------------------
# The turn clock.
# ---------------------------------------------------------------------------
def _age_the_turn(hours: int) -> None:
    with _engine.begin() as c:
        c.execute(text("update draft_state set turn_started_at = "
                       "now() - make_interval(hours => :h) where season = :s"),
                  {"h": hours, "s": _NEXT})


def test_the_clock_picks_for_a_manager_who_walked_away(league):
    _open_room(league)
    on_clock = draft.on_the_clock(_engine, _NEXT)
    best = draft.prospects(_engine, _NEXT)[0]

    # a turn inside the limit is not swept
    _age_the_turn(DRAFT_TURN_LIMIT_HOURS - 1)
    assert draft.sweep_expired_turns(_engine, _NEXT) == []

    _age_the_turn(DRAFT_TURN_LIMIT_HOURS + 1)
    swept = draft.sweep_expired_turns(_engine, _NEXT)
    assert len(swept) == 1
    assert swept[0]["team"] == on_clock["team"]
    assert swept[0]["player_name"] == best["name"]      # best available, by the rules

    row = next(r for r in _picks() if r["pick_number"] == 1)
    assert row["used"] and row["auto_pick"] is True
    # the clock starts fresh for the next team rather than expiring them too
    assert draft.sweep_expired_turns(_engine, _NEXT) == []
    assert draft.on_the_clock(_engine, _NEXT)["pick_number"] == 2


def test_a_long_absence_still_only_costs_the_absentee_their_own_turn(league):
    """An auto-pick starts the NEXT team's clock fresh, so a draft left alone for
    days catches up one pick per read rather than burning through the order: the team
    behind an absentee does not inherit their lateness."""
    _open_room(league)
    _age_the_turn(DRAFT_TURN_LIMIT_HOURS * 5)
    assert len(draft.sweep_expired_turns(_engine, _NEXT)) == 1
    assert draft.sweep_expired_turns(_engine, _NEXT) == []      # team 2 has a full turn
    assert draft.on_the_clock(_engine, _NEXT)["pick_number"] == 2

    _age_the_turn(DRAFT_TURN_LIMIT_HOURS + 1)
    assert len(draft.sweep_expired_turns(_engine, _NEXT)) == 1
    assert draft.on_the_clock(_engine, _NEXT)["pick_number"] == 3


def test_the_state_read_is_the_clock(league):
    _open_room(league)
    _age_the_turn(DRAFT_TURN_LIMIT_HOURS + 2)
    state = draft.draft_state(_engine, _NEXT)
    assert len(state["swept"]) == 1
    assert state["picks_made"] == 1
    assert state["auto_picks"] == 1
    assert state["current_overall"] == 2
    assert state["on_the_clock"]["overall"] == 2
    assert 0 < state["on_the_clock"]["turn_seconds_left"] <= DRAFT_TURN_LIMIT_HOURS * 3600
    assert state["turn_limit_hours"] == DRAFT_TURN_LIMIT_HOURS


def test_the_state_read_serves_the_whole_room(league):
    state = draft.draft_state(_engine, _NEXT)
    assert state["status"] == "pending"
    assert state["picks"] == _PICKS and state["picks_made"] == 0
    assert state["on_the_clock"] is None
    assert state["lottery"]["results"] == []

    _open_room(league)
    draft.make_pick(_engine, _NEXT)
    state = draft.draft_state(_engine, _NEXT)
    assert state["status"] == "open"
    assert len(state["order"]) == _PICKS
    assert state["order"][0]["player_name"] is not None
    assert state["order"][0]["term"] == 5 and state["order"][0]["value"] == 5
    assert len(state["board"]) == _PICKS + 6 - 1
    assert len(state["lottery"]["results"]) == 16


def test_a_season_with_no_draft_reads_as_an_empty_room():
    _ranked(4)
    state = draft.draft_state(_engine, _NEXT)
    assert state["status"] is None and state["order"] == []


# ---------------------------------------------------------------------------
# The phases that wait on the draft.
# ---------------------------------------------------------------------------
def test_free_agency_will_not_open_until_the_draft_is_done(league, client):
    _as_manager(role="commissioner")
    r = client.post("/free-agency/periods")
    assert r.status_code == 409
    assert "draft is not finished" in r.json()["detail"]
    assert "lottery has not been drawn" in r.json()["detail"]

    _open_room(league)
    assert "still on the clock" in client.post("/free-agency/periods").json()["detail"]

    for _ in range(_PICKS):
        draft.make_pick(_engine, _NEXT)
    assert client.post("/free-agency/periods").status_code == 201


def test_season_readiness_blocks_period_one_until_the_draft_is_done(league):
    blockers = season_readiness.season_blockers(_engine, _NEXT)
    draft_blockers = [b for b in blockers if b.check == "draft_complete"]
    assert len(draft_blockers) == 1
    assert "lottery has not been drawn" in draft_blockers[0].message

    _open_room(league)
    for _ in range(_PICKS):
        draft.make_pick(_engine, _NEXT)
    assert [b for b in season_readiness.season_blockers(_engine, _NEXT)
            if b.check == "draft_complete"] == []


def test_readiness_reports_the_check_even_when_it_passes(league):
    report = season_readiness.readiness_report(_engine, _NEXT)
    assert "draft_complete" in {c["name"] for c in report["checks"]}


# ---------------------------------------------------------------------------
# The endpoints.
# ---------------------------------------------------------------------------
def test_commissioner_runs_the_draft_end_to_end_over_http(league, client):
    _as_manager(role="commissioner")

    assert client.post("/draft/lottery", json={"seed": 5}).status_code == 201
    r = client.post("/draft/prospects", json={"text": _names(_PICKS + 2)})
    assert r.status_code == 201 and r.json()["enough"] is True
    assert client.post("/draft/open").status_code == 201

    state = client.get("/draft/state").json()
    assert state["status"] == "open" and state["is_commissioner"] is True
    assert state["on_the_clock"]["overall"] == 1
    assert len(state["board"]) == _PICKS + 2

    # the commissioner forcing a pick for the team on the clock
    r = client.post("/draft/pick", json={"force": True})
    assert r.status_code == 201, r.text
    assert r.json()["overall"] == 1


def test_a_manager_may_only_pick_on_their_own_turn(league, client):
    _open_room(league)
    on_clock = draft.on_the_clock(_engine, _NEXT)["team"]
    other = next(s for s in league if s != on_clock)

    _as_manager(other)
    assert client.post("/draft/pick", json={}).status_code == 403

    _as_manager(on_clock)
    board = draft.prospects(_engine, _NEXT)
    r = client.post("/draft/pick", json={"prospect_id": board[3]["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["player_name"] == board[3]["name"]
    assert r.json()["team"] == on_clock


def test_the_commissioner_gets_no_ownership_bypass_on_a_plain_pick(league, client):
    """A commissioner is also a manager with teams of their own; picking for
    somebody else has to be the explicit `force`, not a side effect of the role."""
    _open_room(league)
    on_clock = draft.on_the_clock(_engine, _NEXT)["team"]
    other = next(s for s in league if s != on_clock)
    _as_manager(other, role="commissioner")
    assert client.post("/draft/pick", json={}).status_code == 403
    assert client.post("/draft/pick", json={"force": True}).status_code == 201


def test_draft_writes_are_commissioner_only(league, client):
    _as_manager(league[0])
    assert client.post("/draft/lottery", json={}).status_code == 403
    assert client.post("/draft/prospects", json={"text": _names(64)}).status_code == 403
    assert client.post("/draft/open").status_code == 403
    # ...but the room itself is readable by any manager
    assert client.get("/draft/state").status_code == 200


def test_upload_accepts_a_multipart_file(league, client):
    _as_manager(role="commissioner")
    client.post("/draft/lottery", json={"seed": 1})
    r = client.post("/draft/prospects",
                    files={"file": ("class.csv", "Name,Position\nJo Ling,Goalie\n", "text/csv")})
    assert r.status_code == 201, r.text
    assert r.json()["prospects"] == 1
    assert draft.prospects(_engine, _NEXT)[0]["position"] == "Goalie"


def test_bad_uploads_are_rejected_with_a_sentence(league, client):
    _as_manager(role="commissioner")
    client.post("/draft/lottery", json={"seed": 1})
    assert client.post("/draft/prospects", json={"text": "   "}).status_code == 400


def test_picking_when_nobody_is_on_the_clock_is_a_conflict(league, client):
    _as_manager(role="commissioner")
    r = client.post("/draft/pick", json={"force": True})
    assert r.status_code == 409 and "not on the clock" in r.json()["detail"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
