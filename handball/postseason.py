"""
Name: postseason.py
Description: The draft and playoff phases of OperationsHandler, ported onto the
    redesigned stack as injected, offline-testable services.

    DraftService -- reverse-standings rookie draft. Slots run worst-team-first
        and repeat each round; traded picks are honored via an injected
        pick-ownership map. Each selection builds a domain.Player (reusing the
        existing, tested draft_simulator stat generation, then converting to the
        domain model with a stable id), tagged with a rookie contract. Returns
        the picks; rostering a draftee onto a team stays a manager action (as in
        the legacy design), so the service never mutates a Team.

    PlayoffService -- top-N-per-conference single elimination. Games run on
        teams loaded fresh from the repository and NEVER saved, so canonical
        regular-season state is untouched (the legacy code deep-copied for the
        same reason). The GameEngine is injected, so playoffs are deterministic
        in tests via SimpleGameEngine.

    Ranking is injected (a best->worst list of team ids), not computed here: the
    orchestrator already owns standings, and richer tiebreakers (goal
    differential) belong with the record sink. Keeping ranking out makes both
    services pure functions of their inputs.
Author: design sketch
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from handball.domain import Player
from handball.league_views import TeamId
from handball.orchestration import GameEngine
from handball.repository import TeamRepository, _player_from_dict

ROOKIE_CONTRACT_YEARS = 3
ROOKIE_CONTRACT_SALARY = 1  # millions


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------------------
# Draft.
# ---------------------------------------------------------------------------
@dataclass
class DraftPickResult:
    round_num: int
    pick_num: int
    overall: int
    holder_team_id: TeamId   # the team that MADE the pick (honors trades)
    original_team_id: TeamId  # whose draft slot it was
    player: Player


class DraftService:
    def __init__(
        self,
        rookie_years: int = ROOKIE_CONTRACT_YEARS,
        rookie_salary: int = ROOKIE_CONTRACT_SALARY,
    ) -> None:
        self.rookie_years = rookie_years
        self.rookie_salary = rookie_salary

    def run(
        self,
        ranked_team_ids: list[TeamId],
        prospects: Iterable[tuple[str, str | None]],
        rounds: int = 2,
        pick_ownership: dict[int, dict[TeamId, TeamId]] | None = None,
    ) -> list[DraftPickResult]:
        """Run the draft. `ranked_team_ids` is best->worst (draft order is its
        reverse). `prospects` yields (name, position|None) -- position None gets
        a random one. `pick_ownership` maps {round: {original_team: holder}};
        absent entries mean a team picks its own slot. Stops early if prospects
        run out."""
        from handball.draft_simulator import assign_random_position, create_draft_player

        pick_ownership = pick_ownership or {}
        order = list(reversed(ranked_team_ids))  # worst picks first
        prospect_iter = iter(prospects)

        picks: list[DraftPickResult] = []
        overall = 0
        used_ids: set[str] = set()
        for round_num in range(1, rounds + 1):
            round_owners = pick_ownership.get(round_num, {})
            for pick_num, original in enumerate(order, start=1):
                try:
                    name, position = next(prospect_iter)
                except StopIteration:
                    return picks
                holder = round_owners.get(original, original)
                overall += 1

                position = position or assign_random_position()
                legacy = create_draft_player(name, position)
                legacy.update_contract(self.rookie_years, self.rookie_salary, rookie=True)
                legacy.years_remaining = self.rookie_years
                player = self._to_domain_player(legacy, holder, used_ids)

                picks.append(DraftPickResult(
                    round_num=round_num, pick_num=pick_num, overall=overall,
                    holder_team_id=holder, original_team_id=original, player=player,
                ))
        return picks

    @staticmethod
    def _to_domain_player(legacy, holder: TeamId, used_ids: set[str]) -> Player:
        """Convert a draft_simulator Player into a domain.Player with a stable,
        collision-safe id (reuses the tested serialization round-trip)."""
        base = f"{_slug(holder)}-{_slug(legacy.name)}"
        pid, n = base, 2
        while pid in used_ids:
            pid, n = f"{base}-{n}", n + 1
        used_ids.add(pid)
        d = legacy.to_dict()
        d["id"] = pid
        return _player_from_dict(d)


# ---------------------------------------------------------------------------
# Playoffs.
# ---------------------------------------------------------------------------
@dataclass
class SeriesResult:
    label: str
    high_seed: TeamId
    low_seed: TeamId
    winner: TeamId


@dataclass
class Bracket:
    series: list[SeriesResult] = field(default_factory=list)
    conference_champions: dict[str, TeamId] = field(default_factory=dict)
    champion: TeamId | None = None


_ROUND_NAMES = {8: "Quarterfinals", 4: "Semifinals", 2: "Conference Final"}


class PlayoffService:
    def __init__(
        self,
        engine: GameEngine,
        conference_of: Callable[[TeamId], str],
        teams_per_conference: int = 8,
    ) -> None:
        self.engine = engine
        self.conference_of = conference_of
        self.teams_per_conference = teams_per_conference

    def seed(self, ranked_team_ids: list[TeamId]) -> dict[str, list[TeamId]]:
        """{conference: [seed1..seedN]} -- the top teams per conference in
        best->worst order, taken from the overall ranking."""
        by_conf: dict[str, list[TeamId]] = {}
        for tid in ranked_team_ids:
            by_conf.setdefault(self.conference_of(tid), []).append(tid)
        return {c: teams[: self.teams_per_conference] for c, teams in by_conf.items()}

    def run(self, repo: TeamRepository, ranked_team_ids: list[TeamId]) -> Bracket:
        """Run single elimination per conference (8->4->2->champion), then a
        final between conference champions (better-ranked hosts). Teams are
        loaded fresh and never saved, so canonical state is untouched."""
        seeded = self.seed(ranked_team_ids)
        needed = {t for teams in seeded.values() for t in teams}
        teams = {tid: repo.load(tid) for tid in needed}  # throwaway copies

        bracket = Bracket()
        for conference, seeds in seeded.items():
            remaining = list(seeds)
            while len(remaining) > 1:
                label = f"{conference} {_ROUND_NAMES.get(len(remaining), f'Round of {len(remaining)}')}"
                winners = []
                for high, low in self._pairings(remaining):
                    winner = self._play(teams, high, low)
                    bracket.series.append(SeriesResult(label, high, low, winner))
                    winners.append(winner)
                remaining = sorted(winners, key=lambda t: seeds.index(t))  # re-seed
            if remaining:
                bracket.conference_champions[conference] = remaining[0]

        rank = {tid: i for i, tid in enumerate(ranked_team_ids)}
        champs = sorted(bracket.conference_champions.values(), key=lambda t: rank.get(t, 1 << 30))
        if len(champs) >= 2:
            winner = self._play(teams, champs[0], champs[1])
            bracket.series.append(SeriesResult("Final", champs[0], champs[1], winner))
            bracket.champion = winner
        elif champs:
            bracket.champion = champs[0]
        return bracket

    @staticmethod
    def _pairings(seeded: list[TeamId]) -> list[tuple[TeamId, TeamId]]:
        """Highest vs lowest: [s1,s2,s3,s4] -> [(s1,s4),(s2,s3)]. Higher seed
        first (hosts)."""
        n = len(seeded)
        return [(seeded[i], seeded[n - 1 - i]) for i in range(n // 2)]

    def _play(self, teams: dict[TeamId, "object"], high: TeamId, low: TeamId) -> TeamId:
        """One elimination game; the higher seed (home) advances on a tie, so
        there is always a winner regardless of the engine's tie policy."""
        result = self.engine.play(teams[high], teams[low])
        return high if result.home_score >= result.away_score else low
