"""
Unit tests for the offseason market's RULES (handball/free_agency_rules.py): the
contract ranking, the offer-exposure invariant, round resolution, and the sequential
bidding machine. All pure functions over hand-built snapshots -- no database, so
these always run. The SQL layer is covered in tests/test_free_agency.py.
"""
import ast
import itertools

import pytest

from handball.free_agency_rules import (
    BID_FORFEIT,
    BID_MATCH,
    BID_RAISE,
    OUTCOME_ALL_FORFEITED,
    OUTCOME_BID_WON,
    OUTCOME_NO_OFFERS,
    OUTCOME_PLAYER_INELIGIBLE,
    OUTCOME_RFA_KEPT,
    OUTCOME_SOLE_OFFER,
    PENDING_OFFER_ID,
    STATUS_AWAITING_AWARD,
    STATUS_BIDDING,
    STATUS_MATCHING,
    STATUS_RESOLVED,
    STATUS_VOID,
    AuctionInput,
    BidError,
    BiddingState,
    Offer,
    OfferError,
    PendingOffer,
    Seat,
    TeamExposure,
    apply_bid,
    best_offer,
    better,
    by_rank,
    check_award,
    check_exposure,
    check_offer_legal,
    check_raise,
    committed_salary,
    deadlocked,
    next_turn,
    open_roster_spots,
    plan_after_decline,
    plan_auction,
    rank_key,
    turn_order,
    with_pending_signing,
)
from handball.league_views import DEFAULT_RULES
from handball.salary_cap import max_outside_signing
from handball.simulation_vars import HARD_CAP, MAX_CONTRACT_VALUE, SALARY_CAP

BOS, DEN, AUS = "team-bos", "team-den", "team-aus"


def _o(offer_id: int, team: str, term: int, value: int) -> Offer:
    return Offer(offer_id=offer_id, team_id=team, term=term, value=value)


# -- the module is pure ------------------------------------------------------
def test_the_rules_module_never_touches_the_database():
    """The pure/SQL split is the whole shape of this module; assert it mechanically
    rather than trusting the docstring."""
    import handball.free_agency_rules as rules

    tree = ast.parse(open(rules.__file__).read())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("sqlalchemy" in m for m in imported)


# -- the ranking -------------------------------------------------------------
def test_salary_outranks_term():
    assert better(_o(2, DEN, 1, 20), _o(1, BOS, 5, 19))


def test_term_breaks_a_salary_tie():
    assert better(_o(2, DEN, 5, 20), _o(1, BOS, 3, 20))


def test_earlier_submission_breaks_a_salary_and_term_tie():
    assert better(_o(1, BOS, 3, 20), _o(2, DEN, 3, 20))


def test_ranking_is_a_strict_total_order():
    offers = [_o(i, f"t{i}", term, value)
              for i, (term, value) in enumerate(itertools.product((1, 3, 5), (0, 10, 45)), 1)]
    for a in offers:
        assert not better(a, a)                                   # irreflexive
        for b in offers:
            if a is b:
                continue
            assert better(a, b) != better(b, a)                   # antisymmetric + total
            for c in offers:
                if better(a, b) and better(b, c):
                    assert better(a, c)                           # transitive


def test_best_offer_of_nothing_is_nothing():
    assert best_offer([]) is None


def test_turn_order_is_the_ranking_reversed():
    offers = [_o(1, BOS, 3, 20), _o(2, DEN, 1, 30), _o(3, AUS, 2, 10)]
    assert turn_order(offers) == list(reversed(by_rank(offers)))
    # the worst offer acts first, the leader last
    assert [o.team_id for o in turn_order(offers)] == [AUS, BOS, DEN]


def test_rank_key_and_better_agree():
    a, b = _o(1, BOS, 3, 20), _o(2, DEN, 4, 20)
    assert better(b, a) is (rank_key(b) < rank_key(a))


# -- what counts as a raise --------------------------------------------------
def test_re_offering_the_leading_contract_is_not_a_raise():
    lead = _o(1, BOS, 3, 20)
    with pytest.raises(BidError, match="does not beat"):
        check_raise(_o(PENDING_OFFER_ID, DEN, 3, 20), lead)


def test_one_more_million_is_a_raise():
    check_raise(_o(PENDING_OFFER_ID, DEN, 3, 21), _o(1, BOS, 3, 20))


def test_the_same_salary_for_longer_is_a_raise():
    check_raise(_o(PENDING_OFFER_ID, DEN, 4, 20), _o(1, BOS, 3, 20))


