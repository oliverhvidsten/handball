"""
Name: orchestration.py
Description: The season orchestrator after constructor injection -- the redesign
    of OperationsHandler. It depends on three injected abstractions and NOTHING
    concrete:

        TeamRepository   -- load/save the model (source of truth)
        SheetGateway     -- manager inbox (arrangements) + outbox (projection)
        GameEngine       -- play(home, away) -> GameResult

    The current OperationsHandler instead opens cred.txt, builds a SheetHandler,
    and reconstructs teams via TeamInfo.from_sheet + Team.from_TeamInfo -- so it
    cannot run without live Google access and cannot be unit-tested. Here the
    same orchestration runs fully offline against InMemoryTeamRepository +
    FakeSheetGateway + a deterministic engine.

    GameSimulator was already pure (it takes two Teams, touches no sheet), so it
    needs no injection itself -- it just gets wrapped by a GameEngine adapter so
    the orchestrator depends on the abstraction, not the concrete simulator.
Author: design sketch
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from handball.domain import Team
from handball.league_views import DEFAULT_RULES, PlayerId, RosterRules, TeamId
from handball.repository import TeamRepository
from handball.sheet_gateway import SheetGateway


# ---------------------------------------------------------------------------
# Game-engine seam.
# ---------------------------------------------------------------------------
@dataclass
class GameResult:
    home_id: TeamId
    away_id: TeamId
    home_score: int
    away_score: int
    went_to_overtime: bool = False
    # player id -> this game's line. Keys: goals, shots, saves, goals_allowed,
    # performance. Field players carry 0 saves/goals_allowed; goalies carry the
    # halftime-split goalie stats. Empty for engines that don't model lineups
    # (e.g. SimpleGameEngine).
    player_lines: dict[PlayerId, dict] = field(default_factory=dict)

    @property
    def outcome_for_home(self) -> str:
        if self.home_score > self.away_score:
            return "W"
        if self.home_score < self.away_score:
            return "L"
        return "T"


@runtime_checkable
class GameEngine(Protocol):
    def play(self, home: Team, away: Team) -> GameResult:
        """Simulate a game. CONTRACT: mutates both teams in place -- applies the
        W/L/T result to their records and appends per-player season-log entries --
        and returns a GameResult summarizing it. The orchestrator then persists
        and publishes; it does NOT touch records itself."""
        ...


class SimpleGameEngine:
    """Deterministic reference engine so the orchestrator runs end-to-end
    offline. Score = starter attack vs opponent goalie -- enough to make a
    stronger lineup win, which lets tests assert that manager edits matter.

    NOT the real handball simulation; that's GameSimulatorAdapter's job."""

    @staticmethod
    def _attack(team: Team) -> float:
        return sum(p.offense for pos in ("Forward", "Midfielder") for p in team.starters[pos])

    @staticmethod
    def _keeper(team: Team) -> float:
        return team.starters["Goalie"][0].goalie_skill if team.starters["Goalie"] else 0.0

    def play(self, home: Team, away: Team) -> GameResult:
        home_score = max(0, round(self._attack(home) - self._keeper(away)))
        away_score = max(0, round(self._attack(away) - self._keeper(home)))
        result = GameResult(home.id, away.id, home_score, away_score)
        outcome = result.outcome_for_home
        home.record_result(outcome)
        away.record_result({"W": "L", "L": "W", "T": "T"}[outcome])
        return result


class GameSimulatorAdapter:
    """Production GameEngine: runs the real handball.game_simulator.GameSimulator
    directly on domain.Team, then maps get_game_summary() -> GameResult.

    The import is lazy so constructing the orchestrator with a different engine
    (e.g. SimpleGameEngine) never pulls in the simulator / its deps."""

    def __init__(self, allow_tie: bool = False):
        self.allow_tie = allow_tie

    def play(self, home: Team, away: Team) -> GameResult:
        from handball.game_simulator import GameSimulator

        sim = GameSimulator(home, away, allow_tie=self.allow_tie)
        sim.simulate_game()           # runs the game, calls postgame(), appends season logs
        s = sim.get_game_summary()

        # Build complete per-player lines keyed by stable PlayerId. The simulator
        # has just appended this game's entry to every player's season log, so the
        # last element of each list IS this game's line -- a robust, id-keyed
        # source (no fragile name lookups, and goalie saves/goals-allowed land on
        # the right player). Non-goalies have empty saves/goals_allowed lists.
        def lines_for(team: Team) -> dict[PlayerId, dict]:
            out: dict[PlayerId, dict] = {}
            for p in team.roster():
                log = p.current_season_log
                out[p.id] = {
                    "goals": int(log["goals"][-1]) if log["goals"] else 0,
                    "shots": int(log["shots_taken"][-1]) if log["shots_taken"] else 0,
                    "saves": int(log["saves"][-1]) if log["saves"] else 0,
                    "goals_allowed": int(log["goals_allowed"][-1]) if log["goals_allowed"] else 0,
                    "performance": float(log["performances"][-1]) if log["performances"] else 0.0,
                }
            return out

        lines = {**lines_for(home), **lines_for(away)}

        return GameResult(
            home_id=home.id,
            away_id=away.id,
            home_score=int(s["home_score"]),
            away_score=int(s["away_score"]),
            went_to_overtime=bool(s.get("went_to_overtime", False)),
            player_lines=lines,
        )


# ---------------------------------------------------------------------------
# Record sink seam (where finished games are persisted for stats).
# ---------------------------------------------------------------------------
@runtime_checkable
class RecordSink(Protocol):
    def record_game(self, result: GameResult) -> None: ...


class InMemoryRecordSink:
    def __init__(self) -> None:
        self.games: list[GameResult] = []

    def record_game(self, result: GameResult) -> None:
        self.games.append(result)


# ---------------------------------------------------------------------------
# The orchestrator.
# ---------------------------------------------------------------------------
class SeasonOrchestrator:
    def __init__(
        self,
        team_repo: TeamRepository,
        gateway: SheetGateway | None,
        engine: GameEngine,
        record_sink: RecordSink | None = None,
        rules: RosterRules = DEFAULT_RULES,
    ) -> None:
        # gateway is None when the manager inbox/outbox lives elsewhere (the
        # website applies validated arrangements directly via the API). The
        # batch sim then needs no Sheet: publish/read become no-ops.
        self.team_repo = team_repo
        self.gateway = gateway
        self.engine = engine
        self.record_sink = record_sink or InMemoryRecordSink()
        self.rules = rules

    def publish_all(self) -> None:
        """Establish the sheet baseline for every team (pre-season / after any
        out-of-band roster change). No-op when there is no gateway."""
        if self.gateway is None:
            return
        for team in self.team_repo.load_all():
            self.gateway.publish(team.public_view())

    def apply_manager_lineup(self, team_id: TeamId) -> bool:
        """Pull a manager's lineup edit from the sheet: diff vs baseline, and if
        changed validate + apply + persist + republish. Returns whether an edit
        was applied. Invalid edits raise ArrangementError and leave the team
        untouched (atomic). No-op (returns False) when there is no gateway --
        edits then arrive live through the API, not an inbox poll."""
        if self.gateway is None:
            return False
        team = self.team_repo.load(team_id)
        on_sheet = self.gateway.read_arrangement(team_id)
        if on_sheet == team.arrangement():
            return False
        team.apply_arrangement(on_sheet, self.rules)   # validates; atomic
        self.team_repo.save(team)
        self.gateway.publish(team.public_view())
        return True

    def simulate_matchup(self, home_id: TeamId, away_id: TeamId) -> GameResult:
        home = self.team_repo.load(home_id)
        away = self.team_repo.load(away_id)
        result = self.engine.play(home, away)   # mutates records + season logs

        for team in (home, away):
            self.team_repo.save(team)
            if self.gateway is not None:
                self.gateway.publish(team.public_view())
        self.record_sink.record_game(result)
        return result

    def standings(self) -> list[tuple[TeamId, tuple[int, int, int]]]:
        """Current league table from the repo, sorted by wins desc then losses
        asc. Includes every team (unplayed teams show 0-0-0)."""
        teams = self.team_repo.load_all()
        return sorted(((t.id, t.record) for t in teams), key=lambda x: (-x[1][0], x[1][1]))

    def run_period(self, matchups: list[tuple[TeamId, TeamId]]) -> list[GameResult]:
        """One period: first pull every team's manager lineup edits, then play
        all games. (Pull-then-play matches the mailbox cadence: managers edit
        between periods, we read once, then simulate.)"""
        for team_id in sorted({t for pair in matchups for t in pair}):
            self.apply_manager_lineup(team_id)
        return [self.simulate_matchup(h, a) for h, a in matchups]


# ---------------------------------------------------------------------------
# Production wiring (one place; everything else is injected).
# ---------------------------------------------------------------------------
def build_production_orchestrator(datafiles_dir, sheet_id: str, allow_tie: bool = False) -> SeasonOrchestrator:  # pragma: no cover
    from handball.repository import JsonTeamRepository
    from handball.sheet_gateway import GoogleSheetGateway

    return SeasonOrchestrator(
        team_repo=JsonTeamRepository(datafiles_dir),
        gateway=GoogleSheetGateway.from_sheet_id(sheet_id),
        engine=GameSimulatorAdapter(allow_tie=allow_tie),
    )
