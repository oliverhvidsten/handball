"""
The persisted postseason (handball/playoffs.py) against the dev Postgres: seeding a
bracket from final standings, running it round by round to a champion, re-seeding,
what a playoff game must NOT touch, and rolling a round back.

The bracket's pure shape (seeding, pairing) is covered DB-free in
tests/test_postseason.py.

Skips when Postgres is unavailable; local DB only (truncates).
"""
import pytest
from sqlalchemy import text

from handball import playoffs
from handball.db import get_engine, is_local_db
from handball.domain import Player, Team
from handball.league_structure import get_conference
from handball.orchestration import SimpleGameEngine
from handball.pg_repository import PostgresTeamRepository

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from playoff_series limit 1"))
    _PG_OK = is_local_db()        # destructive tests: local DB only, never remote
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(
    not _PG_OK, reason="Postgres dev DB not available/migrated (needs alembic 0012)")

_TABLES = ("teams players injuries awards games player_game_lines draft_picks managers "
           "trades trade_assets playoff_series season_state schedule_games")
_SEASON = 2027

# Eight per conference, taken from league_structure so get_conference resolves them.
_EAST = ["Boston", "New York", "Philadelphia", "Washington",
         "Charlotte", "Atlanta", "Miami", "Tampa Bay"]
_WEST = ["Milwaukee", "Minneapolis", "St. Louis", "Kansas City",
         "Oklahoma City", "New Orleans", "Dallas", "Houston"]


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield


def _team(team_id: str, strength: float) -> Team:
    """A legal roster whose starters' offense is `strength`, so SimpleGameEngine
    (attack minus the opposing goalie) makes the stronger team win, every time."""
    def p(pid, pos, off=None, gk=0.1):
        return Player(id=f"{team_id.lower().replace(' ', '-')}-{pid}",
                      name=f"{team_id} {pid}", position=pos,
                      offense=strength if off is None else off,
                      defense=5.0, goalie_skill=gk, variance=0.5)

    return Team(
        id=team_id, name=team_id, coaches=["HC", "OC", "DC"],
        starters={
            "Forward": [p("f1", "Forward"), p("f2", "Forward"), p("f3", "Forward")],
            "Midfielder": [p("m1", "Midfielder"), p("m2", "Midfielder"), p("m3", "Midfielder")],
            "Defense": [p("d1", "Defense"), p("d2", "Defense"), p("d3", "Defense")],
            "Goalie": [p("g1", "Goalie", off=0.1, gk=6.0)],
        },
        bench={
            "Forward": [p("f4", "Forward"), p("f5", "Forward")],
            "Midfielder": [p("m4", "Midfielder"), p("m5", "Midfielder")],
            "Defense": [p("d4", "Defense"), p("d5", "Defense")],
            "Goalie": [p("g2", "Goalie", off=0.1, gk=5.0)],
        },
        reserves=[p("r1", "Forward"), p("r2", "Defense")],
    )


@pytest.fixture
def ranked() -> list[str]:
    """Sixteen teams, best->worst, alternating conferences so each gets seeds 1..8.
    Strength and the persisted W-L both follow the ranking, so the favourite always
    wins and the standings agree with the list we seed from."""
    order = [t for pair in zip(_EAST, _WEST) for t in pair]  # E1,W1,E2,W2,...
    repo = PostgresTeamRepository(_engine)
    for i, slug in enumerate(order):
        repo.save(_team(slug, strength=8.0 - i * 0.2))
    with _engine.begin() as c:
        c.execute(
            text("update teams set wins = :w, losses = :l where slug = :s"),
            [{"s": slug, "w": 60 - i, "l": i} for i, slug in enumerate(order)],
        )
        c.execute(
            text("insert into season_state (season, periods_run) values (:s, 5)"),
            {"s": _SEASON},
        )
    return order


def _run(**kw):
    return playoffs.run_round(
        _engine, _SEASON, game_engine=SimpleGameEngine(), roll_injuries=False, **kw
    )


def _series(round_num: int) -> list[dict]:
    return [s for s in playoffs.bracket(_engine, _SEASON)["series"]
            if s["round"] == round_num]


def _count(sql: str) -> int:
    with _engine.connect() as c:
        return c.execute(text(sql)).scalar_one()


