"""
The All-Star ballot, the selection, and the exhibition, over the dev Postgres. The
load-bearing test here is the one that asserts the game reaches all_star_games and
NOTHING else: the moment an exhibition lands in `games` or `player_game_lines` it
starts deciding standings, leaderboards and the award race.

Teams are named from handball/league_structure so the conferences are real. Skips
when Postgres is unavailable. Local DB only (truncates).
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball import all_star
from handball import schedule_repository as sched_repo
from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.league_structure import CONFERENCES, get_conference
from handball.pg_repository import PostgresTeamRepository
from handball.simulation_vars import ALL_STAR_BALLOT

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

if _PG_OK:
    from api.auth import Manager, get_current_manager
    from api.main import app

_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets fa_periods fa_rounds "
           "fa_auctions fa_offers fa_auction_seats fa_actions playoff_series "
           "coaches coach_tenures season_state "
           "ballots voting_status award_tallies all_star_games "
           "draft_lotteries draft_state draft_prospects hall_of_fame")

_SEASON = 2026
# Two real teams per conference: enough bodies to fill a 5/5/5/2 ballot twice over,
# and enough teams that an own-team vote is a thing that can be refused.
EAST = ["Boston", "New York"]
WEST = ["Denver", "Seattle"]


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _team(team_id: str) -> Team:
    def p(pid, name, pos, off=5.0, deff=5.0, gk=0.1):
        return Player(id=f"{team_id.lower()}-{pid}", name=f"{team_id} {name}", position=pos,
                      offense=off, defense=deff, goalie_skill=gk, variance=0.5)

    return Team(
        id=team_id, name=team_id, coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "F1", "Forward", off=7), p("f2", "F2", "Forward", off=6),
                        p("f3", "F3", "Forward", off=6)],
            "Midfielder": [p("m1", "M1", "Midfielder"), p("m2", "M2", "Midfielder"),
                           p("m3", "M3", "Midfielder")],
            "Defense": [p("d1", "D1", "Defense", deff=7), p("d2", "D2", "Defense", deff=7),
                        p("d3", "D3", "Defense", deff=6)],
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
    for slug in EAST + WEST:
        repo.save(_team(slug))
    return repo


def _uuid_of(slug: str) -> str:
    with _engine.connect() as c:
        return str(c.execute(text("select id from teams where slug=:s"), {"s": slug}).scalar_one())


def _as_manager(*slugs: str, role: str = "manager") -> Manager:
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
    return mgr


def _periods(n: int, season: int = _SEASON, *, scheduled: bool = False) -> None:
    sched_repo.init_season_state(_engine, season, schedule_seed=season, injury_seed=season)
    with _engine.begin() as c:
        c.execute(
            text("update season_state set periods_run=:n, schedule_generated=:g "
                 "where season=:s"),
            {"n": n, "s": season, "g": scheduled},
        )


def _pools(client, conference: str) -> dict[str, list[dict]]:
    return client.get("/voting/state").json()["candidates"]["all_star"][conference]


def _ballot_from(pools: dict[str, list[dict]], offset: int = 0) -> dict[str, list[str]]:
    """A full ballot taking each position's players starting at `offset`."""
    return {
        pos: [c["id"] for c in pools[pos][offset:offset + n]]
        for pos, n in ALL_STAR_BALLOT.items()
    }


def _vote(client, conference: str, ballot: dict[str, list[str]]):
    r = client.post("/voting/all-star/ballot",
                    json={"conference": conference, "ballot": ballot})
    assert r.status_code == 200, r.text
    return r


def _seed_votes(client) -> dict[str, dict[str, list[dict]]]:
    """Enough of a vote to select both squads: two unaffiliated voters (managers who
    own no team, so nothing is off limits to them) filing a full ballot in each
    conference. Who exactly starts is not this helper's concern -- see
    test_the_top_of_the_vote_starts for that."""
    pools = {}
    for _voter in range(2):
        _as_manager("")
        for conference in CONFERENCES:
            p = _pools(client, conference)
            pools[conference] = p
            _vote(client, conference, _ballot_from(p))
    return pools


