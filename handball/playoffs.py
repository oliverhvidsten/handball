"""
Name: playoffs.py
Description: The persisted postseason -- the bracket the website runs, over the
    Postgres engine (mirrors offseason.py / free_agency.py: pure shape in
    postseason.py, SQL here).

    handball/postseason.py already knew how to seed and pair a bracket, but it ran
    the whole thing in memory in one call and threw the games away. That is the
    right shape for the offline stack and the wrong one for a league of humans, who
    want to see the matchups, set a lineup, and watch a round at a time. So the
    SEEDING and PAIRING are imported from there (one definition of the bracket's
    shape) and everything else -- persistence, cadence, recovery -- lives here.

    Format: ONE GAME decides each matchup, the higher seed hosts, and a tie goes to
    the host (so there is always a winner regardless of the engine's tie policy).
    8 teams per conference: quarterfinals -> semifinals -> conference final, then a
    cross-conference Final. 15 games in all.

    Cadence: one ROUND per commissioner action, run as a background job like a
    regular-season period. Each round's matchups are materialized from the previous
    round's winners the moment that round finishes, so managers can see who they
    play next and fix their lineup before it is run.

    What a playoff game does NOT touch: team W-L. Games are played on team copies
    loaded fresh from the repository and never saved (the same trick the in-memory
    PlayoffService used), so the standings that seeded the bracket stay exactly as
    they were -- which is also what lets a Final seed itself off them, and what
    keeps advance_season's draft order honest. What it DOES touch: games +
    player_game_lines (flagged is_playoff, so box scores exist but the MVP race and
    the Leaders page ignore them) and injuries.

    Injuries tick for the teams that PLAYED the round, treating a round as an injury
    chunk (chunk = PERIODS + round, continuing the regular season's numbering).
    Eliminated teams stop ticking -- their season is over, and their injuries carry
    to the offseason exactly as they would have if the playoffs had not happened.
Author: postseason
"""
from __future__ import annotations

import random

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.league_structure import CONFERENCES, get_conference
from handball.league_views import TeamId
from handball.postseason import pairings, round_name, seed_conferences
from handball.season import PERIODS

TEAMS_PER_CONFERENCE = 8
FINAL_LABEL = "Final"


class PlayoffError(RuntimeError):
    """The postseason was asked for something its current state cannot give."""


# -- bracket shape -----------------------------------------------------------
def conference_rounds(teams_per_conference: int = TEAMS_PER_CONFERENCE) -> int:
    """How many rounds it takes to get one conference down to a champion. Requires
    a power-of-two field: with 8 teams that is 3 (8 -> 4 -> 2 -> 1)."""
    n, rounds = teams_per_conference, 0
    while n > 1:
        if n % 2:
            raise PlayoffError(
                f"teams per conference must be a power of two, got {teams_per_conference}"
            )
        n //= 2
        rounds += 1
    return rounds


def total_rounds(teams_per_conference: int = TEAMS_PER_CONFERENCE) -> int:
    """Conference rounds plus the Final."""
    return conference_rounds(teams_per_conference) + 1


def _label(round_num: int, conference: str | None, teams_per_conference: int) -> str:
    if conference is None:
        return FINAL_LABEL
    remaining = teams_per_conference >> (round_num - 1)
    return f"{conference} {round_name(remaining)}"


# -- reading -----------------------------------------------------------------
def bracket(engine: Engine, season: int, *,
            teams_per_conference: int = TEAMS_PER_CONFERENCE) -> dict:
    """The whole bracket for `season`, ready to render: every series created so far
    (played or not), plus the champion once there is one. Series are ordered by
    round, then conference, then seed, which is reading order for a bracket."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select ps.round, ps.conference, ps.label, ps.high_seed, ps.low_seed, "
                "       hi.slug as high_slug, hi.name as high_name, "
                "       lo.slug as low_slug, lo.name as low_name, "
                "       w.slug  as winner_slug, "
                "       g.id as game_id, g.home_score, g.away_score, g.went_to_overtime "
                "from playoff_series ps "
                "join teams hi on hi.id = ps.high_seed_team_id "
                "join teams lo on lo.id = ps.low_seed_team_id "
                "left join teams w on w.id = ps.winner_team_id "
                "left join games g on g.id = ps.game_id "
                "where ps.season = :s "
                "order by ps.round, ps.conference nulls last, ps.high_seed"
            ),
            {"s": season},
        ).mappings().all()

    series = [
        {
            "round": r["round"],
            "conference": r["conference"],
            "label": r["label"],
            "high": {"slug": r["high_slug"], "name": r["high_name"], "seed": r["high_seed"]},
            "low": {"slug": r["low_slug"], "name": r["low_name"], "seed": r["low_seed"]},
            "winner": r["winner_slug"],
            "played": r["game_id"] is not None,
            "game_id": str(r["game_id"]) if r["game_id"] else None,
            "high_score": r["home_score"],
            "low_score": r["away_score"],
            "went_to_overtime": r["went_to_overtime"],
        }
        for r in rows
    ]
    champ = _champion_from(series)
    return {
        "season": season,
        "started": bool(series),
        "total_rounds": total_rounds(teams_per_conference),
        "rounds_created": max((s["round"] for s in series), default=0),
        "next_round": _next_round_from(series),
        "champion": champ,
        "complete": champ is not None,
        "series": series,
    }


def _next_round_from(series: list[dict]) -> int | None:
    """The lowest round that still has an undecided matchup -- what run_round runs."""
    pending = [s["round"] for s in series if s["winner"] is None]
    return min(pending) if pending else None


def _champion_from(series: list[dict]) -> str | None:
    """The last round's winner, once that round is a single decided series. Written
    this way rather than 'the Final's winner' so a league configured with a single
    conference still crowns its conference champion."""
    if not series:
        return None
    last = max(s["round"] for s in series)
    final = [s for s in series if s["round"] == last]
    if len(final) == 1 and final[0]["winner"]:
        return final[0]["winner"]
    return None


def champion(engine: Engine, season: int) -> str | None:
    """The champion's slug, or None while the bracket is unfinished."""
    return bracket(engine, season)["champion"]


