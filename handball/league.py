"""
Name: league.py
Description: The single composition root for the redesigned stack -- the new
    replacement for OperationsHandler.

    OperationsHandler was both the orchestration logic AND the wiring: its
    __init__ opened cred.txt, built a SheetHandler, a RecordKeeper, a
    SeasonArchive, a FreeAgencyHandler, and held mutable in-memory season state,
    and its methods reached for all of them directly. That made it impossible to
    construct (let alone test) without live Google access.

    Here the logic lives in small injected services (orchestration, season,
    injury_*, postseason); LeagueOperations is a thin facade that holds them and
    exposes the season lifecycle (publish, run periods, draft, playoffs,
    standings). All wiring is in ONE place -- the build_* factories at the bottom
    -- so production differs from a test only by which repository / gateway /
    engine are injected.

        build_production_league(...)  -> Google sheet + JSON datafiles + real sim
        (offline)                     -> construct LeagueOperations directly with
                                         InMemoryTeamRepository + FakeSheetGateway
                                         + SimpleGameEngine (see tests)
Author: design sketch
"""
from __future__ import annotations

import random
from typing import Callable, Iterable

from handball.injury_service import InjuryService
from handball.injury_simulator import InjurySimulator
from handball.league_views import DEFAULT_RULES, RosterRules, TeamId
from handball.orchestration import (
    GameResult,
    SeasonOrchestrator,
    build_production_orchestrator,
)
from handball.postseason import Bracket, DraftPickResult, DraftService, PlayoffService
from handball.season import Schedule, SeasonRunner

# Mirrors the legacy OperationsHandler constants so the facade behaves the same.
PLAYOFF_TEAMS_PER_CONFERENCE = 8


def _default_conference_of() -> Callable[[TeamId], str]:
    from handball.schedule_generator import get_conference

    return get_conference


class LeagueOperations:
    """Facade over the season's services. Construct it with a wired
    SeasonOrchestrator and (optionally) the postseason/injury/schedule
    collaborators; the build_* factories below do the production wiring."""

    def __init__(
        self,
        orchestrator: SeasonOrchestrator,
        *,
        schedule: Schedule | None = None,
        injuries: InjurySimulator | None = None,
        draft: DraftService | None = None,
        playoff_engine=None,
        conference_of: Callable[[TeamId], str] | None = None,
        teams_per_conference: int = PLAYOFF_TEAMS_PER_CONFERENCE,
        rules: RosterRules = DEFAULT_RULES,
    ) -> None:
        self.orch = orchestrator
        self.schedule = schedule
        self.injuries = injuries
        self.rules = rules
        self.draft_service = draft or DraftService()
        # Playoffs reuse the orchestrator's engine unless one is given explicitly.
        self._playoff_engine = playoff_engine or orchestrator.engine
        self._conference_of = conference_of  # resolved lazily (avoids ortools import offline)
        self.teams_per_conference = teams_per_conference

    # -- schedule ----------------------------------------------------------
    def set_schedule(self, schedule: Schedule) -> None:
        self.schedule = schedule

    def generate_schedule(self, seed: int | None = None) -> Schedule:
        """Build a full league schedule via the OR-Tools ScheduleGenerator and
        store it. Lazy import so the offline stack never pulls in ortools."""
        from handball.schedule_generator import ScheduleGenerator

        gen = ScheduleGenerator(seed=seed)
        self.schedule = Schedule.from_generator_dict(gen.to_json_serializable())
        return self.schedule

    def _runner(self) -> SeasonRunner:
        if self.schedule is None:
            raise RuntimeError(
                "no schedule set; call generate_schedule() or set_schedule() first"
            )
        return SeasonRunner(self.orch, self.schedule, self.injuries)

    # -- season lifecycle --------------------------------------------------
    def publish_all(self) -> None:
        """Establish the sheet baseline for every team (pre-season)."""
        self.orch.publish_all()

    def run_week(self, n: int) -> list[GameResult]:
        return self._runner().run_week(n)

    def run_period(self, p: int) -> list[GameResult]:
        return self._runner().run_period(p)

    def standings(self) -> list[tuple[TeamId, tuple[int, int, int]]]:
        return self.orch.standings()

    def ranked_team_ids(self) -> list[TeamId]:
        """Best -> worst, the order draft (reversed) and playoff seeding use.
        Derived from the orchestrator's W-L-T standings; richer goal-differential
        tiebreakers can be layered on via the record sink later."""
        return [tid for tid, _ in self.orch.standings()]

    # -- postseason --------------------------------------------------------
    def run_draft(
        self,
        prospects: Iterable[tuple[str, str | None]],
        rounds: int = 2,
        pick_ownership: dict[int, dict[TeamId, TeamId]] | None = None,
    ) -> list[DraftPickResult]:
        return self.draft_service.run(
            self.ranked_team_ids(), prospects, rounds=rounds, pick_ownership=pick_ownership
        )

    def run_playoffs(self) -> Bracket:
        conference_of = self._conference_of or _default_conference_of()
        svc = PlayoffService(
            self._playoff_engine, conference_of, self.teams_per_conference
        )
        return svc.run(self.orch.team_repo, self.ranked_team_ids())


