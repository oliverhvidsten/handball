"""
Offseason rollover ("advance season") against the dev Postgres. Builds a controlled
fixture directly via SQL so award winners / FA / aging outcomes are unambiguous. Skips
when Postgres is unavailable. Local DB only (truncates).
"""
import pytest
from sqlalchemy import text

from handball import offseason
from handball.db import get_engine, is_local_db

try:
    _engine = get_engine()
    with _engine.connect() as _c:
        _c.execute(text("select 1 from teams limit 1"))
    _PG_OK = is_local_db()
except Exception:  # noqa: BLE001
    _PG_OK = False

pytestmark = pytest.mark.skipif(not _PG_OK, reason="Postgres dev DB not available/migrated")

_TABLES = "teams players injuries awards games player_game_lines draft_picks season_state"
_SEASON = 2026


@pytest.fixture(autouse=True)
def _clean_db():
    with _engine.begin() as c:
        c.execute(text(f"truncate {_TABLES.replace(' ', ', ')} restart identity cascade"))
    yield


def _team(conn, slug: str, wins: int = 0) -> str:
    return str(conn.execute(
        text("insert into teams (slug, name, wins) values (:s, :s, :w) returning id"),
        {"s": slug, "w": wins},
    ).scalar_one())


# Numeric columns Player progression needs; the rest can stay NULL.
def _player(conn, legacy_id, team_id, position, *, age=25, yil=0, yr=3,
            off=5.0, deff=5.0, gk=0.1):
    return str(conn.execute(
        text("insert into players (legacy_id, team_id, name, position, age, years_in_league, "
             "years_remaining, offense, defense, goalie_skill, max_offense, max_defense, "
             "max_goalie_skill, variance, peak_age, decline_age, decline_rate) "
             "values (:lid, cast(:tid as uuid), :lid, cast(:pos as player_position), :age, :yil, "
             ":yr, :off, :deff, :gk, 9.0, 9.0, 9.0, 0.5, 27, 30, 0.15) returning id"),
        {"lid": legacy_id, "tid": team_id, "pos": position, "age": age, "yil": yil,
         "yr": yr, "off": off, "deff": deff, "gk": gk},
    ).scalar_one())


def _line(conn, game_id, player_uuid, team_id, *, goals=0, saves=0, perf=0.0):
    conn.execute(
        text("insert into player_game_lines (game_id, player_id, team_id, season, goals, "
             "shots, saves, goals_allowed, performance) "
             "values (cast(:g as uuid), cast(:p as uuid), cast(:tm as uuid), :s, :goals, 0, "
             ":saves, 0, :perf)"),
        {"g": game_id, "p": player_uuid, "tm": team_id, "s": _SEASON,
         "goals": goals, "saves": saves, "perf": perf},
    )


def test_advance_season_full_rollover():
    with _engine.begin() as c:
        a = _team(c, "Alpha", wins=5)
        b = _team(c, "Bravo", wins=1)
        # award fixture (all on Alpha): distinct leaders
        mvp = _player(c, "a-mvp", a, "Forward", yil=5, off=6.0)          # top performance
        scorer = _player(c, "a-scorer", a, "Forward", yil=3)            # top goals
        goalie = _player(c, "a-gk", a, "Goalie", yil=2, gk=6.0)         # only/most saves
        rookie = _player(c, "a-roy", a, "Forward", yil=0)              # top rookie performance
        # a Bravo player whose contract is up -> free agent after aging
        expiring = _player(c, "b-fa", b, "Defense", yr=0)
        keep = _player(c, "b-keep", b, "Defense", yr=4)
        g = str(c.execute(
            text("insert into games (season, week, home_team_id, away_team_id, home_score, "
                 "away_score) values (:s, 1, cast(:a as uuid), cast(:b as uuid), 3, 2) returning id"),
            {"s": _SEASON, "a": a, "b": b},
        ).scalar_one())
        _line(c, g, mvp, a, goals=10, perf=100.0)
        _line(c, g, scorer, a, goals=50, perf=20.0)
        _line(c, g, goalie, a, saves=30, perf=30.0)
        _line(c, g, rookie, a, goals=5, perf=40.0)

    summary = offseason.advance_season(_engine, _SEASON, ["Alpha", "Bravo"])  # best->worst

    assert summary["new_season"] == _SEASON + 1
    assert summary["awards"] == {
        offseason.AWARD_MVP: "a-mvp",
        offseason.AWARD_TOP_SCORER: "a-scorer",
        offseason.AWARD_TOP_GOALIE: "a-gk",
        offseason.AWARD_ROOKIE: "a-roy",
    }

    with _engine.connect() as c:
        # awards persisted with season + plain-text label
        n_awards = c.execute(text("select count(*) from awards where season=:s"), {"s": _SEASON}).scalar_one()
        assert n_awards == 4

        # draft order for N+1: reverse standings (Bravo first), 2 rounds, pick_number 1..4
        picks = c.execute(
            text("select pick_number, round, t.slug from draft_picks d "
                 "join teams t on t.id = d.holder_team_id where d.season=:s order by pick_number"),
            {"s": _SEASON + 1},
        ).all()
        assert [(p[0], p[2]) for p in picks] == [(1, "Bravo"), (2, "Alpha"), (3, "Bravo"), (4, "Alpha")]

        # aging: age +1, years_remaining -1; a young player's offense grows toward ceiling
        row = c.execute(text("select age, years_remaining, offense from players where legacy_id='a-mvp'")).first()
        assert row[0] == 26 and row[1] == 2 and row[2] > 6.0

        # free agency: expired contract left its team; the multi-year deal stayed
        assert c.execute(text("select team_id from players where legacy_id='b-fa'")).scalar_one() is None
        assert c.execute(text("select team_id from players where legacy_id='b-keep'")).scalar_one() is not None

        # records zeroed; new season opened
        assert c.execute(text("select coalesce(sum(wins+losses+ties),0) from teams")).scalar_one() == 0
        assert c.execute(text("select 1 from season_state where season=:s"), {"s": _SEASON + 1}).first() is not None


def test_retirement_candidates_and_retire():
    with _engine.begin() as c:
        a = _team(c, "Alpha")
        _player(c, "old-1", a, "Forward", age=40)
        _player(c, "old-2", a, "Defense", age=37)
        _player(c, "young", a, "Forward", age=24)

    cands = offseason.retirement_candidates(_engine)
    assert {x["legacy_id"] for x in cands} == {"old-1", "old-2"}     # age > 35 only

    n = offseason.retire_players(_engine, ["old-1"], _SEASON)
    assert n == 1
    with _engine.connect() as c:
        row = c.execute(text("select retired, retired_season, team_id from players where legacy_id='old-1'")).first()
        assert row[0] is True and row[1] == _SEASON and row[2] is None   # flagged + off roster
        # row kept for history
        assert c.execute(text("select count(*) from players where legacy_id='old-1'")).scalar_one() == 1
    # no longer a candidate
    assert "old-1" not in {x["legacy_id"] for x in offseason.retirement_candidates(_engine)}
