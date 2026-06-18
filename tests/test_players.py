"""
Name: test_players.py
Description: Holds unit tests for functions in players.py
Author: Oliver Hvidsten
Date: 8/3/2025 8:11PM PST
"""
import pytest
from handball.players import Player, PlayerInfo, InjuryReport


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def player_obj():
    return Player(
        name="Test Player",
        age=28,
        years_in_league=2,
        height=70,
        weight=170,
        position="Forward",
        offense=8.8,
        defense=2.7,
        goalie_skill=1.7,
        max_offense=9.7,
        max_defense=7.0,
        max_goalie_skill=4.0,
        variance=2.8,
        peak_age=27,
        decline_age=30,
        decline_rate=0.15,
        is_injured=False,
        injury_risk=0.05,
        injury_log=InjuryReport(active_injury=False, injuries=list()),
        contract_term=5,
        contract_value=10,
        years_remaining=2,
        amount_paid=6,
        rookie_contract=False,
        restricted_free_agent=False,
        awards_won=list(),
        current_season_log=dict()
    )


@pytest.fixture
def young_player():
    """Player well before peak age — triggers growth branch."""
    return Player(
        name="Young Gun", age=20, years_in_league=1, height=70, weight=170,
        position="Forward", offense=4.0, defense=3.0, goalie_skill=0.1,
        max_offense=8.0, max_defense=6.0, max_goalie_skill=0.1, variance=0.5,
        peak_age=27, decline_age=30, decline_rate=0.15,
    )


@pytest.fixture
def peak_player():
    """Player between peak and decline — triggers noisy plateau branch."""
    return Player(
        name="Peak Pro", age=28, years_in_league=6, height=72, weight=185,
        position="Midfielder", offense=7.0, defense=7.0, goalie_skill=0.1,
        max_offense=8.0, max_defense=8.0, max_goalie_skill=0.1, variance=0.5,
        peak_age=27, decline_age=32, decline_rate=0.15,
    )


@pytest.fixture
def old_player():
    """Player past decline age — triggers decline branch."""
    return Player(
        name="Old Timer", age=33, years_in_league=12, height=71, weight=180,
        position="Defense", offense=5.0, defense=7.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=8.0, max_goalie_skill=0.1, variance=0.5,
        peak_age=27, decline_age=30, decline_rate=0.20,
    )


@pytest.fixture
def goalie_player():
    return Player(
        name="Goalie Guy", age=31, years_in_league=8, height=74, weight=195,
        position="Goalie", offense=0.1, defense=0.1, goalie_skill=7.5,
        max_offense=0.1, max_defense=0.1, max_goalie_skill=9.0, variance=0.3,
        peak_age=28, decline_age=31, decline_rate=0.12,
    )


# ======================================================================
# Player — serialization
# ======================================================================

def test_data_storing(player_obj):
    d = player_obj.to_dict()
    new_player_obj = Player.from_dict(d)
    assert player_obj == new_player_obj


def test_round_trip_with_injuries():
    """Serialization preserves injury log entries."""
    p = Player(
        name="Hurt", age=25, years_in_league=3, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
    )
    p.injure(2025, "Knee (Strain)")
    d = p.to_dict()
    loaded = Player.from_dict(d)
    assert len(loaded.injury_log.injuries) == 1
    assert loaded.is_injured is True


def test_advance_year_resets_season_log():
    """A new season starts with a clean stat sheet."""
    p = Player(
        name="Ager", age=25, years_in_league=3, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
    )
    p.current_season_log["goals"].extend([1, 2, 3])
    p.current_season_log["shots_taken"].extend([4, 5, 6])
    age_before = p.age

    p.advance_year()

    assert p.age == age_before + 1
    assert p.current_season_log["goals"] == []
    assert p.current_season_log["shots_taken"] == []
    assert p.current_season_log["saves"] == []


def test_round_trip_with_season_log():
    """Season log data survives serialization."""
    p = Player(
        name="Logger", age=25, years_in_league=3, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
    )
    p.current_season_log["goals"].extend([1, 0, 2])
    p.current_season_log["shots_taken"].extend([3, 2, 5])
    p.current_season_log["performances"].extend([8.5, 4.2, 9.1])
    d = p.to_dict()
    loaded = Player.from_dict(d)
    assert loaded.current_season_log["goals"] == [1, 0, 2]


# ======================================================================
# Player — __eq__ edge cases
# ======================================================================

def test_eq_with_none(player_obj):
    assert player_obj != None  # noqa: E711


def test_eq_with_wrong_type(player_obj):
    assert player_obj != "not a player"