def is_complete(engine: Engine, season: int) -> bool:
    return champion(engine, season) is not None


# -- starting ----------------------------------------------------------------
def start_playoffs(
    engine: Engine,
    season: int,
    ranked_team_ids: list[TeamId],
    *,
    conference_of=None,
    teams_per_conference: int = TEAMS_PER_CONFERENCE,
) -> dict:
    """Seed the bracket from the finished regular season and create round 1.
    `ranked_team_ids` is best->worst -- the SAME ranking advance_season seeds the
    draft from, so the two can never disagree about who finished where.

    Refuses if a bracket already exists for the season: re-seeding a live postseason
    would silently rewrite matchups managers have already seen."""
    conference_of = conference_of or get_conference
    conference_rounds(teams_per_conference)  # validates the field size

    with engine.begin() as conn:
        if conn.execute(
            text("select 1 from playoff_series where season = :s limit 1"), {"s": season}
        ).first():
            raise PlayoffError(f"the season {season} bracket already exists")

        seeded = seed_conferences(ranked_team_ids, conference_of, teams_per_conference)
        short = {c: len(t) for c, t in seeded.items() if len(t) != teams_per_conference}
        if short:
            raise PlayoffError(
                f"every conference needs {teams_per_conference} teams to seed a bracket; got {short}"
            )
        if not seeded:
            raise PlayoffError("no teams to seed")

        rows = []
        for conference in _ordered(seeded):
            seeds = seeded[conference]
            seed_no = {tid: i + 1 for i, tid in enumerate(seeds)}
            for high, low in pairings(seeds):
                rows.append({
                    "conference": conference,
                    "label": _label(1, conference, teams_per_conference),
                    "high": high, "low": low,
                    "high_seed": seed_no[high], "low_seed": seed_no[low],
                })
        _insert_series(conn, season, 1, rows)
    return bracket(engine, season, teams_per_conference=teams_per_conference)


def _ordered(seeded: dict[str, list[TeamId]]) -> list[str]:
    """Conferences in league order (East, then West), with any unrecognized ones
    after them, so a bracket always renders the same way."""
    known = [c for c in CONFERENCES if c in seeded]
    return known + sorted(c for c in seeded if c not in CONFERENCES)


def _insert_series(conn, season: int, round_num: int, rows: list[dict]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            "insert into playoff_series "
            "(season, round, conference, label, high_seed_team_id, low_seed_team_id, "
            " high_seed, low_seed) "
            "select :season, :round, :conference, :label, "
            "       (select id from teams where slug = :high), "
            "       (select id from teams where slug = :low), "
            "       :high_seed, :low_seed"
        ),
        [{"season": season, "round": round_num, **r} for r in rows],
    )


