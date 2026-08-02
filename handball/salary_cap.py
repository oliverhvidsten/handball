"""
Name: salary_cap.py
Description: The league's salary-cap and contract rules, as pure functions over a
    team's payroll (its total_salaries in $M/yr). This module is the single place
    those rules live so every write path -- trade approval today, free-agent
    signing later -- enforces the same thing.

    The rules (all figures $M/yr, from simulation_vars):
      - A contract runs 1..MAX_CONTRACT_YEARS for MIN_CONTRACT_VALUE..MAX_CONTRACT_VALUE.
        A MIN_CONTRACT_VALUE ($0) contract is a "minimum" deal: it never counts
        against the cap, because total_salaries just sums contract_value.
      - SALARY_CAP is soft. A team may exceed it (a) to re-sign/extend one of its
        OWN players (Bird rights -- only the hard cap binds), or (b) to sign an
        outside player using its Mid-Level Exception.
      - The MLE is set by current payroll: FIRST_MLE below the first luxury
        threshold, SECOND_MLE below the second, $0 at/above the second.
      - HARD_CAP may not be exceeded by a signing or a trade. The ONE exception is
        a ROOKIE draft contract: a team must always be able to sign its picks, so
        those go on the books regardless of payroll. A team that ends up over the
        hard cap that way is not in violation -- it simply may not START a season
        there, which season_readiness.py enforces as a blocker the commissioner
        must clear. Because that state is legal, a team already over the hard cap
        may still trade, as long as the trade doesn't push its payroll further up
        (assert_trade_hard_cap).

Author: relational backend
"""
from __future__ import annotations

from dataclasses import dataclass

from handball.simulation_vars import (
    FIRST_LUXURY_TAX_THRESHOLD,
    FIRST_MLE,
    HARD_CAP,
    MAX_CONTRACT_VALUE,
    MAX_CONTRACT_YEARS,
    MIN_CONTRACT_VALUE,
    SALARY_CAP,
    SECOND_LUXURY_TAX_THRESHOLD,
    SECOND_MLE,
)


class ContractError(ValueError):
    """A proposed contract or signing that the cap/contract rules forbid."""


def mid_level_exception(payroll: int) -> int:
    """The MLE a team with this payroll may spend ABOVE the cap on an outside
    signing: FIRST_MLE below the first luxury threshold, SECOND_MLE below the
    second, $0 at/above the second."""
    if payroll < FIRST_LUXURY_TAX_THRESHOLD:
        return FIRST_MLE
    if payroll < SECOND_LUXURY_TAX_THRESHOLD:
        return SECOND_MLE
    return 0


@dataclass(frozen=True)
class CapSituation:
    """A team's standing against the cap, derived purely from its payroll."""
    payroll: int
    cap_room: int              # SALARY_CAP - payroll, clamped at 0 (space under the cap)
    over_cap: bool             # payroll > SALARY_CAP
    over_first_threshold: bool
    over_second_threshold: bool
    mid_level_exception: int   # MLE available for an outside signing
    hard_cap_room: int         # HARD_CAP - payroll (headroom before the absolute ceiling)


def cap_situation(payroll: int) -> CapSituation:
    """Compute a team's cap standing from its payroll ($M/yr)."""
    return CapSituation(
        payroll=payroll,
        cap_room=max(0, SALARY_CAP - payroll),
        over_cap=payroll > SALARY_CAP,
        over_first_threshold=payroll >= FIRST_LUXURY_TAX_THRESHOLD,
        over_second_threshold=payroll >= SECOND_LUXURY_TAX_THRESHOLD,
        mid_level_exception=mid_level_exception(payroll),
        hard_cap_room=HARD_CAP - payroll,
    )