def test_eq_different_players(player_obj, young_player):
    assert player_obj != young_player


def test_eq_identical_copy(player_obj):
    copy = Player.from_dict(player_obj.to_dict())
    assert player_obj == copy


# ======================================================================
# Player — create_new_player for all positions
# ======================================================================

@pytest.mark.parametrize("position", ["Forward", "Midfielder", "Defense", "Goalie"])
def test_create_new_player_all_positions(position):
    p = Player.create_new_player(f"Test {position}", position)
    assert p.position == position
    assert 0 <= p.offense <= 10
    assert 0 <= p.defense <= 10
    assert 0 <= p.goalie_skill <= 10
    assert p.years_in_league == 0
    assert p.rookie_contract is True

    if position == "Goalie":
        assert p.offense == pytest.approx(0.1)
        assert p.defense == pytest.approx(0.1)
        assert p.max_offense == pytest.approx(0.1)
        assert p.max_defense == pytest.approx(0.1)
    else:
        assert p.max_goalie_skill == pytest.approx(0.1)


def test_create_new_player_overall_distribution():
    """Overall skill is sampled from N(5, 1.5), floored at 0 and capped at 10."""
    players = [Player.create_new_player("DistTest", "Forward") for _ in range(300)]
    # All stats must respect the [0, 10] cap.
    for p in players:
        assert 0 <= p.offense <= 10
        assert 0 <= p.defense <= 10
    # Mean overall (offense+defense average over a Forward) should sit near 5.
    overalls = [(p.offense + p.defense) / 2 for p in players]
    assert 3.5 < sum(overalls) / len(overalls) < 6.5


# ======================================================================
# Player — update_stats (three age branches)
# ======================================================================

def test_update_stats_young_player_grows(young_player):
    """Before peak: stats should increase toward max."""
    off_before = young_player.offense
    def_before = young_player.defense
    young_player.update_stats(years=1, rate_scale=1.0)
    assert young_player.offense > off_before
    assert young_player.defense > def_before


def test_update_stats_peak_player_noisy(peak_player):
    """Between peak and decline: stats change by random noise."""
    peak_player.update_stats(years=1, rate_scale=1.0)
    assert 0 <= peak_player.offense <= 10
    assert 0 <= peak_player.defense <= 10


def test_update_stats_old_player_declines(old_player):
    """Past decline age: stats should decrease."""
    off_before = old_player.offense
    def_before = old_player.defense
    old_player.update_stats(years=1, rate_scale=1.0)
    assert old_player.offense < off_before
    assert old_player.defense < def_before


def test_update_stats_goalie_decline(goalie_player):
    """Goalie past decline uses goalie_skill decline, not offense/defense."""
    gs_before = goalie_player.goalie_skill
    goalie_player.update_stats(years=1, rate_scale=1.0)
    assert goalie_player.goalie_skill < gs_before


def test_update_stats_capped_at_10():
    """Stats must never exceed 10.0."""
    p = Player(
        name="Capped", age=20, years_in_league=1, height=70, weight=170,
        position="Forward", offense=9.9, defense=9.9, goalie_skill=0.1,
        max_offense=15.0, max_defense=15.0, max_goalie_skill=0.1, variance=0.5,
        peak_age=27, decline_age=30, decline_rate=0.15,
    )
    p.update_stats(years=5, rate_scale=1.0)
    assert p.offense <= 10.0
    assert p.defense <= 10.0


# ======================================================================
# Player — advance_year
# ======================================================================

def test_advance_year(player_obj):
    player_obj.advance_year()
    assert player_obj.age == 29
    assert player_obj.years_in_league == 3
    assert player_obj.years_remaining == 1


def test_advance_year_years_remaining_goes_negative():
    """Contract can expire (years_remaining goes to -1)."""
    p = Player(
        name="Expiring", age=25, years_in_league=3, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
        years_remaining=0,
    )
    p.advance_year()
    assert p.years_remaining == -1


# ======================================================================
# Player — update_contract
# ======================================================================

def test_update_contract_rookie(player_obj):
    player_obj.rookie_contract = True
    player_obj.restricted_free_agent = True
    player_obj.update_contract(4, 8, rookie=True)
    assert player_obj.contract_term == 4
    assert player_obj.contract_value == 8
    assert player_obj.rookie_contract is True
    assert player_obj.restricted_free_agent is True


def test_update_contract_non_rookie(player_obj):
    player_obj.rookie_contract = True
    player_obj.restricted_free_agent = True
    player_obj.update_contract(3, 15, rookie=False)
    assert player_obj.contract_term == 3
    assert player_obj.contract_value == 15
    assert player_obj.rookie_contract is False
    assert player_obj.restricted_free_agent is False


