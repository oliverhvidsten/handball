"""
Name: draft_rules.py
Description: Every decision the draft makes, as pure functions over plain data --
    the DB-free half of handball/draft.py, in the same split free_agency_rules.py
    has from free_agency.py. If you are looking for "what are the odds", "who picks
    where", "what is this pick worth" or "did that protection convey", it is here;
    handball/draft.py only reads snapshots, calls these, and writes what they say.

    Five decisions live here, and they are separate because they are made at
    different MOMENTS by different people:

      - draw_lottery()        the commissioner draws, once, for round 1 picks 1..M
      - build_draft_order()   the rollover fixes everything the draw does not
      - resolve_protection()  the draw's consequence for a pick that was traded
                              with a condition on it
      - rookie_deal()         the pick's price, fixed by where it fell
      - best_available()      what the clock picks when a manager does not

    THE LOTTERY is sequential and weight-positional: weight i belongs to the i-th
    WORST team still in the pool, the winner is removed, and the weights are
    renormalised over whoever is left. So LOTTERY_WEIGHTS states the odds on the
    FIRST pick only; every later slot is conditional on what came before, and the
    worst team's odds on pick 2 depend on whether it already won pick 1. The draw
    takes an injected `random.Random`, never the module-level one, because a draw
    that cannot be replayed from its seed is a draw nobody has to believe -- the
    seed is stored next to the result (draft_lotteries.seed) for exactly that.

    The weights are NOT required to sum to anything. They are read as relative
    shares and normalised at every step, so scaling them all by a constant cannot
    change a draw, and a pool shorter or longer than the weight table still draws.

    ORDER, once the draw is done, is the rulebook's:
      round 1  picks 1..M          the M non-playoff teams, by lottery
               picks M+1..N        the playoff teams by how far they went --
                                   first-round losers first (worse record first
                                   within a round), then the champion, last
      round 2  picks N+1..N+M      non-playoff teams, worst record first
               picks N+M+1..2N     playoff teams, worst record first
    Round 2 ignores the lottery and the bracket both: losing the lottery should not
    cost a team twice, and "no better than the 17th pick of the second round" is
    the rule the playoff half of that round exists to satisfy.
Author: rules alignment
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from handball.simulation_vars import LOTTERY_WEIGHTS, ROOKIE_SCALE

T = TypeVar("T")

# How a protection finally resolved, matching the draft_picks.protection_outcome
# CHECK constraint (alembic 0014).
PROTECTION_REVERTED = "reverted"
PROTECTION_CONVEYED = "conveyed"


class DraftRulesError(ValueError):
    """A draft decision the rules cannot make -- an empty lottery pool, a pick
    outside the rookie scale, a protection on a pick that was never traded. Carries
    a sentence fit to show whoever asked for it."""


# ---------------------------------------------------------------------------
# The lottery.
# ---------------------------------------------------------------------------
def normalized_weights(weights: Sequence[float], n: int) -> list[float]:
    """The first `n` weights as probabilities summing to 1.

    Two liberties, both deliberate. The weights need not sum to 100 (or to
    anything) -- they are shares, and this is the one place that matters, so the
    constant in simulation_vars can be written for readability. And a pool LONGER
    than the weight table extends it with its last (smallest) weight rather than
    failing: a league that grows should draw a slightly-too-flat tail, not refuse
    to hold a lottery."""
    if n <= 0:
        raise DraftRulesError("a lottery needs at least one team in the pool")
    if not weights:
        raise DraftRulesError("a lottery needs at least one weight")
    w = [float(weights[i]) if i < len(weights) else float(weights[-1]) for i in range(n)]
    if any(x < 0 for x in w):
        raise DraftRulesError("lottery weights may not be negative")
    total = sum(w)
    if total <= 0:
        raise DraftRulesError("lottery weights must not all be zero")
    return [x / total for x in w]


def draw_lottery(
    order: Sequence[T],
    rng: random.Random,
    weights: Sequence[float] = LOTTERY_WEIGHTS,
) -> list[T]:
    """Draw every slot, worst-first pool in, pick order out: `order` is the lottery
    pool WORST FIRST, and the result is the teams in the order they will pick
    (index 0 == the first overall pick).

    One draw per slot: the i-th worst team still in the pool carries weights[i],
    the winner comes out of the pool, and the next slot is drawn over the
    renormalised remainder. The last team left needs no draw.

    Selection is an explicit cumulative walk over rng.random() rather than
    random.choices, so the result depends only on the seed and this function --
    not on a CPython implementation detail that could change the historical record
    of a draw under the league's feet."""
    pool = list(order)
    if not pool:
        raise DraftRulesError("a lottery needs at least one team in the pool")

    drawn: list[T] = []
    while len(pool) > 1:
        probs = normalized_weights(weights, len(pool))
        roll = rng.random()
        cumulative = 0.0
        index = len(pool) - 1        # float-safety: a roll of ~1.0 takes the last
        for i, p in enumerate(probs):
            cumulative += p
            if roll < cumulative:
                index = i
                break
        drawn.append(pool.pop(index))
    drawn.append(pool.pop())
    return drawn