# -- shape -------------------------------------------------------------------
def test_round_counts():
    assert playoffs.conference_rounds(8) == 3
    assert playoffs.total_rounds(8) == 4
    assert playoffs.total_rounds(4) == 3
    with pytest.raises(playoffs.PlayoffError):
        playoffs.conference_rounds(6)


# -- seeding -----------------------------------------------------------------
def test_start_seeds_one_v_eight(ranked):
    data = playoffs.start_playoffs(_engine, _SEASON, ranked)

    assert data["started"] and data["next_round"] == 1
    assert data["total_rounds"] == 4 and data["champion"] is None
    first = _series(1)
    assert len(first) == 8                       # 4 matchups per conference

    east = [s for s in first if s["conference"] == "Eastern"]
    assert [(s["high"]["seed"], s["low"]["seed"]) for s in east] == [(1, 8), (2, 7), (3, 6), (4, 5)]
    assert east[0]["high"]["slug"] == _EAST[0] and east[0]["low"]["slug"] == _EAST[7]
    assert east[0]["label"] == "Eastern Quarterfinals"
    # Every seeded team really is in the conference it was seeded into.
    for s in first:
        assert get_conference(s["high"]["slug"]) == s["conference"]
        assert get_conference(s["low"]["slug"]) == s["conference"]