# -- the ballot --------------------------------------------------------------
def test_the_all_star_ballot_opens_at_the_break(client, league):
    _periods(2)
    _as_manager("Boston")
    assert client.get("/voting/state").json()["status"]["allstar"] == "closed"
    _periods(3)
    assert client.get("/voting/state").json()["status"]["allstar"] == "open"


def test_a_ballot_must_be_full_at_every_position(client, league):
    _periods(3)
    _as_manager("")
    ballot = _ballot_from(_pools(client, "Eastern"))
    ballot["Forward"] = ballot["Forward"][:3]
    r = client.post("/voting/all-star/ballot",
                    json={"conference": "Eastern", "ballot": ballot})
    assert r.status_code == 400
    assert "exactly 5 at Forward" in r.json()["detail"]


def test_you_may_not_put_your_own_player_on_the_ballot(client, league):
    _periods(3)
    _as_manager("Boston")
    pools = _pools(client, "Eastern")
    assert any(c["own_team"] for c in pools["Forward"])
    r = client.post("/voting/all-star/ballot",
                    json={"conference": "Eastern", "ballot": _ballot_from(pools)})
    assert r.status_code == 400
    assert "team you own" in r.json()["detail"]


def test_candidates_are_confined_to_their_conference(client, league):
    _periods(3)
    _as_manager("")
    east = _pools(client, "Eastern")
    slugs = {c["team_slug"] for lst in east.values() for c in lst}
    assert slugs == set(EAST)
    assert all(get_conference(s) == "Eastern" for s in slugs)


def test_a_western_player_cannot_be_named_on_the_eastern_ballot(client, league):
    _periods(3)
    _as_manager("")
    east, west = _pools(client, "Eastern"), _pools(client, "Western")
    ballot = _ballot_from(east)
    ballot["Goalie"][0] = west["Goalie"][0]["id"]
    r = client.post("/voting/all-star/ballot",
                    json={"conference": "Eastern", "ballot": ballot})
    assert r.status_code == 400
    assert "not an eligible Goalie" in r.json()["detail"]


def test_one_ballot_per_voter_per_conference_upserts(client, league):
    _periods(3)
    _as_manager("")
    pools = _pools(client, "Eastern")
    _vote(client, "Eastern", _ballot_from(pools, offset=0))
    _vote(client, "Eastern", _ballot_from(pools, offset=1))
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from ballots")).scalar_one() == 1
    saved = client.get("/voting/state").json()["my_ballots"]["allstar"]["Eastern"]
    assert saved["Forward"] == [c["id"] for c in pools["Forward"][1:6]]


# -- the game ----------------------------------------------------------------
def _play(client, league) -> dict:
    _periods(3)
    _seed_votes(client)
    _as_manager("", role="commissioner")
    r = client.post("/voting/all-star/play")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_exhibition_is_played_and_stored(client, league):
    game = _play(client, league)
    assert {game["home_conference"], game["away_conference"]} == set(CONFERENCES)
    # allow_tie=False, so somebody wins -- overtime settles a level game.
    assert game["home_score"] != game["away_score"]
    assert len(game["home_roster"]["players"]) == sum(ALL_STAR_BALLOT.values()) == 17
    assert len(game["away_roster"]["players"]) == 17
    assert len(game["box_score"]["home"]["players"]) == 17
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from all_star_games")).scalar_one() == 1


def test_the_exhibition_writes_nothing_to_games_or_player_game_lines(client, league):
    """The whole reason all_star_games exists. `games` and `player_game_lines` feed
    the standings, the leaderboards and the award race; an exhibition in either of
    them is a bug in all three."""
    _play(client, league)
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from games")).scalar_one() == 0
        assert c.execute(text("select count(*) from player_game_lines")).scalar_one() == 0


def test_the_exhibition_leaves_team_records_and_players_alone(client, league):
    with _engine.connect() as c:
        before = c.execute(
            text("select coalesce(sum(wins+losses+ties),0) from teams")
        ).scalar_one()
        players_before = c.execute(
            text("select count(*), coalesce(sum(offense),0)::numeric(12,4) from players")
        ).one()
    _play(client, league)
    with _engine.connect() as c:
        assert c.execute(
            text("select coalesce(sum(wins+losses+ties),0) from teams")
        ).scalar_one() == before
        assert c.execute(
            text("select count(*), coalesce(sum(offense),0)::numeric(12,4) from players")
        ).one() == players_before


