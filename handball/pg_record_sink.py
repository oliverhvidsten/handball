"""
Name: pg_record_sink.py
Description: Postgres-backed RecordSink. Each finished game becomes one `games`
    row plus one `player_game_lines` row per player who has a line in the
    GameResult -- the fully-relational replacement for appending to every
    Player.current_season_log. Season aggregates (leaderboards, career totals)
    are then queried from the player_season_stats view, never recomputed in app.

    Season is fixed per sink (a batch run simulates one season); week is optional.
    Each line's team_id is resolved from the players table, which the orchestrator
    has already saved before calling record_game (see SeasonOrchestrator.
    simulate_matchup), so home/away need not be threaded through GameResult.
Author: relational backend
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.db import get_engine
from handball.orchestration import GameResult


class PostgresRecordSink:
    def __init__(self, engine: Engine | None = None, *, season: int = 0):
        self.engine = engine or get_engine()
        self.season = season

    def record_game(self, result: GameResult, *, week: int | None = None) -> None:
        with self.engine.begin() as conn:
            home = self._team_uuid(conn, result.home_id)
            away = self._team_uuid(conn, result.away_id)
            game_id = conn.execute(
                text(
                    "insert into games (season, week, home_team_id, away_team_id, "
                    "home_score, away_score, went_to_overtime) "
                    "values (:season, :week, :home, :away, :hs, :as_, :ot) returning id"
                ),
                {"season": self.season, "week": week, "home": home, "away": away,
                 "hs": result.home_score, "as_": result.away_score,
                 "ot": result.went_to_overtime},
            ).scalar_one()

            for legacy_id, line in result.player_lines.items():
                prow = conn.execute(
                    text("select id, team_id from players where legacy_id = :lid"),
                    {"lid": legacy_id},
                ).first()
                if prow is None:
                    continue  # unknown player (e.g. reference engine with no DB rows)
                conn.execute(
                    text(
                        "insert into player_game_lines (game_id, player_id, team_id, season, "
                        "goals, shots, saves, goals_allowed, performance) "
                        "values (:g, :p, :tm, :season, :goals, :shots, :saves, :ga, :perf)"
                    ),
                    {"g": game_id, "p": prow[0], "tm": prow[1], "season": self.season,
                     "goals": line.get("goals", 0), "shots": line.get("shots", 0),
                     "saves": line.get("saves", 0), "ga": line.get("goals_allowed", 0),
                     "perf": line.get("performance")},
                )

    @staticmethod
    def _team_uuid(conn, slug: str):
        row = conn.execute(text("select id from teams where slug = :s"), {"s": slug}).first()
        if row is None:
            raise KeyError(f"no team {slug!r} in database")
        return row[0]