def test_less_money_for_longer_is_not_a_raise():
    with pytest.raises(BidError):
        check_raise(_o(PENDING_OFFER_ID, DEN, 5, 19), _o(1, BOS, 3, 20))


# -- exposure ----------------------------------------------------------------
def _exp(payroll: int, *offers: PendingOffer, roster: int = 15) -> TeamExposure:
    return TeamExposure(team_id=BOS, team_name="Boston", payroll=payroll,
                        roster_size=roster, live_offers=offers)


def _p(offer_id: int, value: int, *, term: int = 3, own: bool = False) -> PendingOffer:
    return PendingOffer(offer_id=offer_id, player_id=f"p{offer_id}",
                        player_name=f"Player {offer_id}", term=term, value=value,
                        own_player=own)


def test_a_team_with_no_offers_is_always_fine():
    check_exposure(_exp(HARD_CAP))


def test_offers_may_not_outnumber_open_roster_spots():
    roster = DEFAULT_RULES.max_roster - 2
    check_exposure(_exp(0, _p(1, 5), _p(2, 5), roster=roster))
    with pytest.raises(OfferError, match="roster spot"):
        check_exposure(_exp(0, _p(1, 5), _p(2, 5), _p(3, 5), roster=roster))


def test_a_full_roster_may_not_offer_at_all():
    with pytest.raises(OfferError, match="roster spot"):
        check_exposure(_exp(0, _p(1, 0), roster=DEFAULT_RULES.max_roster))


def test_open_roster_spots_counts_promises_as_bodies():
    assert open_roster_spots(_exp(0, _p(1, 5), roster=18)) == DEFAULT_RULES.max_roster - 19


def test_promises_may_not_cross_the_hard_cap():
    check_exposure(_exp(HARD_CAP - 20, _p(1, 20, own=True)))
    with pytest.raises(OfferError, match="hard cap"):
        check_exposure(_exp(HARD_CAP - 20, _p(1, 21, own=True)))


def test_outside_offers_share_one_mid_level_exception():
    """Two offers that are each legal alone must not jointly spend the MLE twice."""
    payroll = 145
    assert max_outside_signing(payroll) == 15
    check_exposure(_exp(payroll, _p(1, 10), _p(2, 5)))            # 15 total: exactly the room
    with pytest.raises(OfferError, match="outside free agents"):
        check_exposure(_exp(payroll, _p(1, 10), _p(2, 10)))       # 20 > 15, though each fits


def test_a_promise_must_survive_being_the_last_one_to_land():
    """At $199M the team has $5M of room, so $3M + $2M passes the sum -- but signing
    the $3M crosses the second luxury threshold, where the exception drops to $0 and
    the $2M promise can no longer be kept. The set is refused up front."""
    payroll = 199
    assert max_outside_signing(payroll) == 5
    check_exposure(_exp(payroll, _p(1, 5)))                       # the whole room, alone
    with pytest.raises(OfferError, match="last to land"):
        check_exposure(_exp(payroll, _p(1, 3), _p(2, 2)))


def test_bird_rights_offers_are_outside_the_soft_cap_bucket():
    payroll = 200                                                 # no outside room at all
    assert max_outside_signing(payroll) == 0
    check_exposure(_exp(payroll, _p(1, MAX_CONTRACT_VALUE, own=True)))
    with pytest.raises(OfferError, match="outside free agents"):
        check_exposure(_exp(payroll, _p(1, 1)))


def test_minimum_offers_are_free_but_still_need_a_body():
    check_exposure(_exp(HARD_CAP, _p(1, 0), _p(2, 0)))
    with pytest.raises(OfferError, match="roster spot"):
        check_exposure(_exp(HARD_CAP, _p(1, 0), roster=DEFAULT_RULES.max_roster))


def test_committed_salary_can_exclude_one_offer():
    exposure = _exp(0, _p(1, 10), _p(2, 15))
    assert committed_salary(exposure) == 25
    assert committed_salary(exposure, exclude=1) == 15