# -- running a round ---------------------------------------------------------
def run_round(
    engine: Engine,
    season: int,
    *,
    game_engine=None,
    injury_seed: int | None = None,
    roll_injuries: bool = True,
    teams_per_conference: int = TEAMS_PER_CONFERENCE,
) -> dict:
    """Play every undecided matchup in the next round, record the games, advance the
    winners, and materialize the round after it. Returns a summary.

    Not run in one transaction: each game is a real simulation of minutes, and a
    failure halfway through should keep the games it already played rather than
    throwing away an hour of compute. What makes that safe is that a partially-run
    round is a legible state -- some series decided, some not -- and re-running the
    round simply picks up the undecided ones. reset_round is there for the operator
    who wants the round wiped instead."""
    from handball.orchestration import GameSimulatorAdapter, SeasonOrchestrator
    from handball.pg_record_sink import PostgresRecordSink
    from handball.pg_repository import PostgresTeamRepository

    round_num = _next_round(engine, season)
    if round_num is None:
        raise PlayoffError(
            "no round to run: the bracket has not been seeded, or the postseason is complete"
        )

    repo = PostgresTeamRepository(engine)
    game_engine = game_engine or GameSimulatorAdapter(allow_tie=False)
    sink = PostgresRecordSink(
        engine, season=season, is_playoff=True, playoff_round=round_num
    )

    played, participants = [], []
    for s in _pending_series(engine, season, round_num):
        # Fresh copies: engine.play mutates records, and these are never saved.
        home = repo.load(s["high_slug"])
        away = repo.load(s["low_slug"])
        result = game_engine.play(home, away)
        game_id = sink.record_game(result)  # week stays NULL -- not a fixture-list game
        winner = (
            s["high_slug"] if result.home_score >= result.away_score else s["low_slug"]
        )
        _decide(engine, s["id"], winner=winner, game_id=game_id)
        played.append({
            "label": s["label"], "high": s["high_slug"], "low": s["low_slug"],
            "high_score": result.home_score, "low_score": result.away_score,
            "winner": winner,
        })
        participants += [s["high_slug"], s["low_slug"]]

    if roll_injuries and participants:
        from handball.injury_simulator import InjurySimulator

        injuries = InjurySimulator(
            rng=random.Random(f"{injury_seed}-playoff-{round_num}"), year=season
        )
        orch = SeasonOrchestrator(
            team_repo=repo, gateway=None, engine=game_engine, record_sink=sink
        )
        # A round is one injury chunk, continuing the regular season's numbering.
        injuries.process_period_end(
            orch, sorted(set(participants)), chunk=PERIODS + round_num
        )

    created = _materialize_next_round(engine, season, round_num, teams_per_conference)
    _mark_round_run(engine, season, round_num)
    return {
        "season": season,
        "round": round_num,
        "games": len(played),
        "results": played,
        "next_round_series": created,
        "champion": champion(engine, season),
    }


def _mark_round_run(engine: Engine, season: int, round_num: int) -> None:
    """Advance the postseason cursor. Set to a value rather than incremented, so a
    re-run that finished a partially-played round lands on the same number."""
    with engine.begin() as conn:
        conn.execute(
            text("update season_state set "
                 "playoff_rounds_run = greatest(playoff_rounds_run, :r), "
                 "updated_at = now() where season = :s"),
            {"s": season, "r": round_num},
        )


def _next_round(engine: Engine, season: int) -> int | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("select min(round) from playoff_series "
                 "where season = :s and winner_team_id is null"),
            {"s": season},
        ).first()
    return row[0] if row and row[0] is not None else None


def _pending_series(engine: Engine, season: int, round_num: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select ps.id, ps.label, ps.conference, ps.high_seed, ps.low_seed, "
                "       hi.slug as high_slug, lo.slug as low_slug "
                "from playoff_series ps "
                "join teams hi on hi.id = ps.high_seed_team_id "
                "join teams lo on lo.id = ps.low_seed_team_id "
                "where ps.season = :s and ps.round = :r and ps.winner_team_id is null "
                "order by ps.conference nulls last, ps.high_seed"
            ),
            {"s": season, "r": round_num},
        ).mappings().all()
    return [dict(r) for r in rows]


def _decide(engine: Engine, series_id, *, winner: str, game_id) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "update playoff_series set "
                "winner_team_id = (select id from teams where slug = :w), game_id = :g "
                "where id = :id"
            ),
            {"w": winner, "g": game_id, "id": series_id},
        )


# -- advancing the bracket ---------------------------------------------------
def _materialize_next_round(
    engine: Engine, season: int, round_num: int, teams_per_conference: int
) -> int:
    """Build the round after `round_num` from its winners. A no-op if that round is
    not finished yet, if the next round already exists (idempotent under a re-run),
    or if the postseason is over. Returns series created."""
    conf_rounds = conference_rounds(teams_per_conference)
    if round_num > conf_rounds:
        return 0  # the Final was the last round

    with engine.begin() as conn:
        if conn.execute(
            text("select 1 from playoff_series where season = :s and round = :r limit 1"),
            {"s": season, "r": round_num + 1},
        ).first():
            return 0
        won = conn.execute(
            text(
                "select ps.conference, w.slug as winner, "
                "       case when ps.winner_team_id = ps.high_seed_team_id "
                "            then ps.high_seed else ps.low_seed end as seed "
                "from playoff_series ps "
                "join teams w on w.id = ps.winner_team_id "
                "where ps.season = :s and ps.round = :r"
            ),
            {"s": season, "r": round_num},
        ).mappings().all()
        pending = conn.execute(
            text("select count(*) from playoff_series "
                 "where season = :s and round = :r and winner_team_id is null"),
            {"s": season, "r": round_num},
        ).scalar_one()
        if pending or not won:
            return 0  # round still in progress

        if round_num < conf_rounds:
            rows = _next_conference_round(won, round_num + 1, teams_per_conference)
        else:
            rows = _final_row(conn, won)
        _insert_series(conn, season, round_num + 1, rows)
    return len(rows)


