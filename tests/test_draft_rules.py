"""
The draft's decisions, DB-free (handball/draft_rules.py): the lottery draw, the
order the rollover writes, protection resolution, the rookie scale, and what the
clock picks. Everything here is a pure function of its arguments, so none of it
needs Postgres and none of it is skipped.

`pytest tests/test_draft_rules.py`.
"""
import random

import pytest

from handball import draft_rules as dr
from handball.simulation_vars import DRAFT_ROUNDS, LOTTERY_WEIGHTS, ROOKIE_SCALE

TEAMS = [f"T{i:02d}" for i in range(1, 33)]        # T01 best .. T32 worst
POOL = [f"P{i:02d}" for i in range(1, 17)]         # worst-first lottery pool


def test_the_rules_module_does_not_touch_a_database():
    """The same guard tests/test_free_agency_rules.py puts on its pure layer: if
    sqlalchemy ever appears here, the split has quietly stopped being real."""
    import inspect

    source = inspect.getsource(dr)
    assert "sqlalchemy" not in source
    assert "import handball.draft\n" not in source


# --- the lottery -----------------------------------------------------------
def test_lottery_returns_every_team_exactly_once():
    drawn = dr.draw_lottery(POOL, random.Random(7))
    assert sorted(drawn) == sorted(POOL)
    assert len(drawn) == len(POOL)


def test_lottery_is_reproducible_for_a_seed():
    a = dr.draw_lottery(POOL, random.Random(20260912))
    b = dr.draw_lottery(POOL, random.Random(20260912))
    assert a == b
    # ...and a different seed generally gives a different draw, which is the other
    # half of what "reproducible" has to mean to be worth anything.
    assert any(dr.draw_lottery(POOL, random.Random(s)) != a for s in range(1, 40))


def test_lottery_does_not_touch_the_module_level_rng():
    """The draw takes an injected Random on purpose. If it ever reached for the
    global one, a draw could not be replayed from its stored seed."""
    random.seed(1)
    before = random.random()
    random.seed(1)
    dr.draw_lottery(POOL, random.Random(3))
    assert random.random() == before


def test_weights_renormalize_after_each_removal():
    """Weights are shares, not probabilities: scaling the whole table by a constant
    -- or writing one that sums to 3 instead of 100 -- cannot change a draw."""
    seed = 99
    base = dr.draw_lottery(POOL, random.Random(seed), LOTTERY_WEIGHTS)
    scaled = dr.draw_lottery(POOL, random.Random(seed),
                             tuple(w * 37 for w in LOTTERY_WEIGHTS))
    fractional = dr.draw_lottery(POOL, random.Random(seed),
                                 tuple(w / 100 for w in LOTTERY_WEIGHTS))
    assert base == scaled == fractional


def test_weights_are_normalized_over_whoever_is_left():
    assert dr.normalized_weights((25, 25, 50), 3) == [0.25, 0.25, 0.5]
    assert dr.normalized_weights((25, 25, 50), 2) == [0.5, 0.5]
    assert sum(dr.normalized_weights(LOTTERY_WEIGHTS, 16)) == pytest.approx(1.0)
    assert sum(dr.normalized_weights(LOTTERY_WEIGHTS, 4)) == pytest.approx(1.0)


def test_a_pool_longer_than_the_weight_table_still_draws():
    """A league that grows should get a slightly-too-flat tail, not a refusal."""
    long_pool = [f"X{i}" for i in range(20)]
    drawn = dr.draw_lottery(long_pool, random.Random(1))
    assert sorted(drawn) == sorted(long_pool)
    tail = dr.normalized_weights(LOTTERY_WEIGHTS, 20)
    assert tail[-1] == pytest.approx(tail[-5])          # extended with the last weight


def test_lottery_refuses_an_empty_pool_or_useless_weights():
    with pytest.raises(dr.DraftRulesError):
        dr.draw_lottery([], random.Random(1))
    with pytest.raises(dr.DraftRulesError):
        dr.draw_lottery(POOL, random.Random(1), (0, 0, 0))
    with pytest.raises(dr.DraftRulesError):
        dr.draw_lottery(POOL, random.Random(1), (-1, 2))


