"""
Unit tests for the salary-cap / contract rules (handball/salary_cap.py). Pure
functions over payroll -- no DB, always run.
"""
import pytest

from handball.salary_cap import (
    CapSituation,
    ContractError,
    assert_trade_hard_cap,
    assert_within_hard_cap,
    can_sign,
    hard_cap_overage,
    cap_situation,
    check_signing,
    max_offer,
    max_outside_signing,
    mid_level_exception,
    validate_contract,
)
from handball.simulation_vars import (
    FIRST_LUXURY_TAX_THRESHOLD as T1,
    FIRST_MLE,
    HARD_CAP,
    MAX_CONTRACT_VALUE,
    MAX_CONTRACT_YEARS,
    SALARY_CAP,
    SECOND_LUXURY_TAX_THRESHOLD as T2,
    SECOND_MLE,
)


# -- mid-level exception -----------------------------------------------------
def test_mle_tiers():
    assert mid_level_exception(0) == FIRST_MLE
    assert mid_level_exception(T1 - 1) == FIRST_MLE
    assert mid_level_exception(T1) == SECOND_MLE          # at first threshold -> second tier
    assert mid_level_exception(T2 - 1) == SECOND_MLE
    assert mid_level_exception(T2) == 0                   # at second threshold -> none
    assert mid_level_exception(HARD_CAP) == 0


# -- cap situation -----------------------------------------------------------
def test_cap_situation_under_cap():
    s = cap_situation(100)
    assert isinstance(s, CapSituation)
    assert s.cap_room == SALARY_CAP - 100
    assert not s.over_cap
    assert not s.over_first_threshold and not s.over_second_threshold
    assert s.mid_level_exception == FIRST_MLE
    assert s.hard_cap_room == HARD_CAP - 100


def test_cap_situation_over_cap_clamps_room():
    s = cap_situation(SALARY_CAP + 30)
    assert s.cap_room == 0                                # never negative
    assert s.over_cap
    assert s.hard_cap_room == HARD_CAP - (SALARY_CAP + 30)


def test_cap_situation_thresholds():
    assert cap_situation(T1).over_first_threshold
    assert not cap_situation(T1 - 1).over_first_threshold
    assert cap_situation(T2).over_second_threshold


# -- contract validation -----------------------------------------------------
def test_validate_contract_ok():
    validate_contract(MAX_CONTRACT_YEARS, MAX_CONTRACT_VALUE)   # boundaries allowed
    validate_contract(1, 0)                                     # minimum ($0) contract


@pytest.mark.parametrize("term", [0, MAX_CONTRACT_YEARS + 1, -1])
def test_validate_contract_bad_term(term):
    with pytest.raises(ContractError):
        validate_contract(term, 10)


@pytest.mark.parametrize("value", [-1, MAX_CONTRACT_VALUE + 1])
def test_validate_contract_bad_value(value):
    with pytest.raises(ContractError):
        validate_contract(3, value)


# -- max outside signing -----------------------------------------------------
def test_max_outside_signing_deep_under_cap():
    # far under the cap: room = cap headroom + MLE, still within the hard cap.
    assert max_outside_signing(50) == (SALARY_CAP - 50) + FIRST_MLE


def test_max_outside_signing_over_cap_is_just_mle():
    # over the cap but below the first threshold: no cap room, only the MLE.
    payroll = SALARY_CAP + 10
    assert payroll < T1
    assert max_outside_signing(payroll) == FIRST_MLE


def test_max_outside_signing_above_second_threshold_is_zero():
    assert max_outside_signing(T2 + 5) == 0               # no MLE, can only add minimums


def test_max_outside_signing_never_breaks_hard_cap():
    # For every payroll, using the full outside allowance must never cross the
    # hard cap (the min(HARD_CAP, ...) clamp is defensive; with these constants it
    # only ever binds once MLE has already dropped to 0 above the second threshold).
    for payroll in range(0, HARD_CAP + 1, 5):
        assert payroll + max_outside_signing(payroll) <= HARD_CAP


# -- max_offer (the "what can I offer?" read of the same rules) --------------
def test_max_offer_outside_matches_the_outside_allowance():
    assert max_offer(50, own_player=False) == min(MAX_CONTRACT_VALUE, max_outside_signing(50))


def test_max_offer_own_player_is_bounded_by_the_hard_cap_only():
    payroll = SALARY_CAP + 40                             # no outside room left
    assert max_offer(payroll, own_player=True) == MAX_CONTRACT_VALUE
    assert max_offer(payroll, own_player=False) < MAX_CONTRACT_VALUE
    assert max_offer(HARD_CAP - 7, own_player=True) == 7