def _next_conference_round(
    won: list, next_round: int, teams_per_conference: int
) -> list[dict]:
    """Re-seed each conference's survivors and pair them again. Seeds are the
    ORIGINAL conference seeds (a 4-seed that upsets the 1 stays the 4), so a
    re-seeded bracket keeps rewarding the regular season."""
    by_conf: dict[str, list[tuple[int, str]]] = {}
    for r in won:
        by_conf.setdefault(r["conference"], []).append((r["seed"], r["winner"]))

    rows = []
    for conference in _ordered(by_conf):
        survivors = sorted(by_conf[conference])  # best seed first
        seed_no = {slug: seed for seed, slug in survivors}
        order = [slug for _, slug in survivors]
        for high, low in pairings(order):
            rows.append({
                "conference": conference,
                "label": _label(next_round, conference, teams_per_conference),
                "high": high, "low": low,
                "high_seed": seed_no[high], "low_seed": seed_no[low],
            })
    return rows


def _final_row(conn, won: list) -> list[dict]:
    """The Final: the conference champions, hosted by the better regular season.

    Both are usually 1-seeds, so the conference seed cannot break the tie; the
    W-L-T on `teams` can, and it is still the finished regular season's record --
    nothing in the postseason writes to it, and advance_season (which zeroes it) is
    gated on a champion existing. Ordering matches league.ranked_team_ids()."""
    champs = [r["winner"] for r in won]
    if len(champs) < 2:
        return []
    ranked = [
        slug for (slug,) in conn.execute(
            text("select slug from teams where slug = any(:slugs) "
                 "order by wins desc, losses asc, slug"),
            {"slugs": champs},
        ).all()
    ]
    seed_of = {r["winner"]: r["seed"] for r in won}
    high, low = ranked[0], ranked[1]
    return [{
        "conference": None,
        "label": FINAL_LABEL,
        "high": high, "low": low,
        "high_seed": seed_of[high], "low_seed": seed_of[low],
    }]


# -- recovery ----------------------------------------------------------------
def reset_round(engine: Engine, season: int, round_num: int | None = None) -> dict:
    """Roll a round back so it can be re-run: delete its games (player_game_lines
    cascade), undecide its series, and drop every round after it. Defaults to the
    round currently in progress (or the last one played, if none is).

    Injuries rolled during the round are NOT undone -- the same as the regular
    season's reset_run, and for the same reason: an injury is a fact about a player,
    persisted the moment it happened, with no per-round audit trail to unwind."""
    if round_num is None:
        round_num = _next_round(engine, season)
    if round_num is None:
        with engine.connect() as conn:
            round_num = conn.execute(
                text("select max(round) from playoff_series where season = :s"),
                {"s": season},
            ).scalar()
    if round_num is None:
        raise PlayoffError("no bracket to reset")

    with engine.begin() as conn:
        deleted = conn.execute(
            text("delete from games where season = :s and is_playoff = true "
                 "and playoff_round >= :r returning id"),
            {"s": season, "r": round_num},
        ).all()
        conn.execute(
            text("delete from playoff_series where season = :s and round > :r"),
            {"s": season, "r": round_num},
        )
        conn.execute(
            text("update playoff_series set winner_team_id = null, game_id = null "
                 "where season = :s and round = :r"),
            {"s": season, "r": round_num},
        )
        conn.execute(
            text("update season_state set playoff_rounds_run = :n, run_status = 'idle', "
                 "run_error = null, updated_at = now() where season = :s"),
            {"s": season, "n": round_num - 1},
        )
    return {"season": season, "round": round_num, "games_deleted": len(deleted)}


def clear_playoffs(engine: Engine, season: int) -> int:
    """Delete the season's bracket entirely (games included). The escape hatch for a
    commissioner who seeded off the wrong standings; there is no other way back to
    an unseeded postseason."""
    with engine.begin() as conn:
        conn.execute(
            text("delete from games where season = :s and is_playoff = true"), {"s": season}
        )
        rows = conn.execute(
            text("delete from playoff_series where season = :s returning id"), {"s": season}
        ).all()
        conn.execute(
            text("update season_state set playoff_rounds_run = 0, updated_at = now() "
                 "where season = :s"),
            {"s": season},
        )
    return len(rows)
