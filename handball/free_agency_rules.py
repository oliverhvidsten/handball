"""
Name: free_agency_rules.py
Description: The rules of the offseason market, as pure functions over frozen
    snapshots. No database, no SQL -- this module does not import sqlalchemy, which
    is the mechanical test that it stayed pure. handball/free_agency.py is the SQL
    layer that reads snapshots, calls these, and writes the results.

    The mechanism, in the order the rules fire:

      1. OFFER ROUND. Teams submit sealed offers (term x salary) to free agents. A
         team may hold only one live offer per player, and all of its live offers
         must be honourable AT ONCE -- see check_exposure, the invariant the whole
         auction rests on.
      2. RESTRICTED RESOLUTION (plan_auction). A player is restricted only the first
         time they reach free agency. If their former team led the bidding they
         simply stay; otherwise that team gets an exclusive window to match the top
         offer exactly. Declining re-plans the board as an ordinary free agency
         (plan_after_decline).
      3. NORMAL RESOLUTION (plan_auction). One offer signs it. Two or more open a
         sequential auction.
      4. BIDDING (apply_bid). Turn order is the initial offers WORST-first. Each turn
         is match / raise / forfeit. Last team standing signs at its own last offer.
         A full cycle in which everyone matched and nobody raised is a deadlock the
         commissioner breaks.

    Two things carry the whole design:

    THE RANKING (rank_key) is one total order on contracts -- higher salary, then
    longer term, then earlier submission -- and every "highest", "better" and "worst
    first" in the league refers to it. Submission order is fa_offers.id, a bigserial:
    a sequence cannot produce the ties a timestamp can, so the order is genuinely
    total and MATCH and RAISE stay distinct actions (re-offering the leading contract
    ranks just BELOW it, so it can never take the lead).

    THE EXPOSURE RULE (check_exposure) says an offer is a promise and a team may not
    promise more than it can keep. Without it the sealed round is free option-buying:
    bid on everyone, honour whatever you happen to win.

Author: relational backend
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from handball.league_views import DEFAULT_RULES, RosterRules
from handball.salary_cap import ContractError, check_signing, max_outside_signing
from handball.simulation_vars import HARD_CAP

# Bid actions, matching the API verbs and fa_actions labels.
BID_MATCH = "match"
BID_RAISE = "raise"
BID_FORFEIT = "forfeit"

# Auction statuses / outcomes, matching the fa_auction_status and fa_auction_outcome
# enum labels in alembic 0011. Kept as plain strings so this module stays free of any
# database dependency.
STATUS_MATCHING = "matching"
STATUS_BIDDING = "bidding"
STATUS_AWAITING_AWARD = "awaiting_award"
STATUS_RESOLVED = "resolved"
STATUS_VOID = "void"

OUTCOME_RFA_KEPT = "rfa_kept"
OUTCOME_RFA_MATCHED = "rfa_matched"
OUTCOME_COMMISSIONER_AWARD = "commissioner_award"
OUTCOME_SOLE_OFFER = "sole_offer"
OUTCOME_BID_WON = "bid_won"
OUTCOME_NO_OFFERS = "no_offers"
OUTCOME_ALL_FORFEITED = "all_forfeited"
OUTCOME_PLAYER_INELIGIBLE = "player_ineligible"

PENDING_OFFER_ID = 1 << 62
"""Stand-in id for an offer that has not been inserted yet. fa_offers.id is a
bigserial, so ids only count up and a pending offer is BY DEFINITION the latest
submission; giving it the largest possible id makes the ranking say exactly that,
and the rules never need to know which id the insert will really get."""


class FreeAgencyError(RuntimeError):
    """Something the free-agency rules forbid. Carries a sentence fit to show the
    manager who tried it."""


class OfferError(FreeAgencyError):
    """An offer the rules forbid -- an ineligible player, or a team promising more
    than it can keep."""


class BidError(FreeAgencyError):
    """A bid the rules forbid -- out of turn, from a team that has forfeited, or a
    raise that doesn't actually beat the leader."""


# ---------------------------------------------------------------------------
# The ranking: one total order on contracts.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Offer:
    """One contract offer as the rules see it. `offer_id` is fa_offers.id -- a
    bigserial, so it IS submission order."""
    offer_id: int
    team_id: str
    term: int
    value: int


def rank_key(offer: Offer) -> tuple[int, int, int]:
    """Sort key putting the BEST offer first: higher annual salary, then longer term,
    then earlier submission. Negated so a plain ascending sort is best-first, and
    returned as a tuple so sorted()/min() and better() cannot disagree -- there is
    exactly ONE definition of the league's contract order, and this is it."""
    return (-offer.value, -offer.term, offer.offer_id)