@pytest.mark.parametrize("payroll", [0, 50, 100, 140, 145, 170, 175, 199, 240])
def test_an_honourable_board_stays_honourable_as_offers_convert(payroll):
    """The load-bearing property, stated conditionally: IF a team's live offers are
    honourable, THEN signing any one of them leaves the rest honourable. Without it a
    team could be within the rules at offer time and still be unable to keep a promise
    it won through no fault of its own -- which is exactly what the 'lands last'
    condition exists to prevent.

    Every candidate set below that check_exposure rejects is skipped: rejected sets
    are the rule doing its job, and the property says nothing about them."""
    checked = 0
    for a, b, c in itertools.product((0, 1, 5, 20, MAX_CONTRACT_VALUE), repeat=3):
        offers = [_p(1, a), _p(2, b), _p(3, c)]
        exposure = _exp(payroll, *offers)
        try:
            check_exposure(exposure)
        except OfferError:
            continue                                    # not an honourable board: skip
        checked += 1
        for signed in offers:
            rest = tuple(o for o in offers if o.offer_id != signed.offer_id)
            after = TeamExposure(team_id=BOS, team_name="Boston",
                                 payroll=payroll + signed.value,
                                 roster_size=exposure.roster_size + 1, live_offers=rest)
            check_exposure(after)                       # must not raise
    assert checked, f"no honourable board at ${payroll}M payroll -- test proves nothing"


def test_check_offer_legal_reports_a_single_illegal_deal():
    with pytest.raises(OfferError, match="cannot offer"):
        check_offer_legal(_exp(SALARY_CAP), _p(1, MAX_CONTRACT_VALUE))


def test_with_pending_signing_adds_the_body_and_the_money():
    after = with_pending_signing(_exp(100, roster=15), 20)
    assert (after.payroll, after.roster_size) == (120, 16)


# -- closing the offer round -------------------------------------------------
def _auction(*offers: Offer, restricted: bool = False, rights: str | None = None,
             eligible: bool = True, illegal: frozenset = frozenset()) -> AuctionInput:
    return AuctionInput(auction_id=7, player_id="ada", player_name="Ada Vance",
                        offers=offers, restricted=restricted, rights_team_id=rights,
                        eligible=eligible, illegal_offer_ids=illegal)


def test_a_sole_offer_signs_itself():
    plan = plan_auction(_auction(_o(1, BOS, 3, 12)))
    assert (plan.status, plan.outcome) == (STATUS_RESOLVED, OUTCOME_SOLE_OFFER)
    assert (plan.winning_team_id, plan.winning_term, plan.winning_value) == (BOS, 3, 12)


def test_two_offers_open_a_bidding_board_worst_first():
    plan = plan_auction(_auction(_o(1, BOS, 3, 20), _o(2, DEN, 3, 10), _o(3, AUS, 3, 15)))
    assert plan.status == STATUS_BIDDING
    assert [s.team_id for s in plan.seats] == [DEN, AUS, BOS]
    assert plan.turn_team_id == DEN                     # the worst offer acts first


def test_a_player_nobody_offered_on_goes_unsold():
    plan = plan_auction(_auction())
    assert (plan.status, plan.outcome) == (STATUS_VOID, OUTCOME_NO_OFFERS)


def test_an_ineligible_player_voids_every_offer():
    plan = plan_auction(_auction(_o(1, BOS, 3, 12), eligible=False))
    assert (plan.status, plan.outcome) == (STATUS_VOID, OUTCOME_PLAYER_INELIGIBLE)
    assert plan.voided_offer_ids == (1,)


def test_the_losing_offers_of_a_signing_are_marked_lost():
    plan = plan_auction(_auction(_o(1, BOS, 3, 20), _o(2, DEN, 3, 10),
                                 restricted=True, rights=BOS))
    assert plan.outcome == OUTCOME_RFA_KEPT
    assert plan.lost_offer_ids == (2,)


# -- restricted resolution ---------------------------------------------------
def test_a_restricted_player_whose_own_team_leads_simply_stays():
    plan = plan_auction(_auction(_o(1, BOS, 3, 20), _o(2, DEN, 3, 10),
                                 restricted=True, rights=BOS))
    assert (plan.status, plan.outcome) == (STATUS_RESOLVED, OUTCOME_RFA_KEPT)
    assert plan.winning_team_id == BOS


def test_a_restricted_player_led_from_outside_opens_a_match_window():
    plan = plan_auction(_auction(_o(1, BOS, 3, 10), _o(2, DEN, 3, 20),
                                 restricted=True, rights=BOS))
    assert plan.status == STATUS_MATCHING
    assert plan.match_offer_id == 2                     # the top offer is the offer sheet


def test_a_restricted_player_with_no_offers_is_just_unsold():
    plan = plan_auction(_auction(restricted=True, rights=BOS))
    assert (plan.status, plan.outcome) == (STATUS_VOID, OUTCOME_NO_OFFERS)


def test_restricted_without_a_rights_holder_is_an_ordinary_free_agent():
    """rights_team_id is cleared when a player signs; a flag with nobody behind it
    must not open a match window nobody can use."""
    plan = plan_auction(_auction(_o(1, BOS, 3, 10), _o(2, DEN, 3, 20),
                                 restricted=True, rights=None))
    assert plan.status == STATUS_BIDDING