# ---------------------------------------------------------------------------
# The order.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DraftOrder:
    """One season's pick order, in the three pieces that are decided differently.

    `lottery_pool` is worst-first and holds no pick NUMBERS: those are the slots the
    draw assigns, which is why they are seeded null at the rollover and filled in
    later. The other two are final the moment the season ends."""
    lottery_pool: tuple[Any, ...]        # round 1, picks 1..M -- by lottery
    round_one_playoff: tuple[Any, ...]   # round 1, picks M+1..N -- by playoff result
    round_two: tuple[Any, ...]           # round 2, picks N+1..2N -- by record

    @property
    def teams(self) -> int:
        return len(self.round_two)

    @property
    def lottery_slots(self) -> int:
        return len(self.lottery_pool)


def playoff_pick_order(
    losers_by_round: Mapping[int, Iterable[T]],
    champion: T | None,
    ranked_team_ids: Sequence[T],
) -> list[T]:
    """The playoff half of round 1, in pick order: knocked out earliest picks first.

    Within one round the losers are ordered by RECORD, worse first -- they all went
    exactly as far as each other, so the regular season is the only thing left to
    separate them, and it is the same thing that separates the teams picking ahead
    of them. The champion picks last; the runner-up (the final round's only loser)
    picks second-to-last, which falls out of the round ordering without a special
    case.

    `ranked_team_ids` is best->worst. A team that is not in it sorts last, which is
    a defensive position, not a rule: it should not happen."""
    rank = {tid: i for i, tid in enumerate(ranked_team_ids)}
    worst_first = lambda tid: -rank.get(tid, len(ranked_team_ids))  # noqa: E731

    out: list[T] = []
    seen: set = set()
    for round_num in sorted(losers_by_round):
        losers = [t for t in losers_by_round[round_num] if t not in seen]
        for tid in sorted(losers, key=worst_first):
            seen.add(tid)
            out.append(tid)
    if champion is not None and champion not in seen:
        out.append(champion)
    return out


def build_draft_order(
    ranked_team_ids: Sequence[T],
    losers_by_round: Mapping[int, Iterable[T]] | None = None,
    champion: T | None = None,
) -> DraftOrder:
    """The whole order for next season, from this season's standings and bracket.

    `ranked_team_ids` is best->worst (handball/standings.py's one ranking rule).
    `losers_by_round` / `champion` describe the postseason; with neither -- a league
    that has not played one -- every team is a "non-playoff" team, the lottery pool
    is the entire league worst-first, and round 2 is plain reverse standings. That
    is the pre-playoff behaviour, and it is what the offline stack and any league
    that skipped a postseason get."""
    playoff: set = set()
    for teams in (losers_by_round or {}).values():
        playoff.update(teams)
    if champion is not None:
        playoff.add(champion)

    worst_first = list(reversed(list(ranked_team_ids)))
    pool = [t for t in worst_first if t not in playoff]
    playoff_worst_first = [t for t in worst_first if t in playoff]

    return DraftOrder(
        lottery_pool=tuple(pool),
        round_one_playoff=tuple(
            playoff_pick_order(losers_by_round or {}, champion, ranked_team_ids)
        ),
        round_two=tuple(pool + playoff_worst_first),
    )