def test_max_offer_never_exceeds_the_contract_maximum():
    for payroll in range(0, HARD_CAP + 1, 5):
        for own in (True, False):
            assert max_offer(payroll, own_player=own) <= MAX_CONTRACT_VALUE


def test_max_offer_is_exactly_what_can_sign_accepts():
    for payroll in range(0, HARD_CAP + 1, 5):
        for own in (True, False):
            ceiling = max_offer(payroll, own_player=own)
            assert can_sign(payroll, ceiling, own_player=own)
            if ceiling < MAX_CONTRACT_VALUE:
                assert not can_sign(payroll, ceiling + 1, own_player=own)


def test_max_offer_bottoms_out_at_zero_not_negative():
    assert max_offer(HARD_CAP + 20, own_player=True) == 0   # over the cap on rookie deals
    assert max_offer(HARD_CAP + 20, own_player=False) == 0


# -- can_sign ----------------------------------------------------------------
def test_can_sign_outside_within_room():
    assert can_sign(SALARY_CAP + 5, SECOND_MLE, own_player=False) is (SALARY_CAP + 5 < T1)


def test_can_sign_outside_beyond_mle_rejected():
    payroll = SALARY_CAP + 10                             # below T1 -> MLE only
    assert not can_sign(payroll, FIRST_MLE + 1, own_player=False)
    assert can_sign(payroll, FIRST_MLE, own_player=False)


def test_own_player_may_exceed_soft_cap_but_not_hard_cap():
    payroll = SALARY_CAP + 40                             # well over the soft cap
    assert can_sign(payroll, 30, own_player=True)         # Bird rights
    assert not can_sign(payroll, 30, own_player=False)    # outside signing blocked
    # hard cap still binds even for own players
    assert not can_sign(HARD_CAP - 10, 20, own_player=True)


def test_minimum_contract_always_fits():
    # $0 contracts never move payroll, so they fit at any legal payroll.
    assert can_sign(HARD_CAP, 0, own_player=False)
    assert can_sign(HARD_CAP, 0, own_player=True)


# -- check_signing (full validation with reasons) ----------------------------
def test_check_signing_hard_cap_message():
    with pytest.raises(ContractError, match="hard cap"):
        check_signing(HARD_CAP - 5, 3, 20, own_player=True)


def test_check_signing_outside_room_message():
    with pytest.raises(ContractError, match="available room"):
        check_signing(SALARY_CAP + 10, 3, FIRST_MLE + 5, own_player=False)


def test_check_signing_contract_limit_message():
    with pytest.raises(ContractError):
        check_signing(50, MAX_CONTRACT_YEARS + 1, 10, own_player=False)


def test_check_signing_ok():
    check_signing(50, 4, 30, own_player=False)            # plenty of room, valid contract


# -- assert_within_hard_cap (signing path) -----------------------------------
def test_assert_within_hard_cap():
    assert_within_hard_cap(HARD_CAP)                      # exactly at the cap is fine
    with pytest.raises(ContractError, match="hard cap"):
        assert_within_hard_cap(HARD_CAP + 1, label="Boston")


# -- hard_cap_overage --------------------------------------------------------
def test_hard_cap_overage():
    assert hard_cap_overage(HARD_CAP - 1) == 0
    assert hard_cap_overage(HARD_CAP) == 0
    assert hard_cap_overage(HARD_CAP + 12) == 12


# -- assert_trade_hard_cap (trade path) --------------------------------------
def test_trade_within_cap_is_fine():
    assert_trade_hard_cap(100, HARD_CAP)                  # ends exactly at the cap
    assert_trade_hard_cap(HARD_CAP, HARD_CAP - 5)         # ends under it


def test_trade_may_not_cross_the_cap():
    with pytest.raises(ContractError, match="would be over"):
        assert_trade_hard_cap(HARD_CAP - 5, HARD_CAP + 1, label="Boston")


def test_over_cap_team_may_trade_down_but_not_up():
    # A team over the cap on rookie deals HAS to be able to trade its way back.
    assert_trade_hard_cap(HARD_CAP + 20, HARD_CAP + 5)    # still over, but closer
    assert_trade_hard_cap(HARD_CAP + 20, HARD_CAP - 1)    # all the way into compliance
    with pytest.raises(ContractError, match="already over"):
        assert_trade_hard_cap(HARD_CAP + 20, HARD_CAP + 20, label="Boston")   # no change
    with pytest.raises(ContractError, match="already over"):
        assert_trade_hard_cap(HARD_CAP + 20, HARD_CAP + 25, label="Boston")   # worse


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
