"""
Unit tests for the free-agent signing RULES (handball/signing_service.py's pure
layer) and the shared canonical-layout rules (handball/roster_layout.py). Both are
pure functions over a snapshot, so these run with no DB -- the SQL write path is
covered by tests/test_signing_service.py (Postgres) and tests/test_api.py.
"""
import pytest

from handball.domain import Player
from handball.league_views import DEFAULT_RULES
from handball.roster_layout import RosterLayoutError, canonical_team
from handball.salary_cap import max_offer
from handball.signing_service import (
    SigningContext,
    SigningError,
    check_signing_allowed,
    has_bird_rights,
    offer_ceiling,
)
from handball.simulation_vars import (
    FIRST_MLE,
    HARD_CAP,
    MAX_CONTRACT_VALUE,
    MAX_CONTRACT_YEARS,
    SALARY_CAP,
)

TEAM = "team-uuid-1"
OTHER = "team-uuid-2"


def _ctx(
    *,
    payroll: int = 100,
    roster_size: int = 19,
    retired: bool = False,
    current_team_id: str | None = None,
    rights_team_id: str | None = None,
) -> SigningContext:
    return SigningContext(
        team_id=TEAM,
        team_name="Boston Foxes",
        payroll=payroll,
        roster_size=roster_size,
        player_id="free-agent-1",
        player_name="Ada Vance",
        retired=retired,
        current_team_id=current_team_id,
        rights_team_id=rights_team_id,
    )


# -- Bird rights -------------------------------------------------------------
def test_bird_rights_only_for_the_team_a_contract_expired_off():
    assert has_bird_rights(_ctx(rights_team_id=TEAM)) is True
    assert has_bird_rights(_ctx(rights_team_id=OTHER)) is False
    assert has_bird_rights(_ctx(rights_team_id=None)) is False


# -- eligibility -------------------------------------------------------------
def test_a_plain_outside_signing_under_the_cap_is_allowed():
    check_signing_allowed(_ctx(payroll=100), 3, 20)        # no raise


def test_a_retired_player_cannot_be_signed():
    with pytest.raises(SigningError, match="retired"):
        check_signing_allowed(_ctx(retired=True), 3, 5)


def test_a_player_under_contract_elsewhere_must_be_traded_for():
    with pytest.raises(SigningError, match="trade"):
        check_signing_allowed(_ctx(current_team_id=OTHER), 3, 5)


def test_your_own_rostered_player_is_not_a_free_agent():
    with pytest.raises(SigningError, match="already under contract"):
        check_signing_allowed(_ctx(current_team_id=TEAM), 3, 5)


def test_eligibility_is_reported_before_the_cap():
    """A retired player is rejected as retired, not as unaffordable -- the manager
    should hear the disqualifying reason, not a cap number."""
    with pytest.raises(SigningError, match="retired"):
        check_signing_allowed(_ctx(retired=True, payroll=HARD_CAP), 3, MAX_CONTRACT_VALUE)


def test_a_full_roster_blocks_a_signing():
    with pytest.raises(SigningError, match="roster is full"):
        check_signing_allowed(_ctx(roster_size=DEFAULT_RULES.max_roster, payroll=0), 3, 5)


def test_the_last_roster_spot_can_still_be_used():
    check_signing_allowed(_ctx(roster_size=DEFAULT_RULES.max_roster - 1, payroll=0), 3, 5)


# -- money -------------------------------------------------------------------
def test_an_outside_signing_is_capped_at_cap_room_plus_the_mle():
    ctx = _ctx(payroll=SALARY_CAP)                     # no cap room; MLE only
    check_signing_allowed(ctx, 2, FIRST_MLE)
    with pytest.raises(SigningError, match="exceeds available room"):
        check_signing_allowed(ctx, 2, FIRST_MLE + 1)


def test_bird_rights_let_a_re_signing_blow_past_the_soft_cap():
    ctx = _ctx(payroll=SALARY_CAP + 20, rights_team_id=TEAM)
    check_signing_allowed(ctx, 4, MAX_CONTRACT_VALUE)  # would be illegal from outside
    outside = _ctx(payroll=SALARY_CAP + 20, rights_team_id=OTHER)
    with pytest.raises(SigningError, match="exceeds available room"):
        check_signing_allowed(outside, 4, MAX_CONTRACT_VALUE)