def better(a: Offer, b: Offer) -> bool:
    """Does `a` rank strictly above `b`? Total: for two offers with different ids,
    exactly one of better(a, b) / better(b, a) holds."""
    return rank_key(a) < rank_key(b)


def best_offer(offers: Iterable[Offer]) -> Offer | None:
    """The leading offer, or None for an empty field."""
    return min(offers, key=rank_key, default=None)


def by_rank(offers: Iterable[Offer]) -> list[Offer]:
    """Offers best-first."""
    return sorted(offers, key=rank_key)


def turn_order(offers: Iterable[Offer]) -> list[Offer]:
    """Bidding order: the WORST initial offer acts first and the best acts last, so
    the team that already leads never has to commit before it has seen everyone."""
    return list(reversed(by_rank(offers)))


def check_raise(new: Offer, leader: Offer) -> None:
    """Raise BidError unless `new` strictly beats the current leader. The submission
    tiebreak is what keeps MATCH and RAISE distinct: an identical term+value offered
    later always ranks BELOW, so re-offering the leading contract is a match, never a
    raise."""
    if not better(new, leader):
        raise BidError(
            f"{new.term}yr/${new.value}M does not beat the leading "
            f"{leader.term}yr/${leader.value}M offer -- raise higher, match it, or "
            f"forfeit")


# ---------------------------------------------------------------------------
# Exposure: a team may not promise more than it can keep.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PendingOffer:
    """A live offer as the EXPOSURE rule sees it: what it would cost, and whether the
    offering team holds that player's Bird rights (which changes its ceiling)."""
    offer_id: int
    player_id: str
    player_name: str
    term: int
    value: int
    own_player: bool          # rights_team_id == this team; see signing_service


@dataclass(frozen=True)
class TeamExposure:
    """A team's whole live position: what is on the books today, plus everything it
    has promised on paper."""
    team_id: str
    team_name: str
    payroll: int                              # cap-counting payroll TODAY ($M/yr)
    roster_size: int                          # non-retired players on the roster TODAY
    live_offers: tuple[PendingOffer, ...] = ()


def committed_salary(exposure: TeamExposure, *, exclude: int | None = None) -> int:
    """What this team's live offers already promise, optionally net of one of them."""
    return sum(o.value for o in exposure.live_offers if o.offer_id != exclude)


def open_roster_spots(exposure: TeamExposure, rules: RosterRules = DEFAULT_RULES) -> int:
    """Roster spots left AFTER honouring every live offer. Negative is a violation."""
    return rules.max_roster - exposure.roster_size - len(exposure.live_offers)


def check_exposure(exposure: TeamExposure, rules: RosterRules = DEFAULT_RULES) -> None:
    """Raise OfferError unless EVERY live offer this team holds could be signed. This
    is the invariant the auction rests on -- an offer is a promise, and a team that
    could welch on a winning bid turns the sealed round into free option-buying.

    Four conditions, all order-independent:

      BODIES.      One live offer needs one open roster spot, and they all need one at
                   the same time. A player under contract cannot be dropped, so a full
                   roster means no offers at all.

      HARD CAP.    Payroll plus every promised salary must fit under the hard cap.
                   This is the only bound on a Bird-rights offer (re-signing your own
                   expiring player), exactly as salary_cap.check_signing has it.

      ONE MLE.     Outside offers TOGETHER must fit the team's outside allowance -- cap
                   room plus ONE mid-level exception. Checked as a sum, because
                   checking each offer against the current payroll separately would
                   let a team spend the same exception once per offer.

      LANDS LAST.  Each offer must still be legal when its payroll is inflated by all
                   the others -- i.e. if it happens to be the one that resolves last.
                   The team does not choose the order (auctions resolve when other
                   managers happen to log in), so every offer has to survive the worst
                   one. This is what the sum above cannot see: the allowance SHRINKS as
                   promises land, and it falls off a cliff at each luxury threshold. At
                   a $199M payroll, promising $3M and $2M passes the sum ($5M of $5M
                   room) but signing the $3M crosses the second threshold, where the
                   exception is $0 and the $2M promise can no longer be kept.

    Called after EVERY mutation of the live set (submit, edit, withdraw, raise,
    match) and always over the WHOLE set, because adding a fourth offer can make the
    first one unaffordable without touching its row."""
    spots = open_roster_spots(exposure, rules)
    if spots < 0:
        free = rules.max_roster - exposure.roster_size
        raise OfferError(
            f"{exposure.team_name} has {len(exposure.live_offers)} live offer(s) but "
            f"only {free} open roster spot(s); withdraw an offer before making another")

    promised = committed_salary(exposure)
    if exposure.payroll + promised > HARD_CAP:
        raise OfferError(
            f"{exposure.team_name} has promised ${promised}M on top of a "
            f"${exposure.payroll}M payroll, which would reach "
            f"${exposure.payroll + promised}M -- over the ${HARD_CAP}M hard cap")

    outside = sum(o.value for o in exposure.live_offers if not o.own_player)
    allowance = max_outside_signing(exposure.payroll)
    if outside > allowance:
        raise OfferError(
            f"{exposure.team_name} has promised ${outside}M to outside free agents "
            f"but has only ${allowance}M of room (cap space plus one mid-level "
            f"exception) at a ${exposure.payroll}M payroll")

    for offer in exposure.live_offers:
        others = promised - offer.value
        try:
            check_signing(exposure.payroll + others, offer.term, offer.value,
                          own_player=offer.own_player)
        except ContractError as e:
            raise OfferError(
                f"{exposure.team_name} could not honour its {offer.term}yr/"
                f"${offer.value}M offer to {offer.player_name} if it were the last to "
                f"land (${others}M already promised on top of a ${exposure.payroll}M "
                f"payroll): {e}") from e


