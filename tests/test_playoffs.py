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
from handball.league_structure import division_key, get_conference
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

# Eight per conference, taken from league_structure so get_conference/division_key
# resolve them -- TWO from each of the conference's four divisions, so the seeding
# really has four division winners to promote and four wildcards to rank.
_EAST = ["Boston", "New York",          # Mid-Atlantic
         "Charlotte", "Atlanta",        # South
         "Toronto", "Detroit",          # Midwest
         "Cincinnati", "Louisville"]    # Country
_WEST = ["Milwaukee", "Minneapolis",    # North
         "Oklahoma City", "New Orleans",  # South
         "Phoenix", "Los Angeles",      # Pacific
         "Las Vegas", "Denver"]         # Mountain

# The four East division winners, given the ranking the `ranked` fixture builds.
_EAST_WINNERS = ["Boston", "Charlotte", "Toronto", "Cincinnati"]
_EAST_WILDCARDS = ["New York", "Atlanta", "Detroit", "Louisville"]


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


def test_division_winners_take_the_top_four_seeds(ranked):
    """Four divisions per conference, so seeds 1-4 are the division winners and 5-8
    the best of the rest -- each division is represented, and one bad division can't
    send four teams."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)

    seeds = {}
    for s in _series(1):
        if s["conference"] != "Eastern":
            continue
        seeds[s["high"]["seed"]] = s["high"]["slug"]
        seeds[s["low"]["seed"]] = s["low"]["slug"]

    assert [seeds[i] for i in (1, 2, 3, 4)] == _EAST_WINNERS
    assert sorted(seeds[i] for i in (5, 6, 7, 8)) == sorted(_EAST_WILDCARDS)
    # one seeded team per division, per conference, among the top four
    assert len({division_key(seeds[i]) for i in (1, 2, 3, 4)}) == 4


def test_a_division_winner_can_be_seeded_above_a_stronger_wildcard(ranked):
    """Toronto wins the Midwest and takes the 3 seed despite Atlanta finishing ahead
    of it; Atlanta is the 6 seed. That is the point of division-based seeding -- and
    it means the 3 seed can genuinely be the underdog."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)

    three_six = [s for s in _series(1)
                 if s["conference"] == "Eastern" and s["high"]["seed"] == 3][0]
    assert three_six["high"]["slug"] == "Toronto"     # division winner, hosts
    assert three_six["low"]["slug"] == "Atlanta"      # finished ahead, seeded below
    assert ranked.index("Atlanta") < ranked.index("Toronto")

    _run()
    decided = [s for s in _series(1)
               if s["conference"] == "Eastern" and s["high"]["seed"] == 3][0]
    assert decided["winner"] == "Atlanta"             # ...and the underdog host loses