def test_the_top_of_the_vote_starts(client, league):
    """The commissioner's decision: the leading 3/3/3/1 by votes START, and the rest
    of the ballot rides the bench."""
    _periods(3)
    pools = None
    # Three voters. All three name forwards 0-2; only the first two name 3-4, and the
    # third swaps in 5-6. So 0-2 draw three votes, 3-4 two, 5-6 one -- an order the
    # count produces on its own, with no tiebreak involved.
    for forwards in ([0, 1, 2, 3, 4], [0, 1, 2, 3, 4], [0, 1, 2, 5, 6]):
        _as_manager("")
        for conference in CONFERENCES:
            p = _pools(client, conference)
            if conference == "Eastern":
                pools = p
            ballot = _ballot_from(p)
            ballot["Forward"] = [p["Forward"][i]["id"] for i in forwards]
            _vote(client, conference, ballot)
    _as_manager("", role="commissioner")
    game = client.post("/voting/all-star/play").json()

    east = game["home_roster"] if game["home_conference"] == "Eastern" else game["away_roster"]
    starters = [r for r in east["players"] if r["slot"] == "starter" and r["position"] == "Forward"]
    bench = [r for r in east["players"] if r["slot"] == "bench" and r["position"] == "Forward"]
    assert len(starters) == 3 and len(bench) == 2
    # Forwards 0-2 drew 3 votes each (every ballot named them); 3-4 drew two.
    assert all(s["votes"] == 3 for s in starters)
    assert {s["player_id"] for s in starters} == {c["id"] for c in pools["Forward"][:3]}
    assert all(b["votes"] < 3 for b in bench)


def test_the_exhibition_cannot_be_played_twice(client, league):
    _play(client, league)
    r = client.post("/voting/all-star/play")
    assert r.status_code == 409
    assert "already been played" in r.json()["detail"]


def test_playing_closes_the_all_star_vote(client, league):
    _play(client, league)
    _as_manager("")
    state = client.get("/voting/state").json()
    assert state["status"]["allstar"] == "tallied"
    pools = state["candidates"]["all_star"]["Eastern"]
    r = client.post("/voting/all-star/ballot",
                    json={"conference": "Eastern", "ballot": _ballot_from(pools)})
    assert r.status_code == 409


def test_home_conference_alternates_by_season_parity(client, league):
    a, b = all_star.home_conference(2026), all_star.home_conference(2027)
    assert a != b
    assert {a, b} == set(CONFERENCES)
    assert all_star.home_conference(2028) == a


def test_the_results_page_gets_the_box_score(client, league):
    _play(client, league)
    _as_manager("")
    body = client.get("/voting/results").json()
    game = body["all_star_game"]
    assert game is not None
    assert body["season"] == _SEASON
    line = game["box_score"]["home"]["players"][0]
    assert {"legacy_id", "name", "position", "slot", "votes", "goals", "saves"} <= set(line)
    assert game["box_score"]["home"]["score"] == game["home_score"]


# -- the gate on period 4 ----------------------------------------------------
def test_period_four_waits_for_the_all_star_game(client, league):
    """The break falls after ALL_STAR_AFTER_PERIOD; the second half does not start
    until the league has played its showcase."""
    _periods(3, scheduled=True)
    _as_manager("", role="commissioner")
    r = client.post("/periods/run")
    assert r.status_code == 409
    assert "All-Star game" in r.json()["detail"]

    with pytest.raises(all_star.AllStarError):
        all_star.assert_played(_engine, _SEASON)


def test_the_gate_lifts_once_the_game_is_played(client, league):
    _periods(3, scheduled=True)
    _seed_votes(client)
    _as_manager("", role="commissioner")
    assert client.post("/voting/all-star/play").status_code == 200
    all_star.assert_played(_engine, _SEASON)          # no longer raises

    # ...and /periods/run gets past the gate (the simulation itself runs in the
    # background and is not what this test is about).
    assert client.post("/periods/run").status_code == 202


def test_earlier_periods_are_not_gated(client, league):
    """Only period 4 waits on the exhibition -- period 3 must be able to run in
    order for the break to be reached at all."""
    _periods(2, scheduled=True)
    _as_manager("", role="commissioner")
    assert client.post("/periods/run").status_code == 202
