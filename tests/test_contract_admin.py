"""
Unit tests for the bulk contract planner (handball/contract_admin.py). The planner is
a pure function of a list of ContractRow, so everything here runs with no DB --
load_contracts()/apply_bulk() are covered against Postgres in tests/test_api.py.
"""
import pytest

from handball.contract_admin import (
    RESTART,
    BulkContractError,
    ContractRow,
    Override,
    plan_bulk_contracts,
)
from handball.simulation_vars import HARD_CAP, MAX_CONTRACT_VALUE, MAX_CONTRACT_YEARS


def _row(pid: str, *, team: str | None = "Boston", term: int = 3, value: int = 10,
         years: int = 0, rookie: bool = False, rfa: bool = False) -> ContractRow:
    return ContractRow(
        player_id=pid, name=pid.upper(),
        team_id=None if team is None else f"uuid-{team}", team_name=team,
        contract_term=term, contract_value=value, years_remaining=years,
        rookie_contract=rookie, restricted_free_agent=rfa,
    )


# -- the restart strategy ----------------------------------------------------
def test_an_expired_counter_is_restarted_at_the_existing_term():
    plan = plan_bulk_contracts([_row("p1", term=4, value=12, years=-7)])
    assert len(plan.changes) == 1
    c = plan.changes[0]
    assert (c.term_before, c.term_after) == (4, 4)        # the deal is unchanged...
    assert (c.value_before, c.value_after) == (12, 12)
    assert (c.years_before, c.years_after) == (-7, 4)     # ...only the counter moves
    assert c.reason == "restart"


def test_a_live_contract_is_left_alone():
    plan = plan_bulk_contracts([_row("p1", term=4, years=2)])
    assert plan.changes == ()
    assert plan.unchanged == 1


def test_a_counter_at_zero_counts_as_expired():
    """years_remaining == 0 is released by the very next rollover, so it is exactly
    the case the repair is for -- not a live deal."""
    assert len(plan_bulk_contracts([_row("p1", years=0)]).changes) == 1


def test_a_player_with_no_term_to_restart_is_named_not_guessed():
    with pytest.raises(BulkContractError) as e:
        plan_bulk_contracts([_row("p1", term=0, years=-3)])
    assert "no deal to restart" in e.value.problems[0]
    assert "p1" in e.value.problems[0]


def test_strategy_none_changes_nothing_but_still_reports():
    plan = plan_bulk_contracts([_row("p1", years=-5), _row("p2", years=-5)],
                               strategy="none")
    assert plan.changes == ()
    assert plan.expiring_next_rollover == 2      # what the rollover would do today


def test_an_unknown_strategy_is_refused():
    with pytest.raises(BulkContractError, match="unknown strategy"):
        plan_bulk_contracts([_row("p1")], strategy="restart-everything")


# -- overrides ---------------------------------------------------------------
def test_an_override_sets_term_and_value():
    plan = plan_bulk_contracts(
        [_row("p1", term=2, value=5, years=1)],
        strategy="none", overrides=[Override("p1", term=5, value=30)])
    c = plan.changes[0]
    assert (c.term_after, c.value_after, c.years_after) == (5, 30, 5)
    assert c.reason == "override"


def test_an_override_applies_to_a_live_contract_too():
    """The strategy only touches expired deals; an override is the commissioner
    deciding, so it binds whatever the counter says."""
    assert len(plan_bulk_contracts([_row("p1", years=4)], strategy="none",
                                   overrides=[Override("p1", 1, 0)]).changes) == 1


def test_an_override_wins_over_the_restart_strategy():
    plan = plan_bulk_contracts([_row("p1", term=3, value=10, years=-2)],
                               strategy=RESTART,
                               overrides=[Override("p1", term=1, value=1)])
    assert len(plan.changes) == 1                 # not restarted AND overridden
    assert plan.changes[0].reason == "override"
    assert plan.changes[0].years_after == 1


def test_an_override_matching_the_current_deal_is_not_a_change():
    plan = plan_bulk_contracts([_row("p1", term=3, value=10, years=3)],
                               strategy="none",
                               overrides=[Override("p1", term=3, value=10)])
    assert plan.changes == ()


def test_a_restart_of_an_illegal_stored_deal_is_refused_not_applied():
    """The rows this tool repairs predate the contract rules, so the term and value
    a restart re-uses are exactly the ones that might not satisfy them. The planner
    has to catch that: apply_bulk must never fail on a plan that passed."""
    with pytest.raises(BulkContractError) as e:
        plan_bulk_contracts([_row("p1", term=MAX_CONTRACT_YEARS + 3, years=-4)])
    assert "out of range" in e.value.problems[0]
    assert "explicit override" in e.value.problems[0]


def test_a_restart_of_an_over_max_stored_value_is_refused():
    with pytest.raises(BulkContractError, match="out of range"):
        plan_bulk_contracts([_row("p1", value=MAX_CONTRACT_VALUE + 10, years=-4)])


def test_an_illegal_override_is_refused_with_the_rules_reason():
    with pytest.raises(BulkContractError) as e:
        plan_bulk_contracts([_row("p1")], strategy="none",
                            overrides=[Override("p1", MAX_CONTRACT_YEARS + 1, 10)])
    assert "out of range" in e.value.problems[0]


def test_an_override_over_the_max_value_is_refused():
    with pytest.raises(BulkContractError, match="out of range"):
        plan_bulk_contracts([_row("p1")], strategy="none",
                            overrides=[Override("p1", 1, MAX_CONTRACT_VALUE + 1)])


def test_an_override_for_an_unknown_player_is_refused():
    with pytest.raises(BulkContractError, match="no such player"):
        plan_bulk_contracts([_row("p1")], strategy="none",
                            overrides=[Override("ghost", 1, 1)])


