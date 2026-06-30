"""
Name: injury_simulator.py
Description: Chunk-based injury processing.

    The season is divided into 5 chunks (periods of 11 weeks; see season.PERIODS).
    Injuries are NOT rolled per game -- they are rolled and applied ONCE, at the
    END of each chunk, after all of that chunk's games have been simulated:
      1. Tick every player's active injury down one CHUNK; a player whose injury
         heals loses the injured tag (but is NOT auto-restored to the starting
         lineup -- recovery just makes them an available bench/reserve player the
         manager re-promotes between chunks).
      2. Roll a new injury for each healthy active (starter/bench) player with
         probability == injury_risk * INJURY_CHUNK_RISK_SCALE (the per-game risk
         scaled up because we now roll once per chunk instead of once per game).

    There is no auto-substitution. An injured player simply stays where they are
    in the lineup and contributes nothing in the game simulator (see
    game_simulator), so leaving an injured player in the starting lineup hurts the
    team -- the incentive for the manager to re-arrange during the chunk break.
    Injury DURATION is in chunks (minor=1, moderate=2, major=3).

    RNG is injected (a random.Random) so a season is reproducible.
Author: design sketch
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from handball.domain import Player, Team
from handball.league_views import DEFAULT_RULES, PlayerId, RosterRules, TeamId
from handball.simulation_vars import (
    INJURY_CHUNK_RISK_SCALE,
    MAJOR_INJURIES,
    MAJOR_INJURY_IMPACT_CHANCE,
    MINOR_INJURIES,
    MODERATE_INJURIES,
    injury_severity,
)

# Injury severity mix (ported verbatim from OperationsHandler): mostly minor.
_INJURY_SEVERITY = [(MINOR_INJURIES, 0.6), (MODERATE_INJURIES, 0.3), (MAJOR_INJURIES, 0.1)]


@dataclass
class InjuryEvent:
    kind: str                 # "injury" | "recovery"
    player_id: PlayerId
    player_name: str
    team_id: TeamId
    chunk: int
    injury_type: str = ""
    severity: str = ""
    recovered_chunk: int | None = None


class InjurySimulator:
    def __init__(
        self,
        rng: random.Random | None = None,
        year: int = 0,
        rules: RosterRules = DEFAULT_RULES,
    ) -> None:
        self.rng = rng or random.Random()
        self.year = year
        self.rules = rules
        self.events: list[InjuryEvent] = []

    # -- integration with the orchestrator --------------------------------
    def process_period_end(self, orch, team_ids, *, chunk: int) -> None:
        """Process every team's injuries for the just-finished chunk and persist+
        publish any that changed. Loads through the orchestrator's repo so it sees
        the already-saved game results."""
        for tid in team_ids:
            team = orch.team_repo.load(tid)
            if self.process_team(team, chunk=chunk):
                orch.team_repo.save(team)
                if orch.gateway is not None:
                    orch.gateway.publish(team.public_view())

    # -- the per-team loop (pure on the Team; no I/O) ----------------------
    def process_team(self, team: Team, *, chunk: int) -> bool:
        """Tick injuries one chunk, then roll new ones. Returns whether the team
        changed (so the caller knows whether to persist). No substitution: an
        injured player stays in place and contributes nothing in the sim."""
        changed = False

        # 1. Tick everyone one chunk -- injured players sit in bench/reserves too.
        for p in team.roster():
            was_injured = p.is_injured
            p.tick_injury()
            if was_injured and not p.is_injured:
                self._mark_recovered(team, p, chunk)
                changed = True

        # 2. Roll new injuries for players who took the field this chunk.
        for p in self._active_players(team):
            if p.is_injured or self.rng.random() >= p.injury_risk * INJURY_CHUNK_RISK_SCALE:
                continue
            itype = self._random_injury_type()
            p.injure(self.year, itype)
            severity = injury_severity(itype)
            if severity == "major" and self.rng.random() < MAJOR_INJURY_IMPACT_CHANCE:
                p.apply_injury_impact()
            self.events.append(InjuryEvent(
                "injury", p.id, p.name, team.id, chunk, itype, severity))
            changed = True

        return changed

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _active_players(team: Team) -> list[Player]:
        """Starters + bench (goalies included) -- the players who take the field."""
        out: list[Player] = []
        for group in (team.starters, team.bench):
            for plist in group.values():
                out.extend(plist)
        return out

    def _random_injury_type(self) -> str:
        pools = [pool for pool, _ in _INJURY_SEVERITY]
        weights = [w for _, w in _INJURY_SEVERITY]
        pool = self.rng.choices(pools, weights=weights, k=1)[0]
        return self.rng.choice(pool)

    def _mark_recovered(self, team: Team, player: Player, chunk: int) -> None:
        """Close the most recent open injury event for this player, or log a
        standalone recovery if the injury predates this simulator (e.g. carried
        over from a loaded file)."""
        for event in reversed(self.events):
            if (event.kind == "injury" and event.player_id == player.id
                    and event.team_id == team.id and event.recovered_chunk is None):
                event.recovered_chunk = chunk
                return
        self.events.append(InjuryEvent("recovery", player.id, player.name, team.id, chunk))