# ---------------------------------------------------------------------------
# Protections.
# ---------------------------------------------------------------------------
def protection_outcome(pick_number: int, protection_top_n: int) -> str:
    """Did a top-`protection_top_n` protection catch a pick that landed at
    `pick_number`? 'reverted' if it did, 'conveyed' if it did not.

    Inclusive on the boundary: a top-3 protected pick that draws #3 is protected.
    That is how the condition is written and said out loud ("top three protected"),
    and a rule about somebody's first-round pick should mean what the two managers
    said to each other."""
    if protection_top_n < 1:
        raise DraftRulesError("a protection must cover at least the first pick")
    return PROTECTION_REVERTED if pick_number <= protection_top_n else PROTECTION_CONVEYED


def resolve_protection(
    pick_number: int,
    protection_top_n: int,
    holder: T,
    original: T,
) -> tuple[T, str]:
    """Where a protected pick ends up, and what to record about it: (holder,
    outcome).

    A protection only ever describes a pick somebody else is holding -- an untraded
    pick has no condition to resolve -- so an unmoved pick is refused rather than
    silently marked 'conveyed'. A caught pick goes back to the team it came from
    and the obligation ENDS: it does not roll over into a later year. That was a
    decision, not an oversight (see PLAN.md); a rollover would need a second pick
    to attach to and a second year to argue about."""
    if holder == original:
        raise DraftRulesError(
            "a protection describes a traded pick; this one never left its team")
    outcome = protection_outcome(pick_number, protection_top_n)
    return (original if outcome == PROTECTION_REVERTED else holder), outcome


# ---------------------------------------------------------------------------
# What a pick is worth.
# ---------------------------------------------------------------------------
def rookie_deal(overall: int, scale: Sequence[Sequence[int]] = ROOKIE_SCALE) -> tuple[int, int]:
    """(years, $M/yr) for the `overall`-th pick, off the rookie scale.

    A drafted player's contract is not negotiated: where you were taken IS the
    deal. That is what makes a traded pick a knowable asset -- both managers can
    price the 14th pick without knowing who will be standing there -- and it is why
    this is a lookup rather than a signing.

    An overall outside the scale is an error, not a default: it means the draft is
    longer than the league decided to pay for, and inventing a contract for pick 65
    would hide that."""
    for first, last, years, salary in scale:
        if first <= overall <= last:
            return int(years), int(salary)
    raise DraftRulesError(
        f"pick {overall} is outside the rookie scale (picks "
        f"{scale[0][0]}-{scale[-1][1]}); the draft is longer than the scale pays for")


# ---------------------------------------------------------------------------
# What the clock picks.
# ---------------------------------------------------------------------------
def prospect_rating(prospect: Mapping[str, Any]) -> float:
    """One number for a prospect, from the three visible ratings.

    The plain sum, which is exactly what roster_layout.canonical_team sorts a
    roster by. Sharing that definition matters more than refining it: the clock
    should take the player the league already calls the best one, so that a manager
    who let their turn lapse cannot argue the auto-pick used a different yardstick
    than the lineup does. A goalie's offense/defense sit near zero and their
    goalie_skill carries the sum, which is the same reason it works there."""
    return (float(prospect.get("offense") or 0.0)
            + float(prospect.get("defense") or 0.0)
            + float(prospect.get("goalie_skill") or 0.0))


def best_available(prospects: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The prospect the clock takes: highest rating, ties broken by the order the
    class was uploaded in (`ord`), so an auto-pick is reproducible and two identical
    prospects resolve the same way every time rather than by dict ordering."""
    if not prospects:
        raise DraftRulesError("no prospects are left on the board")
    return max(prospects, key=lambda p: (prospect_rating(p), -int(p.get("ord") or 0)))


# ---------------------------------------------------------------------------
# Small arithmetic the room and the UI both need.
# ---------------------------------------------------------------------------
def pick_coordinates(overall: int, teams: int) -> tuple[int, int]:
    """(round, pick within that round) for an overall pick number. Both 1-based."""
    if teams <= 0:
        raise DraftRulesError("a draft needs at least one team")
    if overall < 1:
        raise DraftRulesError("pick numbers start at 1")
    return (overall - 1) // teams + 1, (overall - 1) % teams + 1
