"""
Name: schedule_repository.py
Description: Persistence for the fixture list and the season-run cursor -- the
    durable state that lets "run the season" work from the website instead of
    only from an in-memory, per-process schedule.

    Two concerns, both keyed by season year:
      - the schedule  (schedule_games rows)  <-> a season.Schedule value object
      - the run cursor (season_state row)    -- periods_run + reproducibility seeds

    Team identity: domain TeamId == teams.slug (see pg_repository). The OR-Tools
    ScheduleGenerator keys matchups by the same team names, so its team1/team2 ARE
    slugs; save_schedule asserts that invariant before writing so a mismatch fails
    loudly rather than persisting fixtures the runner can't load.
Author: season-run wiring
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.season import Schedule


class ScheduleError(RuntimeError):
    """A schedule could not be persisted or loaded coherently."""


def _slug_to_id(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(text("select slug, id from teams")).all()
    return {slug: str(tid) for slug, tid in rows}


def save_schedule(engine: Engine, season: int, generator_dict: dict) -> int:
    """Persist a generated schedule for `season`. Idempotent: replaces any existing
    fixtures for the season. `generator_dict` is ScheduleGenerator.to_json_serializable()
    output -- weeks[w] is a list of {"team1","team2","matchup_type"} with team names
    that must equal the DB slugs. Returns the number of fixtures written."""
    slug_to_id = _slug_to_id(engine)
    weeks = generator_dict["weeks"]

    # Fail loudly if the generator and the DB disagree on the team set, rather than
    # silently persisting fixtures the runner can't resolve to a team.
    sched_teams = {str(g["team1"]) for wk in weeks for g in wk} | {
        str(g["team2"]) for wk in weeks for g in wk
    }
    unknown = sched_teams - set(slug_to_id)
    if unknown:
        raise ScheduleError(
            f"schedule references teams not in the database (by slug): {sorted(unknown)}"
        )

    rows = []
    for i, wk in enumerate(weeks, start=1):
        for g in wk:
            rows.append(
                {
                    "season": season,
                    "week": i,
                    "home": slug_to_id[str(g["team1"])],
                    "away": slug_to_id[str(g["team2"])],
                    "mtype": g.get("matchup_type"),
                }
            )

    with engine.begin() as conn:
        conn.execute(
            text("delete from schedule_games where season = :s"), {"s": season}
        )
        if rows:
            conn.execute(
                text(
                    "insert into schedule_games "
                    "(season, week, home_team_id, away_team_id, matchup_type) "
                    "values (:season, :week, cast(:home as uuid), cast(:away as uuid), :mtype)"
                ),
                rows,
            )
    return len(rows)


def load_schedule(engine: Engine, season: int) -> Schedule:
    """Rebuild a season.Schedule from the persisted fixtures for `season`. Weeks are
    1-indexed and contiguous in schedule_games; gaps would surface as empty weeks."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select sg.week, th.slug as home, ta.slug as away "
                "from schedule_games sg "
                "join teams th on th.id = sg.home_team_id "
                "join teams ta on ta.id = sg.away_team_id "
                "where sg.season = :s "
                "order by sg.week"
            ),
            {"s": season},
        ).mappings().all()

    if not rows:
        raise ScheduleError(f"no persisted schedule for season {season}")

    max_week = max(r["week"] for r in rows)
    weeks: list[list[tuple[str, str]]] = [[] for _ in range(max_week)]
    for r in rows:
        weeks[r["week"] - 1].append((r["home"], r["away"]))
    return Schedule.from_weeks(weeks)


# -- season-run cursor -------------------------------------------------------
def get_season_state(engine: Engine, season: int) -> dict | None:
    """The run cursor for `season`, or None if the season hasn't been initialized."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select season, periods_run, schedule_seed, injury_seed, "
                "schedule_generated, run_status, run_period, run_error, "
                "extract(epoch from (now() - updated_at))::int as run_age_seconds "
                "from season_state where season = :s"
            ),
            {"s": season},
        ).mappings().first()
    return dict(row) if row else None


def init_season_state(
    engine: Engine, season: int, *, schedule_seed: int | None, injury_seed: int | None
) -> None:
    """Create the season_state row (or leave it untouched if it already exists)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into season_state (season, schedule_seed, injury_seed) "
                "values (:s, :ss, :is) on conflict (season) do nothing"
            ),
            {"s": season, "ss": schedule_seed, "is": injury_seed},
        )


