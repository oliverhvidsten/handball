"""
Award voting over the dev Postgres, through the API: when the polls open, what a
ballot may say, that a vote is one-per-voter, and what the tally writes. Skips when
Postgres is unavailable. Local DB only (truncates).

The fixtures mirror tests/test_api.py (`_clean_db`, `_as_manager`) rather than
importing them, which is the convention here -- each suite owns its own truncation
list so adding a table to one cannot silently change another.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from handball import offseason
from handball import schedule_repository as sched_repo
from handball import voting
from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.pg_repository import PostgresTeamRepository
from handball.simulation_vars import AWARDS

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

# season_state is truncated here (test_api.py's list does not): every window in this
# suite is a function of periods_run, and active_season() reads the LATEST season_state
# row -- a season left behind by another test would silently move the season under us.
_TABLES = ("teams players injuries awards games player_game_lines "
           "draft_picks managers trades trade_assets fa_periods fa_rounds "
           "fa_auctions fa_offers fa_auction_seats fa_actions playoff_series "
           "coaches coach_tenures season_state "
           "ballots voting_status award_tallies all_star_games "
           "draft_lotteries draft_state draft_prospects hall_of_fame")

_SEASON = 2026
MVP = "Most Valuable Player"
ELEVENTH = "Eleventh Man of the Year"
ROOKIE = "Rookie of the Year"
COACH = "Coach of the Year"


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
def two_teams():
    repo = PostgresTeamRepository(_engine)
    repo.save(_team("Boston"))
    repo.save(_team("Denver"))
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


def _periods(n: int, season: int = _SEASON) -> None:
    sched_repo.init_season_state(_engine, season, schedule_seed=season, injury_seed=season)
    with _engine.begin() as c:
        c.execute(text("update season_state set periods_run=:n where season=:s"),
                  {"n": n, "s": season})


def _coach(name: str, slug: str, role: str = "HC", season: int = _SEASON,
           end_season=None) -> str:
    with _engine.begin() as c:
        cid = str(c.execute(
            text("insert into coaches (legacy_id, name) values (:l, :n) returning id"),
            {"l": name.lower().replace(" ", "-"), "n": name},
        ).scalar_one())
        c.execute(
            text("insert into coach_tenures (coach_id, team_id, role, start_season, end_season) "
                 "values (cast(:c as uuid), (select id from teams where slug=:s), "
                 "cast(:r as coach_role), :st, :en)"),
            {"c": cid, "s": slug, "r": role, "st": season, "en": end_season},
        )
    return cid


def _crown_champion(winner: str = "Boston", loser: str = "Denver", season: int = _SEASON):
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


def _candidates(client, award: str) -> list[dict]:
    return client.get("/voting/state").json()["candidates"]["awards"][award]


# -- the window --------------------------------------------------------------
def test_award_voting_is_shut_until_the_regular_season_ends(client, two_teams):
    _periods(4)
    _as_manager("Boston")
    state = client.get("/voting/state").json()
    assert state["status"]["award"] == "closed"

    cands = state["candidates"]["awards"][MVP]
    denver = next(c for c in cands if c["team_slug"] == "Denver")
    r = client.post("/voting/awards/ballot",
                    json={"award": MVP, "ranked_ids": [denver["id"]]})
    assert r.status_code == 409
    assert "not open" in r.json()["detail"]


def test_award_voting_opens_once_the_fifth_period_has_run(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    state = client.get("/voting/state").json()
    assert state["status"]["award"] == "open"
    # ...and the All-Star vote, which opened earlier, is still open too.
    assert state["status"]["allstar"] == "open"


def test_the_all_star_window_opens_at_the_break_and_the_awards_do_not(client, two_teams):
    _periods(3)
    _as_manager("Boston")
    status = client.get("/voting/state").json()["status"]
    assert status["allstar"] == "open"
    assert status["award"] == "closed"


def test_the_polls_open_without_anyone_opening_them(two_teams):
    """No scheduler in this deployment: the phase flips on the first read."""
    _periods(5)
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from voting_status")).scalar_one() == 0
    assert voting.sync_status(_engine, _SEASON)["award"] == "open"


# -- what a ballot may say ---------------------------------------------------
def test_you_may_not_vote_for_a_player_on_a_team_you_own(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    mine = next(c for c in _candidates(client, MVP) if c["team_slug"] == "Boston")
    assert mine["own_team"] is True
    r = client.post("/voting/awards/ballot",
                    json={"award": MVP, "ranked_ids": [mine["id"]]})
    assert r.status_code == 400
    assert "team you own" in r.json()["detail"]
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from ballots")).scalar_one() == 0


def test_a_multi_team_owner_is_blocked_on_every_team_they_own(client, two_teams):
    _periods(5)
    _as_manager("Boston", "Denver")
    cands = _candidates(client, MVP)
    assert all(c["own_team"] for c in cands)
    r = client.post("/voting/awards/ballot",
                    json={"award": MVP, "ranked_ids": [cands[0]["id"]]})
    assert r.status_code == 400


def test_a_ballot_may_not_name_the_same_player_twice(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    r = client.post("/voting/awards/ballot",
                    json={"award": MVP, "ranked_ids": [denver[0]["id"], denver[0]["id"]]})
    assert r.status_code == 400
    assert "twice" in r.json()["detail"]


def test_one_ballot_per_voter_and_resubmitting_replaces_it(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]

    first = [denver[0]["id"], denver[1]["id"]]
    second = [denver[2]["id"]]
    assert client.post("/voting/awards/ballot",
                       json={"award": MVP, "ranked_ids": first}).status_code == 200
    assert client.post("/voting/awards/ballot",
                       json={"award": MVP, "ranked_ids": second}).status_code == 200

    with _engine.connect() as c:
        assert c.execute(text("select count(*) from ballots")).scalar_one() == 1
    assert client.get("/voting/state").json()["my_ballots"]["award"][MVP] == second


def test_a_voter_sees_their_own_ballot_and_no_one_elses(client, two_teams):
    _periods(5)
    voter = _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    client.post("/voting/awards/ballot", json={"award": MVP, "ranked_ids": [denver[0]["id"]]})

    _as_manager("Denver")
    mine = client.get("/voting/state").json()["my_ballots"]
    assert mine["award"] == {}
    assert voter.user_id  # the other manager's ballot exists, it is simply not served
    with _engine.connect() as c:
        assert c.execute(text("select count(*) from ballots")).scalar_one() == 1


def test_ballot_counts_are_for_the_commissioner_only(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    client.post("/voting/awards/ballot", json={"award": MVP, "ranked_ids": [denver[0]["id"]]})
    assert client.get("/voting/state").json()["ballot_counts"] is None

    _as_manager("", role="commissioner")
    counts = client.get("/voting/state").json()["ballot_counts"]
    assert counts["award"][MVP] == 1


# -- eligibility -------------------------------------------------------------
def test_eleventh_man_candidates_are_players_in_a_bench_slot(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    cands = _candidates(client, ELEVENTH)
    legacy = {c["legacy_id"] for c in cands}
    assert "boston-f4" in legacy            # bench
    assert "boston-f1" not in legacy        # starter
    assert "boston-r1" not in legacy        # reserve
    with _engine.connect() as c:
        n = c.execute(text("select count(*) from players where slot_group='bench'")).scalar_one()
    assert len(cands) == n


def test_a_starter_is_not_an_eleventh_man_candidate(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    starter = next(c for c in _candidates(client, MVP)
                   if c["legacy_id"] == "denver-f1")
    r = client.post("/voting/awards/ballot",
                    json={"award": ELEVENTH, "ranked_ids": [starter["id"]]})
    assert r.status_code == 400
    assert "not eligible" in r.json()["detail"]


def test_rookie_candidates_are_first_year_players_only(client, two_teams):
    with _engine.begin() as c:
        c.execute(text("update players set years_in_league = 4"))
        c.execute(text("update players set years_in_league = 0 where legacy_id = 'denver-f1'"))
    _periods(5)
    _as_manager("Boston")
    assert [c["legacy_id"] for c in _candidates(client, ROOKIE)] == ["denver-f1"]


def test_coach_of_the_year_candidates_hold_an_open_tenure(client, two_teams):
    _coach("Anna Reed", "Denver", "HC")
    # a tenure that is over: it must also START before it ends (coach_tenures_check)
    _coach("Ben Cole", "Denver", "OC", season=_SEASON - 3, end_season=_SEASON - 1)
    _periods(5)
    _as_manager("Boston")
    cands = _candidates(client, COACH)
    assert [c["name"] for c in cands] == ["Anna Reed"]
    assert cands[0]["kind"] == "coach"


def test_you_may_not_vote_for_your_own_coach(client, two_teams):
    _coach("Anna Reed", "Boston", "HC")
    _periods(5)
    _as_manager("Boston")
    coach = _candidates(client, COACH)[0]
    assert coach["own_team"] is True
    r = client.post("/voting/awards/ballot",
                    json={"award": COACH, "ranked_ids": [coach["id"]]})
    assert r.status_code == 400


# -- the tally ---------------------------------------------------------------
def _vote(client, award: str, ids: list[str]):
    r = client.post("/voting/awards/ballot", json={"award": award, "ranked_ids": ids})
    assert r.status_code == 200, r.text


def test_the_tally_writes_the_evidence_and_the_winner(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    winner, runner_up = denver[0], denver[1]
    _vote(client, MVP, [winner["id"], runner_up["id"]])

    # A commissioner who owns no team may vote for anyone; here they second the
    # same name, so the winner is not decided by a tiebreak.
    _as_manager("", role="commissioner")
    boston = [c for c in _candidates(client, MVP) if c["team_slug"] == "Boston"]
    _vote(client, MVP, [winner["id"], boston[0]["id"]])

    body = client.post("/voting/awards/tally").json()
    assert body["winners"][MVP]["entity_id"] == winner["id"]
    assert body["winners"][MVP]["points"] == 20
    assert body["winners"][MVP]["first_place_votes"] == 2

    with _engine.connect() as c:
        rows = c.execute(
            text("select entity_id, points, first_place_votes, rank from award_tallies "
                 "where season=:s and award=:a order by rank"),
            {"s": _SEASON, "a": MVP},
        ).all()
        # three names drew votes: 10+10, and two separate seconds worth 7
        assert len(rows) == 3
        assert [r[1] for r in rows] == [20, 7, 7]
        assert rows[0][2] == 2
        awarded = c.execute(
            text("select player_id, coach_id from awards where season=:s and award=:a"),
            {"s": _SEASON, "a": MVP},
        ).one()
    assert str(awarded[0]) == winner["id"] and awarded[1] is None


def test_coach_of_the_year_lands_in_the_coach_column(client, two_teams):
    cid = _coach("Anna Reed", "Denver", "HC")
    _periods(5)
    _as_manager("Boston")
    _vote(client, COACH, [cid])

    _as_manager("", role="commissioner")
    body = client.post("/voting/awards/tally").json()
    assert body["winners"][COACH]["entity_kind"] == "coach"
    assert body["winners"][COACH]["name"] == "Anna Reed"

    with _engine.connect() as c:
        player_id, coach_id = c.execute(
            text("select player_id, coach_id from awards where season=:s and award=:a"),
            {"s": _SEASON, "a": COACH},
        ).one()
        kind = c.execute(
            text("select entity_kind from award_tallies where season=:s and award=:a"),
            {"s": _SEASON, "a": COACH},
        ).scalar_one()
    # The 0015 check constraint allows exactly one recipient; this is the coach case.
    assert player_id is None and str(coach_id) == cid
    assert kind == "coach"


def test_an_award_nobody_voted_on_has_no_winner(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    _vote(client, MVP, [denver[0]["id"]])

    _as_manager("", role="commissioner")
    winners = client.post("/voting/awards/tally").json()["winners"]
    assert winners[MVP] is not None
    assert all(winners[a] is None for a in AWARDS if a != MVP)
    with _engine.connect() as c:
        assert c.execute(
            text("select count(*) from awards where season=:s"), {"s": _SEASON}
        ).scalar_one() == 1


def test_the_tally_is_commissioner_only(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    assert client.post("/voting/awards/tally").status_code == 403
    assert client.post("/voting/all-star/play").status_code == 403


def test_the_tally_closes_the_vote_for_good(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    _vote(client, MVP, [denver[0]["id"]])

    _as_manager("", role="commissioner")
    assert client.post("/voting/awards/tally").status_code == 200
    assert client.get("/voting/state").json()["status"]["award"] == "tallied"
    r = client.post("/voting/awards/tally")
    assert r.status_code == 409 and "already been counted" in r.json()["detail"]

    _as_manager("Boston")
    r = client.post("/voting/awards/ballot",
                    json={"award": MVP, "ranked_ids": [denver[1]["id"]]})
    assert r.status_code == 409


def test_results_carry_the_whole_count(client, two_teams):
    _periods(5)
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    _vote(client, MVP, [denver[0]["id"], denver[1]["id"], denver[2]["id"]])
    _as_manager("", role="commissioner")
    client.post("/voting/awards/tally")

    body = client.get("/voting/results").json()
    assert body["season"] == _SEASON
    mvp = next(a for a in body["awards"] if a["award"] == MVP)
    assert mvp["winner"]["legacy_id"] == denver[0]["legacy_id"]
    assert [row["points"] for row in mvp["tally"]] == [10, 7, 5]
    assert [row["rank"] for row in mvp["tally"]] == [1, 2, 3]


# -- the gate on the rollover ------------------------------------------------
def test_the_rollover_waits_for_the_award_tally(client, two_teams):
    _periods(5)
    _crown_champion()
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    _vote(client, MVP, [denver[0]["id"]])

    _as_manager("", role="commissioner")
    r = client.post("/season/advance")
    assert r.status_code == 409
    assert "tally the award ballots" in r.json()["detail"]

    client.post("/voting/awards/tally")
    assert client.post("/season/advance").status_code == 200


def test_an_open_vote_blocks_the_rollover_even_with_no_ballots(client, two_teams):
    """The polls being open IS a vote in progress; the rollover zeroes the season it
    was to be cast on."""
    _periods(5)
    _crown_champion()
    _as_manager("Boston")
    client.get("/voting/state")            # opens the polls
    _as_manager("", role="commissioner")
    assert client.post("/season/advance").status_code == 409


def test_the_rollover_does_not_wipe_the_voted_awards(client, two_teams):
    """`awards` holds both kinds. The stat titles are recomputed at rollover and the
    voted ones must survive it -- the stats they were voted on are about to be gone,
    so a second chance to count them does not exist."""
    _periods(5)
    _crown_champion()
    _as_manager("Boston")
    denver = [c for c in _candidates(client, MVP) if c["team_slug"] == "Denver"]
    _vote(client, MVP, [denver[0]["id"]])
    _as_manager("", role="commissioner")
    client.post("/voting/awards/tally")

    assert client.post("/season/advance").status_code == 200
    with _engine.connect() as c:
        labels = {
            r[0] for r in c.execute(
                text("select award from awards where season=:s"), {"s": _SEASON}
            ).all()
        }
    assert MVP in labels
    assert offseason.AWARD_TOP_SCORER not in labels   # no games, so no stat title