def test_the_worst_team_wins_the_first_pick_far_more_often_than_the_best():
    """Not a distribution proof -- just that the weights point the right way, which
    is the failure a transposed table would produce and nothing else would catch."""
    firsts = [dr.draw_lottery(POOL, random.Random(s))[0] for s in range(600)]
    assert firsts.count(POOL[0]) > firsts.count(POOL[-1]) * 5


# --- the order -------------------------------------------------------------
def _bracket(ranked):
    """A 32-team postseason over `ranked` (best->worst): the top 16 make it, and
    the better-ranked team wins every series. Returns (losers_by_round, champion)."""
    alive = list(ranked[:16])
    losers, rnd = {}, 1
    while len(alive) > 1:
        # pair best-vs-worst, better seed advances
        pairs = [(alive[i], alive[len(alive) - 1 - i]) for i in range(len(alive) // 2)]
        losers[rnd] = [low for _, low in pairs]
        alive = [high for high, _ in pairs]
        rnd += 1
    return losers, alive[0]


def test_round_one_is_the_lottery_pool_then_the_playoff_teams_by_how_far_they_went():
    losers, champion = _bracket(TEAMS)
    order = dr.build_draft_order(TEAMS, losers, champion)

    # the 16 who missed, worst record first -> the lottery pool (picks 1-16)
    assert list(order.lottery_pool) == list(reversed(TEAMS[16:]))
    assert order.lottery_slots == 16

    # picks 17-32: first-round losers, then second, then conference finals,
    # then the runner-up, then the champion.
    playoff = list(order.round_one_playoff)
    assert len(playoff) == 16
    assert playoff[-1] == TEAMS[0]                       # champion picks last
    assert playoff[-2] == TEAMS[1]                       # runner-up picks 31st
    assert set(playoff[:8]) == set(losers[1])            # first-round losers pick 17-24
    assert set(playoff[8:12]) == set(losers[2])
    assert set(playoff[12:14]) == set(losers[3])


def test_within_a_round_the_worse_record_picks_first():
    losers, champion = _bracket(TEAMS)
    playoff = list(dr.build_draft_order(TEAMS, losers, champion).round_one_playoff)
    rank = {t: i for i, t in enumerate(TEAMS)}
    first_round = playoff[:8]
    assert first_round == sorted(first_round, key=lambda t: -rank[t])


def test_round_two_puts_every_playoff_team_behind_every_non_playoff_team():
    """The only reading of 'no better than the 17th pick of the second round'."""
    losers, champion = _bracket(TEAMS)
    order = dr.build_draft_order(TEAMS, losers, champion)
    round_two = list(order.round_two)

    assert len(round_two) == 32
    assert round_two[:16] == list(reversed(TEAMS[16:]))   # missed the playoffs, worst first
    assert round_two[16:] == list(reversed(TEAMS[:16]))   # made them, worst first
    # round 2 ignores the bracket beyond that: a champion with a bad record still
    # picks ahead of a runner-up with a good one.
    assert round_two[16] == TEAMS[15]


def test_no_postseason_degrades_to_plain_reverse_standings():
    order = dr.build_draft_order(TEAMS)
    assert list(order.lottery_pool) == list(reversed(TEAMS))
    assert order.round_one_playoff == ()
    assert list(order.round_two) == list(reversed(TEAMS))


def test_a_half_played_bracket_orders_the_rounds_it_finished():
    losers, _ = _bracket(TEAMS)
    partial = {1: losers[1]}                       # only the first round is decided
    order = dr.build_draft_order(TEAMS, partial, None)
    assert len(order.round_one_playoff) == 8
    assert len(order.lottery_pool) == 24           # everyone still alive counts as "missed"


def test_pick_coordinates_walk_the_rounds():
    assert dr.pick_coordinates(1, 32) == (1, 1)
    assert dr.pick_coordinates(32, 32) == (1, 32)
    assert dr.pick_coordinates(33, 32) == (2, 1)
    assert dr.pick_coordinates(64, 32) == (2, 32)
    with pytest.raises(dr.DraftRulesError):
        dr.pick_coordinates(0, 32)


# --- protections -----------------------------------------------------------
def test_a_protection_is_inclusive_on_its_boundary():
    assert dr.protection_outcome(3, 3) == dr.PROTECTION_REVERTED
    assert dr.protection_outcome(4, 3) == dr.PROTECTION_CONVEYED
    assert dr.protection_outcome(1, 1) == dr.PROTECTION_REVERTED


def test_a_caught_pick_goes_home_and_a_missed_one_conveys():
    holder, outcome = dr.resolve_protection(2, 5, holder="Denver", original="Boston")
    assert (holder, outcome) == ("Boston", dr.PROTECTION_REVERTED)

    holder, outcome = dr.resolve_protection(9, 5, holder="Denver", original="Boston")
    assert (holder, outcome) == ("Denver", dr.PROTECTION_CONVEYED)


def test_a_protection_on_an_untraded_pick_is_an_error():
    with pytest.raises(dr.DraftRulesError):
        dr.resolve_protection(1, 5, holder="Boston", original="Boston")
    with pytest.raises(dr.DraftRulesError):
        dr.protection_outcome(1, 0)


# --- the rookie scale ------------------------------------------------------
def test_the_rookie_scale_prices_every_pick_in_the_draft():
    picks = len(TEAMS) * DRAFT_ROUNDS
    deals = [dr.rookie_deal(n) for n in range(1, picks + 1)]
    assert len(deals) == 64
    assert deals[0] == (5, 5)          # 1st overall
    assert deals[9] == (5, 5)          # 10th
    assert deals[10] == (5, 4)         # 11th -- the next band
    assert deals[31] == (5, 3)         # 32nd, the last of round 1
    assert deals[32] == (2, 2)         # 33rd, the first of round 2
    assert deals[63] == (2, 1)         # 64th


def test_the_scale_never_goes_up_as_the_picks_go_down():
    values = [dr.rookie_deal(n)[1] for n in range(1, 65)]
    assert values == sorted(values, reverse=True)


def test_a_pick_outside_the_scale_is_an_error_not_a_default():
    with pytest.raises(dr.DraftRulesError):
        dr.rookie_deal(65)
    with pytest.raises(dr.DraftRulesError):
        dr.rookie_deal(0)
    assert dr.rookie_deal(1, ROOKIE_SCALE) == (5, 5)


# --- what the clock picks --------------------------------------------------
def _p(ord_, off=0.0, deff=0.0, gk=0.0):
    return {"id": f"p{ord_}", "ord": ord_, "offense": off, "defense": deff,
            "goalie_skill": gk}


def test_best_available_takes_the_highest_total_rating():
    board = [_p(1, 5, 5), _p(2, 7, 4), _p(3, 4, 4)]
    assert dr.best_available(board)["id"] == "p2"        # 11 > 10 > 8


def test_best_available_rates_a_goalie_on_the_same_sum():
    goalie = _p(1, 0.1, 0.1, 9.0)
    skater = _p(2, 4.0, 4.0, 0.1)
    assert dr.best_available([goalie, skater])["id"] == "p1"


def test_best_available_breaks_ties_on_upload_order():
    board = [_p(5, 5, 5), _p(2, 5, 5), _p(9, 5, 5)]
    assert dr.best_available(board)["ord"] == 2
    # ...and does so deterministically however the list arrives
    assert dr.best_available(list(reversed(board)))["ord"] == 2


def test_best_available_refuses_an_empty_board():
    with pytest.raises(dr.DraftRulesError):
        dr.best_available([])


def test_prospect_rating_tolerates_missing_columns():
    assert dr.prospect_rating({"offense": 5.0}) == pytest.approx(5.0)
    assert dr.prospect_rating({"offense": None, "goalie_skill": 3.0}) == pytest.approx(3.0)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