def test_a_player_named_twice_is_refused():
    with pytest.raises(BulkContractError, match="named twice"):
        plan_bulk_contracts([_row("p1")], strategy="none",
                            overrides=[Override("p1", 1, 1), Override("p1", 2, 2)])


def test_every_bad_override_is_reported_at_once():
    with pytest.raises(BulkContractError) as e:
        plan_bulk_contracts(
            [_row("p1")], strategy="none",
            overrides=[Override("ghost", 1, 1), Override("p1", 99, 1)])
    assert len(e.value.problems) == 2


# -- the cap rule ------------------------------------------------------------
def _team_at(payroll: int, *, n: int = 12, team: str = "Boston") -> list[ContractRow]:
    """n players sharing `payroll`, the last one absorbing the remainder. n is high
    enough that no individual share approaches MAX_CONTRACT_VALUE -- a team at the
    hard cap on four contracts would be illegal player by player."""
    each = payroll // n
    rows = [_row(f"{team}-{i}", team=team, value=each, years=3) for i in range(n - 1)]
    rows.append(_row(f"{team}-{n - 1}", team=team, value=payroll - each * (n - 1), years=3))
    return rows


def test_a_raise_that_breaks_the_hard_cap_is_refused():
    rows = _team_at(HARD_CAP - 5)
    with pytest.raises(BulkContractError) as e:
        plan_bulk_contracts(rows, strategy="none",
                            overrides=[Override(rows[0].player_id, 3,
                                                rows[0].contract_value + 20)])
    assert "hard cap" in e.value.problems[0]


def test_a_raise_within_the_hard_cap_is_allowed():
    rows = _team_at(HARD_CAP - 30)
    plan = plan_bulk_contracts(rows, strategy="none",
                               overrides=[Override(rows[0].player_id, 3,
                                                   rows[0].contract_value + 20)])
    assert len(plan.changes) == 1
    assert [p.after - p.before for p in plan.payrolls] == [20]


def test_a_team_already_over_the_hard_cap_is_not_blocked_by_a_plan_that_moves_no_money():
    """Six real teams sit over the hard cap on rookie deals, which is a LEGAL state.
    A repair that only restarts counters must not be refused because of it --
    assert_trade_hard_cap demands a strict reduction from a team already over, which
    is the right rule for a trade and the wrong one for an untouched payroll."""
    rows = _team_at(HARD_CAP + 40)
    rows = [ContractRow(**{**r.__dict__, "years_remaining": -4}) for r in rows]
    plan = plan_bulk_contracts(rows, strategy=RESTART)
    assert len(plan.changes) == len(rows)
    assert all(p.before == p.after for p in plan.payrolls)


def test_a_team_already_over_the_hard_cap_may_still_be_cut_down():
    rows = _team_at(HARD_CAP + 40)
    plan = plan_bulk_contracts(rows, strategy="none",
                               overrides=[Override(rows[0].player_id, 2, 0)])
    assert plan.payrolls[0].after < plan.payrolls[0].before


def test_a_team_already_over_the_hard_cap_may_not_be_pushed_further():
    rows = _team_at(HARD_CAP + 40)
    with pytest.raises(BulkContractError, match="already over"):
        plan_bulk_contracts(rows, strategy="none",
                            overrides=[Override(rows[0].player_id, 2,
                                                rows[0].contract_value + 5)])


def test_a_free_agent_is_on_nobody_s_payroll():
    plan = plan_bulk_contracts([_row("fa", team=None, years=-2)], strategy=RESTART)
    assert len(plan.changes) == 1
    assert plan.payrolls == ()
    assert plan.expiry_cohorts == {}            # and cannot be "released" either


# -- what the plan tells the commissioner ------------------------------------
def test_the_plan_reports_the_next_rollover_before_and_after():
    """The number the whole tool exists for: a league of expired counters loses
    everyone, and the repair is only worth running if that number comes down."""
    rows = [_row("p1", term=1, years=-3), _row("p2", term=2, years=-3),
            _row("p3", term=5, years=-3)]
    plan = plan_bulk_contracts(rows, strategy=RESTART)
    assert plan.expiring_before == 3            # all three, as things stand
    assert plan.expiring_next_rollover == 1     # only the 1-year deal, afterwards
    assert plan.expiry_cohorts == {1: 1, 2: 1, 5: 1}


def test_a_one_year_deal_still_expires_immediately():
    """Aging ticks the counter down and THEN releases at <= 0, so a 1 is expiring."""
    plan = plan_bulk_contracts([_row("p1", term=1, years=-9)], strategy=RESTART)
    assert plan.expiring_next_rollover == 1


def test_payrolls_are_reported_per_team():
    rows = [_row("b1", team="Boston", value=10, years=-1),
            _row("d1", team="Denver", value=20, years=-1)]
    plan = plan_bulk_contracts(rows, strategy=RESTART)
    assert [(p.team_name, p.before, p.after) for p in plan.payrolls] == [
        ("Boston", 10, 10), ("Denver", 20, 20)]


def test_the_serialized_plan_only_lists_payrolls_that_moved():
    rows = [_row("b1", team="Boston", value=10, years=3),
            _row("d1", team="Denver", value=20, years=3)]
    plan = plan_bulk_contracts(rows, strategy="none",
                               overrides=[Override("b1", 3, 15)])
    body = plan.as_dict()
    assert [p["team"] for p in body["payrolls"]] == ["Boston"]
    assert body["changed"] == 1 and body["unchanged"] == 1
    assert set(body["expiry_cohorts"]) == {"3"}          # JSON-safe string keys


def test_an_empty_league_plans_nothing():
    plan = plan_bulk_contracts([], strategy=RESTART)
    assert plan.changes == () and plan.expiring_next_rollover == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
