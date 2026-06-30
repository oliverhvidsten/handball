"""
Name: league_views.py
Description: Pure value types for the manager-facing (public) projection and the
    manager-editable inbox (arrangement). These have NO I/O and import nothing
    heavy, so they are safe to use in fully offline / no-credential contexts.

    Design intent:
      - PlayerPublicView / TeamPublicView are ONE-WAY projections of the full
        domain model. They contain only manager-visible fields. There is no
        inverse (no `update_from_Player`) -- the domain model is the single
        source of truth and these are recomputed each publish.
      - TeamArrangement is the entire manager-editable "inbox": which player
        (by stable id) sits in which slot. Reading the sheet yields one of
        these; nothing else flows back from the sheet.
Author: design sketch
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Stable identifiers. Players/teams are matched by id, never by name -- this is
# what removes the name-matching fragility in the current sheet sync.
PlayerId = str
TeamId = str
CoachId = str

POSITIONS = ("Forward", "Midfielder", "Defense", "Goalie")

# A team's three coaching posts. Order is the canonical HC/OC/DC ordering used by
# Team.coaches (the denormalized [HC, OC, DC] name list) and the sheet layout.
COACH_ROLES = ("HC", "OC", "DC")


@dataclass(frozen=True)
class CoachTenure:
    """One continuous stint a coach held: a team + role over a season range.
    end_season is None while the stint is current (the coach still holds the
    post). This is the unit of a coach's career history."""
    team_id: TeamId          # == teams.slug
    role: str                # one of COACH_ROLES
    start_season: int
    end_season: int | None = None   # None == current


@dataclass(frozen=True)
class RosterRules:
    """Roster shape, in one place instead of scattered 3/3/3/1 magic numbers."""
    starter_caps: dict[str, int]
    bench_caps: dict[str, int]
    reserve_max: int
    max_roster: int

    @property
    def positions(self) -> tuple[str, ...]:
        return tuple(self.starter_caps.keys())


# Mirrors the current league: 10 starters + 7 bench + 4 reserves = 21.
DEFAULT_RULES = RosterRules(
    starter_caps={"Forward": 3, "Midfielder": 3, "Defense": 3, "Goalie": 1},
    bench_caps={"Forward": 2, "Midfielder": 2, "Defense": 2, "Goalie": 1},
    reserve_max=4,
    max_roster=21,
)


@dataclass(frozen=True)
class PlayerPublicView:
    """The only player fields a manager is allowed to see. Hidden stats
    (max_*, peak_age, decline_rate, injury_risk, variance, season logs...)
    deliberately do not appear here -- this dataclass IS the visibility
    boundary."""
    id: PlayerId
    name: str
    position: str
    age: int
    contract: str          # human-readable string, e.g. "3/$12M"
    injured: bool
    offense: float
    defense: float
    goalie_skill: float


@dataclass(frozen=True)
class TeamPublicView:
    """Everything published to a team's sheet. Built by Team.public_view()."""
    id: TeamId
    name: str
    coaches: list[str]                              # [HC, OC, DC]
    starters: dict[str, list[PlayerPublicView]]     # position -> ordered players
    bench: dict[str, list[PlayerPublicView]]
    reserves: list[PlayerPublicView]
    record: tuple[int, int, int]                    # (W, L, T)
    total_salaries: int

    def all_players(self) -> list[PlayerPublicView]:
        out: list[PlayerPublicView] = []
        for group in (self.starters, self.bench):
            for plist in group.values():
                out.extend(plist)
        out.extend(self.reserves)
        return out


@dataclass(frozen=True)
class TeamArrangement:
    """The manager-editable inbox: slot -> player id. Tuples (ordered,
    immutable) so two arrangements compare cleanly with `==` for the
    diff-vs-baseline check."""
    starters: dict[str, tuple[PlayerId, ...]]
    bench: dict[str, tuple[PlayerId, ...]]
    reserves: tuple[PlayerId, ...]

    @classmethod
    def from_public_view(cls, view: TeamPublicView) -> "TeamArrangement":
        """The baseline arrangement = whatever was last published. After a
        repo.load(), the model's arrangement equals this, so a sheet read that
        differs can only be a manager edit."""
        def ids(group: dict[str, list[PlayerPublicView]]) -> dict[str, tuple[PlayerId, ...]]:
            return {pos: tuple(p.id for p in plist) for pos, plist in group.items()}

        return cls(
            starters=ids(view.starters),
            bench=ids(view.bench),
            reserves=tuple(p.id for p in view.reserves),
        )

    def all_ids(self) -> list[PlayerId]:
        out: list[PlayerId] = []
        for group in (self.starters, self.bench):
            for ids in group.values():
                out.extend(ids)
        out.extend(self.reserves)
        return out