def test_declining_the_match_replans_the_board_as_unrestricted():
    board = _auction(_o(1, BOS, 3, 10), _o(2, DEN, 3, 20), restricted=True, rights=BOS)
    plan = plan_after_decline(board)
    assert plan.status == STATUS_BIDDING
    # the rights team keeps its own seat -- its lower offer is still on the table
    assert {s.team_id for s in plan.seats} == {BOS, DEN}


def test_declining_with_only_the_outside_offer_left_signs_it():
    plan = plan_after_decline(_auction(_o(2, DEN, 3, 20), restricted=True, rights=BOS))
    assert (plan.status, plan.outcome) == (STATUS_RESOLVED, OUTCOME_SOLE_OFFER)
    assert plan.winning_team_id == DEN


# -- offers that went stale before the round closed --------------------------
def test_a_stale_offer_can_turn_a_contest_into_a_sole_signing():
    plan = plan_auction(_auction(_o(1, BOS, 3, 20), _o(2, DEN, 3, 10),
                                 illegal=frozenset({1})))
    assert (plan.status, plan.outcome) == (STATUS_RESOLVED, OUTCOME_SOLE_OFFER)
    assert plan.winning_team_id == DEN
    assert plan.voided_offer_ids == (1,)


def test_a_stale_sole_offer_leaves_the_player_unsold():
    plan = plan_auction(_auction(_o(1, BOS, 3, 20), illegal=frozenset({1})))
    assert (plan.status, plan.outcome) == (STATUS_VOID, OUTCOME_NO_OFFERS)


# -- sequential bidding ------------------------------------------------------
def _board(*, turn: str, streak: int = 0, forfeited: tuple[str, ...] = ()) -> BiddingState:
    """Three teams in worst-first order DEN($10M) -> AUS($15M) -> BOS($20M)."""
    seats = (Seat(DEN, 0, DEN not in forfeited),
             Seat(AUS, 1, AUS not in forfeited),
             Seat(BOS, 2, BOS not in forfeited))
    offers = tuple(o for o in (_o(2, DEN, 3, 10), _o(3, AUS, 3, 15), _o(1, BOS, 3, 20))
                   if o.team_id not in forfeited)
    return BiddingState(auction_id=7, seats=seats, offers=offers,
                        turn_team_id=turn, no_raise_streak=streak)


def test_bidding_out_of_turn_is_refused():
    with pytest.raises(BidError, match="not your turn"):
        apply_bid(_board(turn=DEN), AUS, BID_MATCH)


def test_the_commissioner_can_act_for_a_stalling_team():
    plan = apply_bid(_board(turn=DEN), DEN, BID_FORFEIT, forced=True)
    assert plan.forfeit_seat is True


def test_a_team_that_already_forfeited_cannot_bid():
    board = _board(turn=AUS, forfeited=(DEN,))
    with pytest.raises(BidError, match="already forfeited"):
        apply_bid(board, DEN, BID_MATCH, forced=True)


def test_matching_copies_the_leader_without_taking_the_lead():
    plan = apply_bid(_board(turn=DEN), DEN, BID_MATCH)
    assert plan.new_offer is not None
    assert (plan.new_offer.term, plan.new_offer.value) == (3, 20)   # the leader's terms
    assert plan.supersede_offer_id == 2                             # replaces its own $10M
    assert plan.no_raise_streak == 1
    assert plan.turn_team_id == AUS


def test_the_leader_matches_by_standing_pat():
    plan = apply_bid(_board(turn=BOS, streak=2), BOS, BID_MATCH)
    assert plan.new_offer is None and plan.supersede_offer_id is None
    assert plan.no_raise_streak == 3


def test_a_raise_takes_the_lead_and_resets_the_streak():
    plan = apply_bid(_board(turn=DEN, streak=2), DEN, BID_RAISE, term=3, value=25)
    assert plan.no_raise_streak == 0
    assert plan.turn_team_id == AUS
    assert plan.new_offer is not None and plan.new_offer.value == 25


def test_a_raise_must_actually_beat_the_leader():
    with pytest.raises(BidError, match="does not beat"):
        apply_bid(_board(turn=DEN), DEN, BID_RAISE, term=3, value=20)


def test_a_raise_needs_numbers():
    with pytest.raises(BidError, match="needs a term"):
        apply_bid(_board(turn=DEN), DEN, BID_RAISE)


