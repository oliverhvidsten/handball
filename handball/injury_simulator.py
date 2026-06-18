"""
Name: injury_simulator.py
Description: The per-game injury loop from OperationsHandler._simulate_injuries,
    ported onto the new stack.

    After each game the two teams that played are processed:
      1. Tick every player's active injury one game; a player whose injury heals
         loses the injured tag (but is NOT auto-restored to the starting lineup
         -- recovery just makes them an available bench/reserve player, matching
         the legacy "managers re-promote between periods" rule).
      2. Roll a new injury for each healthy active (starter/bench) player with
         probability == their injury_risk.
      3. Substitute injured starters via the InjuryService (next-man-up as an
         arrangement edit), and degrade development on a major injury.

    Two differences from legacy, both forced by the redesign:
      - Substitution is an arrangement computation routed through validate(), not
        in-place list surgery, so the lineup is provably legal afterward.
      - The exact-caps model cannot field an injured starter with no healthy
        same-position depth. When a roll would do that (a whole position wiped
        out -- vanishingly rare at injury_risk ~ 1e-3), the injury is DEFERRED
        (the player plays on) so every persisted lineup stays legal. Legacy let
        the player "play hurt" in the same situation; here that state is illegal,
        so deferral is the faithful analogue.

    RNG is injected (a random.Random) so a season is reproducible; injury
    DURATION is still sampled inside InjuryReport.add via numpy, so tests that
    want full determinism also seed np.random.
Author: design sketch
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from handball.domain import Player, Team
from handball.injury_service import InjuryError, InjuryService
from handball.league_views import DEFAULT_RULES, PlayerId, RosterRules, TeamId
from handball.simulation_vars import (
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
    kind: str                 # "injury" | "recovery" | "deferred"
    player_id: PlayerId
    player_name: str
    team_id: TeamId
    week: int
    injury_type: str = ""
    severity: str = ""
    recovered_week: int | None = None


class InjurySimulator:
    def __init__(
        self,
        injury_service: InjuryService | None = None,
        rng: random.Random | None = None,
        year: int = 0,
        rules: RosterRules = DEFAULT_RULES,
    ) -> None:
        self.injury_service = injury_service or InjuryService(rules)
        self.rng = rng or random.Random()
        self.year = year
        self.rules = rules
        self.events: list[InjuryEvent] = []

    # -- integration with the orchestrator --------------------------------
    def process_matchup(self, orch, home_id: TeamId, away_id: TeamId, *, week: int) -> None:
        """Load both post-game teams, process injuries, and persist+publish any
        that changed. Loads through the orchestrator's repo so it sees the
        already-saved game results."""
        for tid in (home_id, away_id):
            team = orch.team_repo.load(tid)
            if self.process_team(team, week=week):
                orch.team_repo.save(team)
                orch.gateway.publish(team.public_view())

    # -- the per-team loop (pure on the Team; no I/O) ----------------------
    def process_team(self, team: Team, *, week: int) -> bool:
        """Tick injuries, roll new ones, substitute. Returns whether the team
        changed (so the caller knows whether to persist)."""
        changed = False

        # 1. Tick everyone -- injured players sit in reserves/bench too.
        for p in team.roster():
            was_injured = p.is_injured
            p.tick_injury()
            if was_injured and not p.is_injured:
                self._mark_recovered(team, p, week)
                changed = True

        # 2. Roll new injuries for players who actually played.
        for p in self._active_players(team):
            if p.is_injured or self.rng.random() >= p.injury_risk:
                continue
            itype = self._random_injury_type()
            p.injure(self.year, itype)
            try:
                self.injury_service.reconcile(team)   # next-man-up, validated
            except InjuryError:
                # Whole position lost: defer rather than persist an illegal lineup.
                self._revert_injury(p)
                self.events.append(InjuryEvent(
                    "deferred", p.id, p.name, team.id, week, itype, injury_severity(itype)))
                continue
            severity = injury_severity(itype)
            if severity == "major" and self.rng.random() < MAJOR_INJURY_IMPACT_CHANCE:
                p.apply_injury_impact()
            self.events.append(InjuryEvent(
                "injury", p.id, p.name, team.id, week, itype, severity))
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

    def _mark_recovered(self, team: Team, player: Player, week: int) -> None:
        """Close the most recent open injury event for this player, or log a
        standalone recovery if the injury predates this simulator (e.g. carried
        over from a loaded file)."""
        for event in reversed(self.events):
            if (event.kind == "injury" and event.player_id == player.id
                    and event.team_id == team.id and event.recovered_week is None):
                event.recovered_week = week
                return
        self.events.append(InjuryEvent("recovery", player.id, player.name, team.id, week))

    @staticmethod
    def _revert_injury(player: Player) -> None:
        """Undo a just-rolled injury (used when it can't be legally substituted).
        reconcile() raised before mutating the team, so only the player's injury
        record needs unwinding."""
        log = player.injury_log
        if log.injuries:
            log.injuries.pop()
        log.active_injury = False
        player.is_injured = False