def check_offer_legal(
    exposure: TeamExposure,
    offer: PendingOffer,
    rules: RosterRules = DEFAULT_RULES,
) -> None:
    """Raise OfferError unless this ONE offer is a legal contract for this team,
    ignoring its other promises -- the per-deal half of the rules
    (salary_cap.check_signing: contract limits, Bird rights, the hard cap). Callers
    pair it with check_exposure over the whole set."""
    try:
        check_signing(exposure.payroll, offer.term, offer.value,
                      own_player=offer.own_player)
    except ContractError as e:
        raise OfferError(
            f"{exposure.team_name} cannot offer {offer.term}yr/${offer.value}M to "
            f"{offer.player_name}: {e}") from e


def with_pending_signing(exposure: TeamExposure, value: int) -> TeamExposure:
    """The exposure this team WILL have once a signing it is about to make lands.

    Needed for exactly one case. When a LIVE offer converts to a contract, exposure is
    neutral: payroll rises by the salary and the offer leaves the promised set, so
    every other offer is measured against the same number as before. An RFA MATCH is
    the exception -- it conjures an offer that was never live and converts it in the
    same breath, so payroll rises with nothing leaving."""
    return replace(exposure,
                   payroll=exposure.payroll + value,
                   roster_size=exposure.roster_size + 1)


# ---------------------------------------------------------------------------
# Closing the offer round: what happens to one player's board.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SeatPlan:
    team_id: str
    turn_order: int


@dataclass(frozen=True)
class AuctionInput:
    """One player's board at the moment the offer round closes."""
    auction_id: int
    player_id: str
    player_name: str
    offers: tuple[Offer, ...] = ()
    restricted: bool = False
    rights_team_id: str | None = None
    eligible: bool = True              # still a signable free agent
    ineligible_reason: str | None = None
    # Offers that were legal when submitted but aren't any more (their team traded
    # since). The SQL layer decides this; the rules just drop them.
    illegal_offer_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class AuctionPlan:
    """What closing the round does to one board."""
    auction_id: int
    status: str
    outcome: str | None = None
    winning_offer_id: int | None = None
    winning_team_id: str | None = None
    winning_term: int | None = None
    winning_value: int | None = None
    match_offer_id: int | None = None
    seats: tuple[SeatPlan, ...] = ()
    turn_team_id: str | None = None
    lost_offer_ids: tuple[int, ...] = ()
    voided_offer_ids: tuple[int, ...] = ()