# ======================================================================
# Player — total_season_goals
# ======================================================================

def test_total_season_goals_empty():
    p = Player(
        name="NoGoals", age=25, years_in_league=1, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
    )
    assert p.total_season_goals == 0


def test_total_season_goals_with_data():
    p = Player(
        name="Scorer", age=25, years_in_league=1, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
    )
    p.current_season_log["goals"] = [2, 0, 1, 3]
    assert p.total_season_goals == 6


# ======================================================================
# Player — injury
# ======================================================================

def test_injury(player_obj):
    player_obj.injure(year=2023, injury_type="Finger (Minor Fracture)")
    assert player_obj.injury_log.active_injury
    assert len(player_obj.injury_log.injuries) == 1


def test_random_player_attribute():
    p = Player.create_new_player("Harry Boxin", "Forward")
    assert p.name == "Harry Boxin"
    assert p.years_in_league == 0
    assert not p.is_injured
    assert p.amount_paid == 0
    assert p.rookie_contract
    assert p.restricted_free_agent
    assert 0 <= p.offense <= 10
    assert 0 <= p.defense <= 10
    assert 0 <= p.goalie_skill <= 10


# ======================================================================
# InjuryReport
# ======================================================================

class TestInjuryReport:
    def test_empty_report(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        assert len(ir) == 0
        assert not ir.active_injury

    def test_add_minor_injury(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        duration = ir.add(2025, "Finger (Sprain)")
        assert ir.active_injury is True
        assert len(ir) == 1
        assert duration >= 0

    def test_add_moderate_injury(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        duration = ir.add(2025, "Finger (Minor Fracture)")
        assert ir.active_injury is True
        assert duration >= 0

    def test_add_major_injury(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        duration = ir.add(2025, "ACL (Tear)")
        assert ir.active_injury is True
        assert duration >= 0

    def test_add_while_already_injured_returns_false(self):
        ir = InjuryReport(active_injury=True, injuries=[(2025, "Knee (Strain)", 3, 5, True)])
        result = ir.add(2025, "Ankle (Sprain)")
        assert result is False
        assert len(ir) == 1

    def test_to_dict_from_dict_round_trip(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        ir.add(2024, "Knee (Strain)")
        d = ir.to_dict()
        loaded = InjuryReport.from_dict(d)
        assert loaded.active_injury is True
        assert len(loaded) == 1

    def test_repr_no_injuries(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        r = repr(ir)
        assert "0 injuries" in r

    def test_repr_with_injuries(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        ir.add(2025, "Finger (Sprain)")
        r = repr(ir)
        assert "1 injuries" in r

    def test_tick_recovers_after_duration(self):
        ir = InjuryReport(active_injury=False, injuries=[])
        duration = ir.add(2025, "Knee (Strain)")
        assert ir.games_remaining == duration

        # Tick down to one game before recovery.
        for _ in range(duration - 1):
            ir.tick()
        assert ir.active_injury is True

        # Final tick recovers.
        ir.tick()
        assert ir.active_injury is False
        assert ir.injuries[0][4] is False
        assert ir.games_remaining == 0

    def test_tick_recovery_survives_serialization(self):
        """Records stay mutable after a JSON round-trip, so tick() works."""
        ir = InjuryReport(active_injury=False, injuries=[])
        duration = ir.add(2025, "ACL (Tear)")
        loaded = InjuryReport.from_dict(ir.to_dict())
        for _ in range(duration):
            loaded.tick()  # must not raise (records are lists)
        assert loaded.active_injury is False


def test_player_tick_injury_recovers():
    p = Player(
        name="Hurt", age=25, years_in_league=3, height=70, weight=170,
        position="Forward", offense=5.0, defense=4.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
    )
    duration = p.injure(2025, "Finger (Sprain)")
    assert p.is_injured is True

    for _ in range(duration):
        p.tick_injury()
    assert p.is_injured is False


def test_apply_injury_impact_young_lowers_ceiling():
    young = Player(
        name="Kid", age=20, years_in_league=1, height=70, weight=170,
        position="Forward", offense=4.0, defense=3.0, goalie_skill=0.1,
        max_offense=8.0, max_defense=6.0, max_goalie_skill=0.1, variance=0.5,
        peak_age=27,
    )
    young.apply_injury_impact()
    assert young.max_offense < 8.0  # growth ceiling lowered
    assert young.max_defense < 6.0
    assert young.max_offense >= young.offense  # never below current ability


def test_apply_injury_impact_old_accelerates_decline():
    old = Player(
        name="Vet", age=33, years_in_league=12, height=71, weight=180,
        position="Defense", offense=5.0, defense=7.0, goalie_skill=0.1,
        max_offense=6.0, max_defense=8.0, max_goalie_skill=0.1, variance=0.5,
        peak_age=27, decline_age=30, decline_rate=0.2,
    )
    old.apply_injury_impact()
    assert old.decline_rate > 0.2


# ======================================================================
# PlayerInfo
# ======================================================================

class TestPlayerInfo:
    @pytest.fixture
    def sample_player(self):
        return Player(
            name="Info Test", age=26, years_in_league=4, height=71, weight=180,
            position="Midfielder", offense=6.5, defense=5.5, goalie_skill=0.1,
            max_offense=8.0, max_defense=7.0, max_goalie_skill=0.1, variance=0.5,
            contract_term=3, contract_value=12, rookie_contract=False,
            is_injured=True,
        )

    @pytest.fixture
    def rookie_player(self):
        return Player(
            name="Rookie Star", age=19, years_in_league=0, height=69, weight=165,
            position="Forward", offense=4.0, defense=2.0, goalie_skill=0.1,
            max_offense=8.0, max_defense=5.0, max_goalie_skill=0.1, variance=0.5,
            contract_term=2, contract_value=5, rookie_contract=True,
        )

    def test_from_player_non_rookie(self, sample_player):
        info = PlayerInfo.from_Player(sample_player)
        assert info.name == "Info Test"
        assert info.position == "Midfielder"
        assert info.age == 26
        assert info.contract == "3/$12"
        assert info.injured is True
        assert info.offense == pytest.approx(6.5)

    def test_from_player_rookie_contract_marker(self, rookie_player):
        info = PlayerInfo.from_Player(rookie_player)
        assert "(R)" in info.contract

    def test_update_from_player(self, sample_player):
        info = PlayerInfo(
            name="Info Test", position="Midfielder", age=20,
            contract="", injured=False, offense=1.0, defense=1.0, goalie_skill=0.1,
        )
        info.update_from_Player(sample_player)
        assert info.age == 26
        assert info.injured is True
        assert info.offense == pytest.approx(6.5)
        assert info.defense == pytest.approx(5.5)

    def test_name_and_stats_non_goalie(self):
        info = PlayerInfo(
            name="Alice", position="Forward", age=25, contract="",
            injured=False, offense=7.123, defense=4.567, goalie_skill=0.1,
        )
        result = info.name_and_stats(is_goalie=False)
        assert result == ["Alice", 7.12, 4.57]

    def test_name_and_stats_goalie(self):
        info = PlayerInfo(
            name="Bob", position="Goalie", age=25, contract="",
            injured=False, offense=0.1, defense=0.1, goalie_skill=6.789,
        )
        result = info.name_and_stats(is_goalie=True)
        assert result == ["Bob", 6.79]

    def test_get_notes(self):
        info = PlayerInfo(
            name="Alice", position="Forward", age=25, contract="3/$10",
            injured=False, offense=5.0, defense=4.0, goalie_skill=0.1,
        )
        notes = info.get_notes()
        assert "Age: 25" in notes
        assert "Position: Forward" in notes
        assert "Contract: 3/$10" in notes
        assert "Injured: False" in notes

    def test_to_sheet(self):
        info = PlayerInfo(
            name="Alice", position="Forward", age=25, contract="3/$10",
            injured=False, offense=5.0, defense=4.0, goalie_skill=0.1,
        )
        name, note, stats = info.to_sheet()
        assert name == "Alice"
        assert "Age: 25" in note
        assert stats == (5.0, 4.0, 0.1)

    def test_equality_same(self):
        a = PlayerInfo("X", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        b = PlayerInfo("X", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        assert a == b

    def test_equality_different(self):
        a = PlayerInfo("X", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        b = PlayerInfo("Y", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        assert a != b

    def test_equality_wrong_type(self):
        a = PlayerInfo("X", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        assert a != "not a PlayerInfo"

    def test_hash_same_objects(self):
        a = PlayerInfo("X", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        b = PlayerInfo("X", "Forward", 25, "2/$5", False, 5.0, 4.0, 0.1)
        assert hash(a) == hash(b)

    def test_str(self):
        info = PlayerInfo("Alice", "Forward", 25, "3/$10", False, 5.0, 4.0, 0.1)
        s = str(info)
        assert "Alice" in s
        assert "Forward" in s