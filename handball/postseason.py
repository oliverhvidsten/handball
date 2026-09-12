"""
Name: postseason.py
Description: The draft and playoff phases of OperationsHandler, ported onto the
    redesigned stack as injected, offline-testable services.

    DraftService -- reverse-standings rookie draft. Slots run worst-team-first
        and repeat each round; traded picks are honored via an injected
        pick-ownership map. Each selection builds a domain.Player (reusing the
        existing, tested draft_simulator stat generation, then converting to the
        domain model with a stable id), tagged with the rookie-scale contract its
        overall pick earns (simulation_vars.ROOKIE_SCALE). Returns
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
from handball.repository import TeamRepository
from handball.simulation_vars import ROOKIE_SCALE

# The flat deal every draftee used to get. Retired in favour of ROOKIE_SCALE (the
# rulebook prices a pick by where it fell, which is what makes a traded pick a
# knowable asset), but kept as names because they are still the shape of a "fixed
# terms" deal -- signing_service.FREE_AGENT_CONTRACT_* is the other one -- and
# nothing is gained by making an old import break.
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
    def __init__(self, scale=ROOKIE_SCALE) -> None:
        """`scale` is the rookie-contract table: (first overall, last overall,
        years, $M/yr) bands, as in simulation_vars.ROOKIE_SCALE. It replaces the flat
        rookie_years/rookie_salary this service used to hand out, so the offline
        stack's draft and the live one price a pick the same way -- a #1 pick and a
        #64 pick cannot be the same contract in one draft and not the other."""
        self.scale = scale

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
        from handball.draft_rules import rookie_deal

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
                pid = self._unique_player_id(holder, name, used_ids)
                player = create_draft_player(name, position, id=pid)
                # A rookie deal goes on the books no matter what the holder's payroll
                # is (a team must be able to sign its picks), so there is no cap check
                # here -- update_contract validates the CONTRACT and starts the term
                # clock. A holder pushed over the hard cap becomes a season-start
                # blocker instead; see handball/season_readiness.py. What the deal IS
                # comes off the rookie scale: where you were taken is the contract.
                years, salary = rookie_deal(overall, self.scale)
                player.update_contract(years, salary, rookie=True)

                picks.append(DraftPickResult(
                    round_num=round_num, pick_num=pick_num, overall=overall,
                    holder_team_id=holder, original_team_id=original, player=player,
                ))
        return picks

    @staticmethod
    def _unique_player_id(holder: TeamId, name: str, used_ids: set[str]) -> str:
        """Stable, collision-safe domain id for a draftee: '<team>-<name>'."""
        base = f"{_slug(holder)}-{_slug(name)}"
        pid, n = base, 2
        while pid in used_ids:
            pid, n = f"{base}-{n}", n + 1
        used_ids.add(pid)
        return pid


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


# -- the bracket's pure shape ------------------------------------------------
# Seeding and pairing are just arithmetic on a ranking; the persisted postseason
# (handball/playoffs.py) needs exactly these and has no repository or game engine
# to hand a PlayoffService. They live at module scope so both callers share one
# definition of what the bracket looks like.
def seed_conferences(
    ranked_team_ids: list[TeamId],
    conference_of: Callable[[TeamId], str],
    teams_per_conference: int = 8,
    division_of: Callable[[TeamId], str] | None = None,
) -> dict[str, list[TeamId]]:
    """{conference: [seed1..seedN]} -- each conference's playoff field, seeded.

    DIVISION WINNERS FIRST. The best team in each division takes a top seed, in
    order of the overall ranking among themselves; the rest of the field is the best
    remaining teams in that conference. So a division winner is seeded above a
    wildcard that finished ahead of it -- winning a division is worth something, and
    that is the whole point of having divisions.

    With four divisions per conference and eight seeds, that is seeds 1-4 for the
    winners and 5-8 for the wildcards. The split follows from the numbers rather
    than being hardcoded: however many divisions a conference has, its winners take
    that many top seeds.

    `division_of` is optional -- without it this degrades to pure ranking order,
    which is what the offline stack (with no division map) wants."""
    by_conf: dict[str, list[TeamId]] = {}
    for tid in ranked_team_ids:
        by_conf.setdefault(conference_of(tid), []).append(tid)

    seeded: dict[str, list[TeamId]] = {}
    for conference, teams in by_conf.items():
        if division_of is None:
            seeded[conference] = teams[:teams_per_conference]
            continue
        # `teams` is already best->worst, so the first team seen in a division is
        # that division's winner and the winners come out in ranking order.
        winners, seen = [], set()
        for tid in teams:
            division = division_of(tid)
            if division not in seen:
                seen.add(division)
                winners.append(tid)
        field_ = winners[:teams_per_conference]
        others = [t for t in teams if t not in set(field_)]
        seeded[conference] = field_ + others[: teams_per_conference - len(field_)]
    return seeded


def pairings(seeded: list[TeamId]) -> list[tuple[TeamId, TeamId]]:
    """Highest vs lowest: [s1,s2,s3,s4] -> [(s1,s4),(s2,s3)]. Higher seed first
    (hosts)."""
    n = len(seeded)
    return [(seeded[i], seeded[n - 1 - i]) for i in range(n // 2)]


def round_name(remaining: int) -> str:
    """What to call a round with `remaining` teams still alive in a conference."""
    return _ROUND_NAMES.get(remaining, f"Round of {remaining}")


class PlayoffService:
    def __init__(
        self,
        engine: GameEngine,
        conference_of: Callable[[TeamId], str],
        teams_per_conference: int = 8,
        division_of: Callable[[TeamId], str] | None = None,
    ) -> None:
        self.engine = engine
        self.conference_of = conference_of
        self.teams_per_conference = teams_per_conference
        self.division_of = division_of

    def seed(self, ranked_team_ids: list[TeamId]) -> dict[str, list[TeamId]]:
        """{conference: [seed1..seedN]} -- the top teams per conference in
        best->worst order, taken from the overall ranking."""
        return seed_conferences(
            ranked_team_ids, self.conference_of, self.teams_per_conference,
            self.division_of,
        )

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
                label = f"{conference} {round_name(len(remaining))}"
                winners = []
                for high, low in pairings(remaining):
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

    _pairings = staticmethod(pairings)  # retained: tests and callers use it

    def _play(self, teams: dict[TeamId, "object"], high: TeamId, low: TeamId) -> TeamId:
        """One elimination game; the higher seed (home) advances on a tie, so
        there is always a winner regardless of the engine's tie policy."""
        result = self.engine.play(teams[high], teams[low])
        return high if result.home_score >= result.away_score else low