def plan_auction(a: AuctionInput) -> AuctionPlan:
    """The whole of the restricted and normal resolution rules, as one pure function
    over a snapshot. Stale offers are dropped first, so a board can fall from two
    offers to a sole-offer signing, or from one to nothing."""
    live = [o for o in a.offers if o.offer_id not in a.illegal_offer_ids]
    dropped = tuple(o.offer_id for o in a.offers if o.offer_id in a.illegal_offer_ids)

    if not a.eligible:
        return AuctionPlan(a.auction_id, STATUS_VOID, OUTCOME_PLAYER_INELIGIBLE,
                           voided_offer_ids=tuple(o.offer_id for o in a.offers))
    if not live:
        return AuctionPlan(a.auction_id, STATUS_VOID, OUTCOME_NO_OFFERS,
                           voided_offer_ids=tuple(o.offer_id for o in a.offers))

    top = best_offer(live)
    assert top is not None

    # Restricted: the former team either already leads, or gets an exclusive window.
    if a.restricted and a.rights_team_id is not None:
        if top.team_id == a.rights_team_id:
            return _signed(a, top, OUTCOME_RFA_KEPT, live, dropped)
        return AuctionPlan(a.auction_id, STATUS_MATCHING, match_offer_id=top.offer_id,
                           voided_offer_ids=dropped)

    if len(live) == 1:
        return _signed(a, live[0], OUTCOME_SOLE_OFFER, live, dropped)

    seats = tuple(SeatPlan(o.team_id, i) for i, o in enumerate(turn_order(live)))
    return AuctionPlan(a.auction_id, STATUS_BIDDING, seats=seats,
                       turn_team_id=seats[0].team_id, voided_offer_ids=dropped)


def plan_after_decline(a: AuctionInput) -> AuctionPlan:
    """The rights team passed (or could not legally match): the player becomes an
    ordinary free agent with the offers already on the table -- the rights team's own
    lower offer included, which is what the rule says literally. That offer can only
    win if every other team voluntarily forfeits, so it is not exploitable."""
    return plan_auction(replace(a, restricted=False))


def _signed(a: AuctionInput, win: Offer, outcome: str,
            live: list[Offer], dropped: tuple[int, ...]) -> AuctionPlan:
    return AuctionPlan(
        a.auction_id, STATUS_RESOLVED, outcome,
        winning_offer_id=win.offer_id, winning_team_id=win.team_id,
        winning_term=win.term, winning_value=win.value,
        lost_offer_ids=tuple(o.offer_id for o in live if o.offer_id != win.offer_id),
        voided_offer_ids=dropped,
    )


# ---------------------------------------------------------------------------
# Sequential bidding.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Seat:
    team_id: str
    turn_order: int
    active: bool = True


@dataclass(frozen=True)
class BiddingState:
    """A live board: who is still in, whose turn it is, and every live offer."""
    auction_id: int
    seats: tuple[Seat, ...]
    offers: tuple[Offer, ...]
    turn_team_id: str | None
    no_raise_streak: int = 0


@dataclass(frozen=True)
class BidPlan:
    """The whole effect of one turn."""
    auction_id: int
    team_id: str
    action: str
    new_offer: Offer | None = None          # to insert (offer_id == PENDING_OFFER_ID)
    supersede_offer_id: int | None = None
    forfeit_offer_id: int | None = None
    forfeit_seat: bool = False
    no_raise_streak: int = 0
    turn_team_id: str | None = None
    status: str = STATUS_BIDDING
    outcome: str | None = None
    winning_team_id: str | None = None
    winning_term: int | None = None
    winning_value: int | None = None
    lost_offer_ids: tuple[int, ...] = ()


def leader(state: BiddingState) -> Offer:
    """The current leading offer. A live board always has one."""
    top = best_offer(state.offers)
    if top is None:
        raise BidError("this auction has no live offers")
    return top


def active_seats(state: BiddingState) -> tuple[Seat, ...]:
    """Teams still in, in turn order."""
    return tuple(sorted((s for s in state.seats if s.active), key=lambda s: s.turn_order))


def next_turn(state: BiddingState, after_team_id: str) -> str | None:
    """The next active seat cyclically after this one, or None if nobody is left.
    Returns the same team when it is the only one still in."""
    active = active_seats(state)
    if not active:
        return None
    order = [s.team_id for s in active]
    if after_team_id in order:
        return order[(order.index(after_team_id) + 1) % len(order)]
    return order[0]


def deadlocked(state: BiddingState) -> bool:
    """A full no-raise cycle: every remaining team matched and nobody raised.

    The streak only increments on a match and is reset to zero by BOTH a raise and a
    forfeit, so reaching the number of active teams means each of them acted once
    without improving the offer. It cannot fire early (a shrinking field resets it)
    and it cannot fire late (the count is exact). Two teams parked at the maximum
    contract is simply the smallest instance."""
    active = active_seats(state)
    return bool(active) and state.no_raise_streak >= len(active)