def mark_schedule_generated(engine: Engine, season: int, schedule_seed: int | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "update season_state set schedule_generated = true, "
                "schedule_seed = :ss, updated_at = now() where season = :s"
            ),
            {"s": season, "ss": schedule_seed},
        )


def set_run_status(
    engine: Engine,
    season: int,
    status: str,
    *,
    period: int | None = None,
    error: str | None = None,
) -> None:
    """Update the background-run state machine (idle/running/done/error). `period`
    and `error` are set when provided, so a 'running' transition can record the
    target period and an 'error' transition the message."""
    sets = ["run_status = :st", "updated_at = now()"]
    params: dict = {"s": season, "st": status, "p": period, "e": error}
    if period is not None:
        sets.append("run_period = :p")
    sets.append("run_error = :e")  # always set (cleared to NULL on a fresh run)
    with engine.begin() as conn:
        conn.execute(
            text(f"update season_state set {', '.join(sets)} where season = :s"), params
        )


def heartbeat(engine: Engine, season: int) -> None:
    """Touch updated_at while a run is in flight. A 'running' row whose heartbeat
    has gone stale means the worker (web instance) died mid-run -- see reset_run."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "update season_state set updated_at = now() "
                "where season = :s and run_status = 'running'"
            ),
            {"s": season},
        )


def bump_periods_run(engine: Engine, season: int) -> int:
    """Advance the cursor by one period after a successful run; returns the new count."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "update season_state set periods_run = periods_run + 1, "
                "updated_at = now() where season = :s returning periods_run"
            ),
            {"s": season},
        ).first()
    if row is None:
        raise ScheduleError(f"no season_state row for season {season}")
    return int(row[0])


def reset_run(engine: Engine, season: int, period: int | None) -> int:
    """Recover from an interrupted/failed period run. Rolls back the partial data of
    `period` so a retry is clean: deletes that period's games (player_game_lines
    cascade) and EXACTLY undoes the W-L-T those games added to each team's record
    (derived from the deleted games' own scores, so no dependence on a recomputed
    baseline). Then clears run_status back to idle. Returns games rolled back.

    Run inside one transaction. `period` None (run died before a period was tagged)
    just clears the status with nothing to roll back."""
    from handball.season import WEEKS_PER_PERIOD

    with engine.begin() as conn:
        rolled = 0
        if period is not None:
            lo = (period - 1) * WEEKS_PER_PERIOD + 1
            hi = period * WEEKS_PER_PERIOD
            games = conn.execute(
                text(
                    "select id, home_team_id, away_team_id, home_score, away_score "
                    "from games where season = :s and week between :lo and :hi"
                ),
                {"s": season, "lo": lo, "hi": hi},
            ).mappings().all()
            for g in games:
                hs, as_ = g["home_score"], g["away_score"]
                if hs is None or as_ is None:
                    continue  # unscored row: no record contribution to undo
                if hs > as_:
                    _dec(conn, g["home_team_id"], "wins")
                    _dec(conn, g["away_team_id"], "losses")
                elif as_ > hs:
                    _dec(conn, g["away_team_id"], "wins")
                    _dec(conn, g["home_team_id"], "losses")
                else:
                    _dec(conn, g["home_team_id"], "ties")
                    _dec(conn, g["away_team_id"], "ties")
            conn.execute(
                text("delete from games where season = :s and week between :lo and :hi"),
                {"s": season, "lo": lo, "hi": hi},
            )
            rolled = len(games)
        conn.execute(
            text(
                "update season_state set run_status = 'idle', run_period = null, "
                "run_error = null, updated_at = now() where season = :s"
            ),
            {"s": season},
        )
    return rolled


def _dec(conn, team_id, col: str) -> None:
    # col is one of the fixed W-L-T column names, never user input.
    conn.execute(
        text(f"update teams set {col} = greatest({col} - 1, 0) where id = :t"),
        {"t": team_id},
    )
