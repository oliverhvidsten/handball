"""
Name: injury_service.py
Description: Injury next-man-up substitution, re-expressed in the redesigned
    stack as a pure arrangement computation.

    In the legacy teams.Team, an injury triggered hand-indexed mutation of the
    starter/bench/reserve lists (apply_injury_substitution: a three-way shuffle
    of bench->starter, reserve->bench, injured->reserves, with a separate
    reverse_injury_substitution to undo it on recovery). That logic lived inside
    the team object and had no validation.

    Here the operation is a SERVICE that computes a new TeamArrangement and hands
    it to Team.apply_arrangement(), so it funnels through the same validate() as
    a manager edit -- no bespoke mutation, no copy-paste index bugs, and the
    result is provably legal or it raises.

    Why a single swap instead of the legacy three-way shuffle: domain.validate()
    only forbids an injured player in a STARTING slot (an injured player on the
    bench or in reserves is a legal lineup). So the minimal legal fix is to swap
    the injured starter with healthy same-position depth -- if a healthy bench
    player exists, the injured player simply moves to the bench; otherwise a
    healthy reserve takes the starting slot and the injured player is parked in
    reserves. Counts in every tier are preserved, so the result always validates.
    Recovery needs no stored "reversal record": a recovered player is just a
    healthy bench/reserve player the manager (or a later policy) can re-start
    through the normal arrangement path.
Author: design sketch
"""
from __future__ import annotations

from handball.domain import Player, Team
from handball.league_views import DEFAULT_RULES, PlayerId, RosterRules, TeamArrangement


class InjuryError(RuntimeError):
    """Raised when an injured starter cannot be replaced -- no healthy player of
    their position exists on the bench or in reserves, so no legal lineup can be
    fielded without them."""


class InjuryService:
    def __init__(self, rules: RosterRules = DEFAULT_RULES) -> None:
        self.rules = rules

    # -- pure computation --------------------------------------------------
    def substitution_for(self, team: Team, injured: Player) -> TeamArrangement | None:
        """Compute the arrangement that pulls `injured` out of a starting slot via
        next-man-up. Returns None when no change is needed (the player is not a
        starter -- being injured on the bench/in reserves is already legal).
        Raises InjuryError when the player is an injured starter with no healthy
        same-position depth anywhere."""
        return self._compute(team, injured, team.arrangement())

    def _compute(self, team: Team, injured: Player, arr: TeamArrangement) -> TeamArrangement | None:
        """Core substitution against an explicit working arrangement, so callers
        can chain several substitutions before validating/applying once. Injury
        status is read from the (unchanging) Player objects; slot membership is
        read from `arr`."""
        pos = injured.position

        if injured.id not in arr.starters.get(pos, ()):  # bench/reserve injury: legal as-is
            return None

        # Prefer benching the injured player: swap with the first healthy bench
        # player of the same position. injured -> bench (legal), bench -> starter.
        replacement = self._first_healthy(team, arr.bench.get(pos, ()))
        if replacement is not None:
            return self._swap(arr, ("bench", pos, replacement), ("starters", pos, injured.id))

        # Bench is fully unavailable: promote a healthy reserve into the starting
        # slot and park the injured player in reserves.
        reserve = self._first_healthy(team, arr.reserves, position=pos)
        if reserve is not None:
            return self._swap(arr, ("reserves", None, reserve), ("starters", pos, injured.id))

        raise InjuryError(
            f"{injured.name} ({pos}) is an injured starter with no healthy "
            f"{pos} on the bench or in reserves; cannot field a legal lineup"
        )

    # -- mutation (routed through the single validated write path) ---------
    def apply(self, team: Team, injured: Player) -> bool:
        """Substitute one injured starter. Returns whether a substitution was
        applied (False == nothing to do). The new arrangement is validated by
        Team.apply_arrangement, so an illegal result can never be persisted."""
        arr = self.substitution_for(team, injured)
        if arr is None:
            return False
        team.apply_arrangement(arr, self.rules)
        return True

    def reconcile(self, team: Team) -> bool:
        """Substitute every injured starter until the starting lineup is healthy,
        then validate + apply the combined result ONCE. Computing intermediate
        arrangements without applying matters: a lineup with two injured starters
        is invalid until BOTH are swapped out, so each single swap would fail
        validation on its own. Idempotent; returns whether any change was made."""
        arr = team.arrangement()
        changed = False
        while True:
            injured_id = next(
                (pid for ids in arr.starters.values() for pid in ids
                 if team.get(pid).is_injured),
                None,
            )
            if injured_id is None:
                break
            arr = self._compute(team, team.get(injured_id), arr)  # raises if no depth
            changed = True
        if changed:
            team.apply_arrangement(arr, self.rules)   # single validated write
        return changed

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _first_healthy(
        team: Team, ids: tuple[PlayerId, ...], position: str | None = None
    ) -> PlayerId | None:
        for pid in ids:
            p = team.get(pid)
            if p is None or p.is_injured:
                continue
            if position is not None and p.position != position:
                continue
            return pid
        return None

    @staticmethod
    def _swap(
        arr: TeamArrangement,
        loc_a: tuple[str, str | None, PlayerId],
        loc_b: tuple[str, str | None, PlayerId],
    ) -> TeamArrangement:
        """Return a new arrangement with the players at loc_a and loc_b exchanged.
        A location is (tier, position, player_id); tier is 'starters'/'bench'
        (position required) or 'reserves' (position None)."""
        starters = {pos: list(ids) for pos, ids in arr.starters.items()}
        bench = {pos: list(ids) for pos, ids in arr.bench.items()}
        reserves = list(arr.reserves)
        tiers = {"starters": starters, "bench": bench}

        def lst(tier: str, pos: str | None) -> list[PlayerId]:
            return reserves if tier == "reserves" else tiers[tier][pos]

        la, lb = lst(*loc_a[:2]), lst(*loc_b[:2])
        ia, ib = la.index(loc_a[2]), lb.index(loc_b[2])
        la[ia], lb[ib] = loc_b[2], loc_a[2]

        return TeamArrangement(
            starters={pos: tuple(ids) for pos, ids in starters.items()},
            bench={pos: tuple(ids) for pos, ids in bench.items()},
            reserves=tuple(reserves),
        )