def test_even_a_re_signing_stops_at_the_hard_cap():
    ctx = _ctx(payroll=HARD_CAP - 5, rights_team_id=TEAM)
    check_signing_allowed(ctx, 1, 5)
    with pytest.raises(SigningError, match="hard cap"):
        check_signing_allowed(ctx, 1, 6)


@pytest.mark.parametrize("term,value", [(0, 5), (MAX_CONTRACT_YEARS + 1, 5),
                                       (3, MAX_CONTRACT_VALUE + 1), (3, -1)])
def test_contract_limits_are_enforced_on_a_signing(term, value):
    with pytest.raises(SigningError, match="out of range"):
        check_signing_allowed(_ctx(payroll=0), term, value)


def test_a_minimum_deal_is_available_to_a_team_with_no_room_at_all():
    """A $0 contract never counts against the cap, so a capped-out team can always
    fill a roster spot with one."""
    check_signing_allowed(_ctx(payroll=HARD_CAP), 1, 0)


# -- offer ceilings (what the UI shows) --------------------------------------
def test_offer_ceiling_follows_the_kind_of_signing():
    payroll = SALARY_CAP + 20
    assert offer_ceiling(_ctx(payroll=payroll, rights_team_id=TEAM)) == max_offer(
        payroll, own_player=True)
    assert offer_ceiling(_ctx(payroll=payroll)) == max_offer(payroll, own_player=False)


def test_an_affordable_offer_is_exactly_what_the_ceiling_says():
    for payroll in (0, SALARY_CAP - 5, SALARY_CAP, SALARY_CAP + 30, HARD_CAP):
        for rights in (TEAM, None):
            ctx = _ctx(payroll=payroll, rights_team_id=rights)
            ceiling = offer_ceiling(ctx)
            check_signing_allowed(ctx, 1, ceiling)         # the ceiling itself is legal
            if ceiling < MAX_CONTRACT_VALUE:               # ... and one dollar more is not
                with pytest.raises(SigningError):
                    check_signing_allowed(ctx, 1, ceiling + 1)


# -- the shared canonical layout --------------------------------------------
def _squad(counts: dict[str, int]) -> list[Player]:
    """`counts` players per position, descending skill so the ordering is checkable."""
    out = []
    for pos, n in counts.items():
        for i in range(n):
            out.append(Player(id=f"{pos.lower()}-{i}", name=f"{pos} {i}", position=pos,
                              offense=9.0 - i, defense=9.0 - i, goalie_skill=9.0 - i))
    return out


_FULL = {"Forward": 5, "Midfielder": 5, "Defense": 5, "Goalie": 2}


def test_canonical_team_fills_starters_then_bench_then_reserves():
    team = canonical_team(_squad({**_FULL, "Forward": 7}), DEFAULT_RULES)
    assert [p.id for p in team.starters["Forward"]] == ["forward-0", "forward-1", "forward-2"]
    assert [p.id for p in team.bench["Forward"]] == ["forward-3", "forward-4"]
    assert [p.id for p in team.reserves] == ["forward-5", "forward-6"]


def test_canonical_team_benches_the_injured_behind_the_healthy():
    squad = _squad(_FULL)
    top = next(p for p in squad if p.id == "forward-0")
    top.is_injured = True                                  # best forward, but hurt
    team = canonical_team(squad, DEFAULT_RULES)
    assert top not in team.starters["Forward"]
    assert top in team.bench["Forward"]


def test_canonical_team_rejects_a_roster_that_cannot_field_a_position():
    with pytest.raises(RosterLayoutError, match="cannot field Goalie"):
        canonical_team(_squad({**_FULL, "Goalie": 1}), DEFAULT_RULES)


def test_canonical_team_rejects_reserve_overflow():
    with pytest.raises(RosterLayoutError, match="reserves"):
        canonical_team(_squad({**_FULL, "Forward": 10}), DEFAULT_RULES)
