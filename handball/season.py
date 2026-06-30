"""
Name: season.py
Description: Season structure for the redesigned stack -- the part of
    OperationsHandler that turned a schedule into period-by-period simulation,
    reframed as small injected services.

    Two pieces:

      Schedule  -- a pure value object over the week-by-week matchup list that
                   schedule_generator already produces. It knows the 55-week /
                   5-period / 11-week-per-period structure (the magic offsets
                   that were inlined in run_period_simulation) and slices itself
                   by week or period. No I/O, no sheet, no team objects.

      SeasonRunner -- drives one period through the SeasonOrchestrator: pull
                   manager lineups once, then play every game in the period's
                   weeks. Injuries are handled by an injected InjurySimulator
                   (optional), so the runner stays a thin coordinator.

    The legacy run_period_simulation mixed all of this with sheet writes, record
    persistence, report generation, and archiving. Those are separate concerns
    (RecordSink/the orchestrator's publish path already cover persistence); this
    module is only the schedule->games control flow.
Author: design sketch
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from handball.league_views import TeamId
from handball.orchestration import GameResult, SeasonOrchestrator

if TYPE_CHECKING:
    from handball.injury_simulator import InjurySimulator

Matchup = tuple[TeamId, TeamId]

PERIODS = 5
WEEKS_PER_PERIOD = 11
REGULAR_SEASON_WEEKS = PERIODS * WEEKS_PER_PERIOD  # 55


@dataclass(frozen=True)
class Schedule:
    """Immutable week-by-week schedule. weeks[w] is the matchups played in week
    w+1; each matchup is an (home_id, away_id) pair. Week and period accessors
    are 1-indexed to match how managers/operators talk about the season."""

    weeks: tuple[tuple[Matchup, ...], ...]

    # -- constructors ------------------------------------------------------
    @classmethod
    def from_weeks(cls, weeks: list[list[Matchup]]) -> "Schedule":
        return cls(tuple(tuple(week) for week in weeks))

    @classmethod
    def from_generator_dict(cls, data: dict) -> "Schedule":
        """Adapt ScheduleGenerator.to_json_serializable() output. Each week game
        is {"team1", "team2", "matchup_type"}; we keep only the pairing (matchup
        type is a scheduling detail the simulator doesn't need)."""
        weeks = [
            [(str(g["team1"]), str(g["team2"])) for g in week]
            for week in data["weeks"]
        ]
        return cls.from_weeks(weeks)

    @classmethod
    def round_robin(cls, team_ids: list[TeamId]) -> "Schedule":
        """Single round-robin via the circle method: every pair plays once, and
        no team plays twice in the same week. Handy for offline tests without the
        OR-Tools solver. With an odd count, one team byes each week."""
        ts = list(team_ids)
        if len(ts) % 2:
            ts.append(None)  # bye marker
        n = len(ts)
        weeks: list[list[Matchup]] = []
        for _ in range(n - 1):
            pairs = [
                (ts[i], ts[n - 1 - i])
                for i in range(n // 2)
                if ts[i] is not None and ts[n - 1 - i] is not None
            ]
            weeks.append(pairs)
            ts = [ts[0]] + [ts[-1]] + ts[1:-1]  # rotate, fixing element 0
        return cls.from_weeks(weeks)

    # -- accessors ---------------------------------------------------------
    @property
    def num_weeks(self) -> int:
        return len(self.weeks)

    def week(self, n: int) -> tuple[Matchup, ...]:
        if n < 1 or n > self.num_weeks:
            raise IndexError(f"week {n} out of range 1..{self.num_weeks}")
        return self.weeks[n - 1]

    def period(self, p: int, weeks_per_period: int = WEEKS_PER_PERIOD) -> list[Matchup]:
        """All matchups in period `p` (1..PERIODS), flattened across its weeks.
        Weeks beyond the schedule's length are silently skipped, so a short
        (test) schedule still works."""
        if p < 1 or p > PERIODS:
            raise ValueError(f"period must be in 1..{PERIODS}, got {p}")
        start = (p - 1) * weeks_per_period + 1
        end = p * weeks_per_period
        out: list[Matchup] = []
        for w in range(start, end + 1):
            if 1 <= w <= self.num_weeks:
                out.extend(self.weeks[w - 1])
        return out


class SeasonRunner:
    """Coordinates a SeasonOrchestrator across the season's period/week
    structure. Persistence, publishing, and record-keeping live in the
    orchestrator; injuries (if any) in the injected InjurySimulator."""

    def __init__(
        self,
        orchestrator: SeasonOrchestrator,
        schedule: Schedule,
        injuries: "InjurySimulator | None" = None,
    ) -> None:
        self.orch = orchestrator
        self.schedule = schedule
        self.injuries = injuries

    def run_week(self, n: int) -> list[GameResult]:
        """Pull lineups for the week's teams, then play each game. Injuries are a
        chunk-level event (see run_period); a single week never rolls them."""
        matchups = list(self.schedule.week(n))
        for tid in sorted({t for pair in matchups for t in pair}):
            self.orch.apply_manager_lineup(tid)
        results = []
        for home, away in matchups:
            results.append(self._play_game(home, away, week=n))
        return results

    def run_period(self, p: int) -> list[GameResult]:
        """Pull every participating team's lineup once at the start of the
        period (the mailbox cadence: managers edit between periods), play every
        game across the period's weeks in order, then -- once the whole chunk is
        done -- roll and apply injuries for the period."""
        weeks_per_period = WEEKS_PER_PERIOD
        start = (p - 1) * weeks_per_period + 1
        end = min(p * weeks_per_period, self.schedule.num_weeks)

        all_matchups = self.schedule.period(p)
        team_ids = sorted({t for pair in all_matchups for t in pair})
        for tid in team_ids:
            self.orch.apply_manager_lineup(tid)

        results: list[GameResult] = []
        for w in range(start, end + 1):
            for home, away in self.schedule.week(w):
                results.append(self._play_game(home, away, week=w))

        if self.injuries is not None:
            self.injuries.process_period_end(self.orch, team_ids, chunk=p)
        return results

    def _play_game(self, home: TeamId, away: TeamId, week: int) -> GameResult:
        return self.orch.simulate_matchup(home, away, week=week)