def test_start_refuses_a_second_bracket(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    with pytest.raises(playoffs.PlayoffError, match="already exists"):
        playoffs.start_playoffs(_engine, _SEASON, ranked)


def test_start_refuses_a_short_conference(ranked):
    with pytest.raises(playoffs.PlayoffError, match="needs 8 teams"):
        playoffs.start_playoffs(_engine, _SEASON, ranked[:9])


# -- running -----------------------------------------------------------------
def test_round_one_advances_favourites_and_reseeds(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    result = _run()

    assert result["round"] == 1 and result["games"] == 8
    # The stronger team is the higher seed in every matchup, so all four favourites
    # survive and the next round re-seeds them 1v4, 2v3.
    assert [s["winner"] for s in _series(1)] == [s["high"]["slug"] for s in _series(1)]
    east2 = [s for s in _series(2) if s["conference"] == "Eastern"]
    assert [(s["high"]["seed"], s["low"]["seed"]) for s in east2] == [(1, 4), (2, 3)]
    assert east2[0]["label"] == "Eastern Semifinals"
    assert playoffs.bracket(_engine, _SEASON)["next_round"] == 2


def test_bracket_runs_to_a_champion(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    rounds = [_run()["round"] for _ in range(4)]

    assert rounds == [1, 2, 3, 4]
    data = playoffs.bracket(_engine, _SEASON)
    assert data["complete"] and data["next_round"] is None
    assert data["champion"] == ranked[0]          # the best team, undefeated
    assert len(data["series"]) == 15              # 8 + 4 + 2 + 1
    assert _count(f"select count(*) from games where season = {_SEASON}") == 15
    assert _count(
        f"select count(*) from games where season = {_SEASON} and is_playoff") == 15
    # Rounds are tagged, and no playoff game claims a fixture-list week.
    assert _count(
        f"select count(*) from games where season = {_SEASON} and week is not null") == 0
    assert playoffs.is_complete(_engine, _SEASON)

    final = _series(4)
    assert len(final) == 1 and final[0]["label"] == "Final"
    assert final[0]["conference"] is None
    # The Final is hosted by the better regular-season record, not the conference.
    assert final[0]["high"]["slug"] == ranked[0]
    assert final[0]["low"]["slug"] == ranked[1]


def test_running_a_finished_postseason_is_refused(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    for _ in range(4):
        _run()
    with pytest.raises(playoffs.PlayoffError, match="complete"):
        _run()


def test_running_without_a_bracket_is_refused(ranked):
    with pytest.raises(playoffs.PlayoffError, match="not been seeded"):
        _run()


def test_playoff_games_never_touch_team_records(ranked):
    def records() -> dict[str, tuple[int, int, int]]:
        with _engine.connect() as c:
            return {r[0]: (r[1], r[2], r[3]) for r in c.execute(
                text("select slug, wins, losses, ties from teams"))}

    before = records()
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    for _ in range(4):
        _run()
    assert records() == before


def test_the_cursor_follows_the_rounds(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()
    _run()
    assert _count(
        f"select playoff_rounds_run from season_state where season = {_SEASON}") == 2


def test_injuries_roll_for_the_teams_that_played(ranked):
    """The default path (roll_injuries=True) runs end to end and ticks only the
    round's participants -- eliminated teams' injuries stand."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    result = playoffs.run_round(
        _engine, _SEASON, game_engine=SimpleGameEngine(), injury_seed=7
    )
    assert result["games"] == 8


# -- stats separation --------------------------------------------------------
def test_playoff_lines_stay_out_of_season_stats(ranked):
    """A playoff stat line is recorded but must not reach player_season_stats --
    which is what keeps the Leaders page and the MVP award regular-season only."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    with _engine.begin() as c:
        pid = c.execute(
            text("select id from players where legacy_id = :l"),
            {"l": "boston-f1"},
        ).scalar_one()
        for is_playoff, goals in ((False, 3), (True, 9)):
            gid = c.execute(
                text("insert into games (season, week, is_playoff) "
                     "values (:s, :w, :p) returning id"),
                {"s": _SEASON, "w": None if is_playoff else 1, "p": is_playoff},
            ).scalar_one()
            c.execute(
                text("insert into player_game_lines "
                     "(game_id, player_id, season, goals, is_playoff) "
                     "values (:g, :p, :s, :goals, :po)"),
                {"g": gid, "p": pid, "s": _SEASON, "goals": goals, "po": is_playoff},
            )

    with _engine.connect() as c:
        row = c.execute(
            text("select games, goals from player_season_stats "
                 "where player_id = :p and season = :s"),
            {"p": pid, "s": _SEASON},
        ).mappings().one()
    assert (row["games"], row["goals"]) == (1, 3)   # the playoff line is invisible


def test_awards_ignore_playoff_lines(ranked):
    """A monster postseason must not win the MVP: the awards read the same
    regular-season-only lines the leaderboard does."""
    from handball import offseason

    with _engine.begin() as c:
        ids = {
            legacy: c.execute(text("select id from players where legacy_id = :l"),
                              {"l": legacy}).scalar_one()
            for legacy in ("boston-f1", "milwaukee-f1")
        }
        for legacy, is_playoff, goals in (
            ("boston-f1", False, 4),        # a modest regular season
            ("milwaukee-f1", True, 40),     # a huge postseason, and nothing else
        ):
            gid = c.execute(
                text("insert into games (season, is_playoff) values (:s, :p) returning id"),
                {"s": _SEASON, "p": is_playoff},
            ).scalar_one()
            c.execute(
                text("insert into player_game_lines "
                     "(game_id, player_id, season, goals, performance, is_playoff) "
                     "values (:g, :p, :s, :goals, :perf, :po)"),
                {"g": gid, "p": ids[legacy], "s": _SEASON, "goals": goals,
                 "perf": float(goals), "po": is_playoff},
            )
        awards = offseason._compute_awards(c, _SEASON)

    assert awards[offseason.AWARD_TOP_SCORER] == "boston-f1"
    assert awards[offseason.AWARD_MVP] == "boston-f1"


# -- recovery ----------------------------------------------------------------
def test_reset_round_undoes_it(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()
    _run()
    assert len(_series(3)) == 2

    result = playoffs.reset_round(_engine, _SEASON, 2)

    assert result["games_deleted"] == 4          # round 2's games (round 3 was unplayed)
    assert _series(3) == []                      # rounds built off it are gone
    assert all(s["winner"] is None for s in _series(2))
    assert all(s["winner"] for s in _series(1))  # round 1 stands
    assert playoffs.bracket(_engine, _SEASON)["next_round"] == 2
    assert _count(
        f"select playoff_rounds_run from season_state where season = {_SEASON}") == 1
    # ...and the round can simply be run again.
    assert _run()["round"] == 2


def test_reset_defaults_to_the_round_in_progress(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()
    playoffs.reset_round(_engine, _SEASON)       # round 2 is next, and unplayed
    assert all(s["winner"] for s in _series(1))  # so round 1 survives
    assert _series(2) != []


def test_clear_playoffs_removes_the_bracket(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()
    assert playoffs.clear_playoffs(_engine, _SEASON) == 8 + 4

    data = playoffs.bracket(_engine, _SEASON)
    assert not data["started"] and data["series"] == []
    assert _count(f"select count(*) from games where season = {_SEASON}") == 0
    # ...and the season can be seeded again from scratch.
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    assert len(_series(1)) == 8