# ---------------------------------------------------------------------------
# Production wiring (the one place that knows about Google + the filesystem).
# ---------------------------------------------------------------------------
def build_production_league(  # pragma: no cover - live wiring
    datafiles_dir,
    sheet_id: str,
    *,
    year: int = 0,
    allow_tie: bool = False,
    seed: int | None = None,
    schedule: Schedule | None = None,
) -> LeagueOperations:
    """Wire the full production facade: JSON datafiles repository + Google sheet
    gateway + the real GameSimulator, plus injuries and the postseason
    services. `seed` makes the injury RNG reproducible."""
    orch = build_production_orchestrator(datafiles_dir, sheet_id, allow_tie=allow_tie)
    injuries = InjurySimulator(InjuryService(DEFAULT_RULES), rng=random.Random(seed), year=year)
    return LeagueOperations(orch, schedule=schedule, injuries=injuries)


def build_production_league_pg(
    *,
    db_url: str | None = None,
    year: int = 0,
    allow_tie: bool = False,
    seed: int | None = None,
    schedule: Schedule | None = None,
) -> LeagueOperations:
    """Postgres-backed production facade -- the relational replacement for
    build_production_league. Source of truth + stats live in Postgres
    (PostgresTeamRepository + PostgresRecordSink), and there is NO SheetGateway:
    managers edit lineups/trades through the website/API, so the batch sim needs
    no inbox. `db_url` overrides $HANDBALL_DB_URL; `year` tags the season the
    record sink writes."""
    from handball.db import get_engine
    from handball.orchestration import GameSimulatorAdapter
    from handball.pg_record_sink import PostgresRecordSink
    from handball.pg_repository import PostgresTeamRepository

    engine = get_engine(db_url)
    orch = SeasonOrchestrator(
        team_repo=PostgresTeamRepository(engine),
        gateway=None,
        engine=GameSimulatorAdapter(allow_tie=allow_tie),
        record_sink=PostgresRecordSink(engine, season=year),
    )
    injuries = InjurySimulator(InjuryService(DEFAULT_RULES), rng=random.Random(seed), year=year)
    return LeagueOperations(orch, schedule=schedule, injuries=injuries)


def build_production_league_from_cred(  # pragma: no cover - live wiring
    datafiles_dir,
    cred_path: str = "/Users/oliverhvidsten/Documents/handball/cred.txt",
    **kwargs,
) -> LeagueOperations:
    """Same as build_production_league, reading the sheet id from the first line
    of `cred_path` (matches OperationsHandler / the migration script)."""
    from pathlib import Path

    sheet_id = Path(cred_path).read_text().splitlines()[0].strip()
    return build_production_league(datafiles_dir, sheet_id, **kwargs)