def apply_bid(
    state: BiddingState,
    team_id: str,
    action: str,
    *,
    term: int | None = None,
    value: int | None = None,
    forced: bool = False,
) -> BidPlan:
    """One turn of sequential bidding.

    MATCH   adopt the leader's exact term and salary and stay in. By the submission
            tiebreak the copy ranks just below theirs, so matching never takes the
            lead -- which is the point: it keeps you alive without paying more. The
            team that ALREADY leads matches by standing pat, inserting nothing.
    RAISE   a strictly better contract; the raiser takes the lead.
    FORFEIT out of this board; the promise is released.

    Then, in order: nobody left -> void; one team left -> it signs at its own last
    offer; a full no-raise cycle -> the commissioner must award it; otherwise the
    turn passes to the next active seat.

    `forced` is the commissioner acting for a manager -- a stalling one, or one whose
    offer has become illegal. It waives two things and nothing else: acting out of
    turn, and the rule that the last team standing may not walk away (a winner whose
    offer is no longer legal has to be removable, which empties the board and returns
    the player to the next round). It never waives the contract or cap rules."""
    if action not in (BID_MATCH, BID_RAISE, BID_FORFEIT):
        raise BidError(f"unknown bid action {action!r}")
    if not forced and state.turn_team_id != team_id:
        raise BidError("it is not your turn to bid on this player")

    seat = next((s for s in state.seats if s.team_id == team_id), None)
    if seat is None:
        raise BidError("your team is not bidding on this player")
    if not seat.active:
        raise BidError("your team has already forfeited this player")

    lead = leader(state)
    mine = next((o for o in state.offers if o.team_id == team_id), None)

    new_offer: Offer | None = None
    supersede: int | None = None
    forfeit_offer: int | None = None
    forfeit_seat = False

    if action == BID_RAISE:
        if term is None or value is None:
            raise BidError("a raise needs a term and a salary")
        new_offer = Offer(PENDING_OFFER_ID, team_id, term, value)
        check_raise(new_offer, lead)
        supersede = mine.offer_id if mine else None
        streak = 0
    elif action == BID_MATCH:
        if lead.team_id != team_id:
            new_offer = Offer(PENDING_OFFER_ID, team_id, lead.term, lead.value)
            supersede = mine.offer_id if mine else None
        streak = state.no_raise_streak + 1
    else:                                   # forfeit
        if not forced and len(active_seats(state)) == 1:
            raise BidError(
                "you are the only team left bidding -- you win the player rather than "
                "forfeit them")
        forfeit_offer = mine.offer_id if mine else None
        forfeit_seat = True
        streak = 0

    # The board as it stands after this action.
    after_seats = tuple(
        replace(s, active=False) if (forfeit_seat and s.team_id == team_id) else s
        for s in state.seats
    )
    after_offers = [o for o in state.offers if o.team_id != team_id or not forfeit_seat]
    if new_offer is not None:
        after_offers = [o for o in after_offers if o.team_id != team_id] + [new_offer]
    after = BiddingState(state.auction_id, after_seats, tuple(after_offers),
                         state.turn_team_id, streak)

    plan = BidPlan(
        auction_id=state.auction_id, team_id=team_id, action=action,
        new_offer=new_offer, supersede_offer_id=supersede,
        forfeit_offer_id=forfeit_offer, forfeit_seat=forfeit_seat,
        no_raise_streak=streak,
    )

    remaining = active_seats(after)
    if not remaining:
        return replace(plan, status=STATUS_VOID, outcome=OUTCOME_ALL_FORFEITED,
                       turn_team_id=None,
                       lost_offer_ids=tuple(o.offer_id for o in after.offers))
    if len(remaining) == 1:
        winner = remaining[0].team_id
        deal = next((o for o in after.offers if o.team_id == winner), None)
        if deal is None:                    # cannot happen: a seat always has an offer
            raise BidError("the last team standing has no live offer")
        return replace(
            plan, status=STATUS_RESOLVED, outcome=OUTCOME_BID_WON, turn_team_id=None,
            winning_team_id=winner, winning_term=deal.term, winning_value=deal.value,
            lost_offer_ids=tuple(o.offer_id for o in after.offers
                                 if o.team_id != winner and o.offer_id != PENDING_OFFER_ID),
        )
    if deadlocked(after):
        return replace(plan, status=STATUS_AWAITING_AWARD, turn_team_id=None)
    return replace(plan, turn_team_id=next_turn(after, team_id))


def check_award(state: BiddingState, team_id: str) -> Offer:
    """The contract a commissioner award signs: that team's own live offer. Raises
    BidError unless the team is still in the bidding."""
    seat = next((s for s in state.seats if s.team_id == team_id and s.active), None)
    if seat is None:
        raise BidError("that team is not one of the teams still bidding")
    deal = next((o for o in state.offers if o.team_id == team_id), None)
    if deal is None:
        raise BidError("that team has no live offer to award")
    return deal