def validate_contract(term: int, value: int) -> None:
    """Raise ContractError unless the term/value are within league limits. A
    minimum ($0) contract is allowed; MIN_CONTRACT_VALUE is the floor."""
    if not (1 <= term <= MAX_CONTRACT_YEARS):
        raise ContractError(
            f"contract term {term} out of range 1..{MAX_CONTRACT_YEARS} years")
    if not (MIN_CONTRACT_VALUE <= value <= MAX_CONTRACT_VALUE):
        raise ContractError(
            f"contract value ${value}M out of range "
            f"${MIN_CONTRACT_VALUE}M..${MAX_CONTRACT_VALUE}M per year")


def max_outside_signing(payroll: int) -> int:
    """The largest annual salary a team at `payroll` may give an OUTSIDE free
    agent: fill its cap room, then spend its MLE above the cap, never past the
    hard cap. (Own-player re-signings use the hard cap directly -- see can_sign.)"""
    ceiling = min(HARD_CAP, max(SALARY_CAP, payroll) + mid_level_exception(payroll))
    return max(0, ceiling - payroll)


def max_offer(payroll: int, *, own_player: bool) -> int:
    """The largest annual salary a team at `payroll` may put in front of a free
    agent -- the answer to "what can I offer?", which is what a signing UI needs.
    Same rules as can_sign, read the other way round: an own-player re-signing is
    bounded by the hard cap alone (Bird rights), an outside signing by cap room +
    MLE, and both by MAX_CONTRACT_VALUE (no contract may exceed it, however much
    room a team has)."""
    room = (HARD_CAP - payroll) if own_player else max_outside_signing(payroll)
    return max(0, min(MAX_CONTRACT_VALUE, room))


def can_sign(payroll: int, value: int, *, own_player: bool) -> bool:
    """Whether a team at `payroll` may add a contract worth `value`/yr without
    breaking the rules. Re-signing an OWN player (Bird rights) is bounded only by
    the hard cap; an outside signing is additionally bounded by cap room + MLE."""
    if payroll + value > HARD_CAP:
        return False              # the hard cap binds everyone, always
    if own_player:
        return True               # Bird rights: may exceed the soft cap freely
    return value <= max_outside_signing(payroll)


def check_signing(payroll: int, term: int, value: int, *, own_player: bool) -> None:
    """Validate a full signing (contract limits + cap headroom), raising
    ContractError with a specific reason on any violation."""
    validate_contract(term, value)
    if payroll + value > HARD_CAP:
        raise ContractError(
            f"signing ${value}M would push payroll to ${payroll + value}M, "
            f"over the ${HARD_CAP}M hard cap")
    if not own_player and value > max_outside_signing(payroll):
        raise ContractError(
            f"outside signing ${value}M exceeds available room "
            f"(${max_outside_signing(payroll)}M) at ${payroll}M payroll")


def assert_within_hard_cap(payroll: int, *, label: str = "team") -> None:
    """Raise ContractError if a resulting payroll breaks the hard cap. Used by
    signing paths, where the cap is absolute (rookie draft deals excepted -- those
    skip this check by design; see the module docstring)."""
    if payroll > HARD_CAP:
        raise ContractError(
            f"{label} payroll ${payroll}M would exceed the ${HARD_CAP}M hard cap")


def hard_cap_overage(payroll: int) -> int:
    """How far this payroll sits ABOVE the hard cap ($0 if compliant). Only rookie
    draft contracts can produce a positive value; season_readiness turns one into a
    season-start blocker."""
    return max(0, payroll - HARD_CAP)


def assert_trade_hard_cap(payroll_before: int, payroll_after: int, *, label: str = "team") -> None:
    """The hard-cap rule for a trade: a team may not finish a trade above the hard
    cap. A team ALREADY above it (rookie exemption) is the one exception -- it may
    still trade, because trading is how it gets back into compliance, so long as the
    trade lowers its payroll rather than raising it."""
    if payroll_after <= HARD_CAP:
        return
    if payroll_after < payroll_before:
        return
    over = "already over" if payroll_before > HARD_CAP else "would be over"
    raise ContractError(
        f"{label} payroll ${payroll_after}M {over} the ${HARD_CAP}M hard cap "
        f"(from ${payroll_before}M); a team at or over the hard cap may only make "
        f"trades that reduce its payroll")