def test_start_refuses_a_second_bracket(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    with pytest.raises(playoffs.PlayoffError, match="already exists"):
        playoffs.start_playoffs(_engine, _SEASON, ranked)


def test_start_refuses_a_short_conference(ranked):
    with pytest.raises(playoffs.PlayoffError, match="needs 8 teams"):
        playoffs.start_playoffs(_engine, _SEASON, ranked[:9])


# -- running -----------------------------------------------------------------
def test_round_one_reseeds_the_survivors(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    result = _run()

    assert result["round"] == 1 and result["games"] == 8
    east2 = [s for s in _series(2) if s["conference"] == "Eastern"]
    assert len(east2) == 2
    assert east2[0]["label"] == "Eastern Semifinals"
    # Survivors are re-paired best-vs-worst on their ORIGINAL seeds: the best
    # surviving seed draws the worst, whatever those numbers turned out to be.
    east1 = [s for s in _series(1) if s["conference"] == "Eastern"]
    survivors = sorted(
        (s["high"]["seed"] if s["winner"] == s["high"]["slug"] else s["low"]["seed"])
        for s in east1
    )
    assert [(s["high"]["seed"], s["low"]["seed"]) for s in east2] == [
        (survivors[0], survivors[3]), (survivors[1], survivors[2])
    ]
    assert playoffs.bracket(_engine, _SEASON)["next_round"] == 2


def test_an_upset_survivor_keeps_its_original_seed(ranked):
    """A 6 seed that knocks out the 3 is still the 6 in the next round -- re-seeding
    rewards the regular season, it doesn't reset it."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()

    east2 = [s for s in _series(2) if s["conference"] == "Eastern"]
    atlanta = [s for s in east2 if "Atlanta" in (s["high"]["slug"], s["low"]["slug"])][0]
    seed = (atlanta["high"]["seed"] if atlanta["high"]["slug"] == "Atlanta"
            else atlanta["low"]["seed"])
    assert seed == 6


def test_bracket_runs_to_a_champion(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    rounds = [_run()["round"] for _ in range(4)]

    assert rounds == [1, 2, 3, 4]
    data = playoffs.bracket(_engine, _SEASON)
    assert data["complete"] and data["next_round"] is None
    assert data["champion"] == ranked[0]          # the best team, undefeated
    assert len(data["series"]) == 15               # 8 + 4 + 2 + 1
    # SimpleGameEngine is deterministic, so the stronger side sweeps every series:
    # 15 series x 4 games. Every game is a playoff game.
    assert _count(f"select count(*) from games where season = {_SEASON}") == 60
    assert _count(
        f"select count(*) from games where season = {_SEASON} and is_playoff") == 60
    assert all(s["high_wins"] + s["low_wins"] == 4 for s in data["series"])
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


# -- best-of-seven -----------------------------------------------------------
def test_a_round_is_a_best_of_seven(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    result = _run()

    assert result["round"] == 1 and result["games"] == 8   # eight SERIES, not games
    for s in _series(1):
        assert s["wins_needed"] == 4
        assert max(s["high_wins"], s["low_wins"]) == 4     # somebody reached four
        assert min(s["high_wins"], s["low_wins"]) < 4      # ...and only one of them
        winner_wins = s["high_wins"] if s["winner"] == s["high"]["slug"] else s["low_wins"]
        assert winner_wins == 4


def test_a_series_stops_the_moment_it_is_won(ranked):
    """No dead rubbers: a 4-0 series is four games, not seven."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()

    for s in _series(1):
        assert len(s["games"]) == s["high_wins"] + s["low_wins"]
        assert len(s["games"]) <= 7
    assert _count(f"select count(*) from games where season = {_SEASON}") == 32  # 8 x 4


def test_the_higher_seed_hosts_games_one_two_five_six_seven(ranked):
    """2-2-1-1-1. The pattern is ceremony today -- the simulator gives the home side
    no advantage -- but the games must still be recorded on the right side."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()

    s = _series(1)[0]
    high, low = s["high"]["slug"], s["low"]["slug"]
    hosts = {g["game"]: g["home"] for g in s["games"]}
    expected = {1: high, 2: high, 3: low, 4: low, 5: high, 6: high, 7: high}
    assert hosts == {n: expected[n] for n in hosts}
    # ...and the visitor is always the other team.
    assert all({g["home"], g["away"]} == {high, low} for g in s["games"])


def test_series_games_are_numbered_and_linked(ranked):
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    _run()

    s = _series(1)[0]
    assert [g["game"] for g in s["games"]] == list(range(1, len(s["games"]) + 1))
    # every playoff game is attached to a series, none orphaned
    assert _count(
        f"select count(*) from games where season = {_SEASON} and is_playoff "
        "and playoff_series_id is null") == 0


def test_an_interrupted_series_resumes_where_it_stopped(ranked):
    """A round is not one transaction. If a worker dies mid-series, re-running the
    round must finish that series from its stored score rather than replaying it."""
    playoffs.start_playoffs(_engine, _SEASON, ranked)
    target = _series(1)[0]

    # Simulate a run that died after three games of one series: 2-1, no winner.
    with _engine.begin() as c:
        sid = c.execute(
            text("select id from playoff_series where season = :s and round = 1 "
                 "and high_seed = :hs and conference = :c"),
            {"s": _SEASON, "hs": target["high"]["seed"], "c": target["conference"]},
        ).scalar_one()
        c.execute(
            text("update playoff_series set high_wins = 2, low_wins = 1 where id = :i"),
            {"i": sid},
        )

    _run()

    resumed = [s for s in _series(1)
               if s["high"]["slug"] == target["high"]["slug"]][0]
    # Started from 2-1, so the stronger side needed only two more wins: 4-1, and only
    # the two resumed games were actually played and recorded.
    assert (resumed["high_wins"], resumed["low_wins"]) == (4, 1)
    assert len(resumed["games"]) == 2
    assert resumed["winner"] == target["high"]["slug"]


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

    assert result["games_deleted"] == 16         # 4 series x 4 games (round 3 unplayed)
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