def test_forfeiting_releases_the_offer_and_passes_the_turn():
    plan = apply_bid(_board(turn=DEN), DEN, BID_FORFEIT)
    assert plan.forfeit_seat is True and plan.forfeit_offer_id == 2
    assert plan.no_raise_streak == 0                    # the field shrank: that's progress
    assert plan.turn_team_id == AUS
    assert plan.status == STATUS_BIDDING


def test_the_last_team_standing_signs_at_its_own_last_offer():
    board = _board(turn=AUS, forfeited=(DEN,))
    plan = apply_bid(board, AUS, BID_FORFEIT)
    assert (plan.status, plan.outcome) == (STATUS_RESOLVED, OUTCOME_BID_WON)
    assert plan.winning_team_id == BOS
    assert (plan.winning_term, plan.winning_value) == (3, 20)


def test_the_winner_of_a_raise_signs_the_raised_contract():
    board = _board(turn=DEN, forfeited=(AUS,))
    raised = apply_bid(board, DEN, BID_RAISE, term=4, value=30)
    assert raised.status == STATUS_BIDDING              # BOS still has a turn
    after = BiddingState(7, board.seats,
                         (_o(1, BOS, 3, 20), Offer(PENDING_OFFER_ID, DEN, 4, 30)),
                         turn_team_id=BOS, no_raise_streak=0)
    plan = apply_bid(after, BOS, BID_FORFEIT)
    assert plan.winning_team_id == DEN and plan.winning_value == 30


def test_you_cannot_forfeit_a_player_you_have_already_won():
    board = BiddingState(7, (Seat(BOS, 0, True),), (_o(1, BOS, 3, 20),), BOS, 0)
    with pytest.raises(BidError, match="only team left"):
        apply_bid(board, BOS, BID_FORFEIT)


def test_a_full_no_raise_cycle_goes_to_the_commissioner():
    """DEN matches, AUS matches, BOS stands pat -- three teams, nobody raised."""
    plan = apply_bid(_board(turn=BOS, streak=2), BOS, BID_MATCH)
    assert plan.status == STATUS_AWAITING_AWARD
    assert plan.turn_team_id is None


def test_the_cycle_does_not_fire_one_action_early():
    plan = apply_bid(_board(turn=AUS, streak=1), AUS, BID_MATCH)
    assert plan.status == STATUS_BIDDING and plan.no_raise_streak == 2


def test_two_teams_both_at_the_maximum_contract_deadlock():
    seats = (Seat(DEN, 0), Seat(BOS, 1))
    offers = (_o(1, BOS, 5, MAX_CONTRACT_VALUE), _o(2, DEN, 5, MAX_CONTRACT_VALUE))
    board = BiddingState(7, seats, offers, turn_team_id=BOS, no_raise_streak=1)
    plan = apply_bid(board, BOS, BID_MATCH)
    assert plan.status == STATUS_AWAITING_AWARD


def test_a_forfeit_resets_an_almost_complete_cycle():
    plan = apply_bid(_board(turn=AUS, streak=2), AUS, BID_FORFEIT)
    assert plan.status == STATUS_BIDDING and plan.no_raise_streak == 0


def test_an_emptied_board_voids():
    board = BiddingState(7, (Seat(DEN, 0), Seat(BOS, 1, active=False)),
                         (_o(2, DEN, 3, 10),), turn_team_id=DEN, no_raise_streak=0)
    plan = apply_bid(board, DEN, BID_FORFEIT, forced=True)
    assert (plan.status, plan.outcome) == (STATUS_VOID, OUTCOME_ALL_FORFEITED)


def test_turn_passing_skips_forfeited_seats_and_wraps():
    board = _board(turn=DEN, forfeited=(AUS,))
    assert next_turn(board, DEN) == BOS
    assert next_turn(board, BOS) == DEN


def test_deadlock_needs_at_least_one_team():
    empty = BiddingState(7, (Seat(BOS, 0, active=False),), (), None, 5)
    assert deadlocked(empty) is False


def test_an_unknown_action_is_refused():
    with pytest.raises(BidError, match="unknown bid action"):
        apply_bid(_board(turn=DEN), DEN, "shrug")


# -- the commissioner's award ------------------------------------------------
def test_an_award_signs_that_teams_own_offer():
    deal = check_award(_board(turn=None, streak=3), AUS)
    assert (deal.term, deal.value) == (3, 15)


def test_an_award_cannot_go_to_a_team_that_walked_away():
    with pytest.raises(BidError, match="still bidding"):
        check_award(_board(turn=None, streak=3, forfeited=(AUS,)), AUS)
