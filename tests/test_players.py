"""
Name: test_players.py
Description: Holds unit tests for funcitons in players.py
Author: Oliver Hvidsten
Date: 8/3/2025 8:11PM PST
"""
import pytest
from handball.players import Player, InjuryReport

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

def test_data_storing(player_obj):
    """
    Test to ensure Player Objects can be serialized and deserialized with no data loss
    """
    d = player_obj.to_dict()
    new_player_obj = Player.from_dict(d)

    assert player_obj == new_player_obj


def test_random_player_attribute():
    """
    Generate a random player. 
    Ensure all attributes exist and remain within required boundaries (if applicable)
    """
    p = Player.create_new_player("Harry Boxin", 3)
    print(p)

    t1 = hasattr(p, "name") and hasattr(p, "age") and hasattr(p, "years_in_league") and hasattr(p, "height") and hasattr(p, "weight") and hasattr(p, "position")
    t2 = hasattr(p, "offense") and hasattr(p, "defense") and hasattr(p, "goalie_skill") and hasattr(p, "max_offense") and hasattr(p, "max_defense") and hasattr(p, "max_goalie_skill") and hasattr(p, "variance")
    t3 = hasattr(p, "is_injured") and hasattr(p, "injury_risk") and hasattr(p, "injury_log")
    t4 = hasattr(p, "contract_term") and hasattr(p, "contract_value") and hasattr(p, "years_remaining") and hasattr(p, "amount_paid") and hasattr(p, "rookie_contract") and hasattr(p, "restricted_free_agent")
    t5 = hasattr(p, "awards_won") and hasattr(p, "current_season_log")

    t6 = p.name == "Harry Boxin"
    t7 = p.years_in_league == 0
    t8 = not p.is_injured
    t9 = p.amount_paid == 0
    t10 = p.rookie_contract
    t11 = p.restricted_free_agent
    t12 = p.offense <= 10 and p.offense >= 0
    t13 = p.defense <= 10 and p.defense >= 0
    t14 = p.goalie_skill <= 10 and p.goalie_skill >= 0
    assert all([t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14])


def test_injury(player_obj):
    """
    First, loads in test player object. 
    Then injures the player and checks to see if all elements have been properly updated.
    """
    player_obj.injure(
        year=2023,
        injury_type="Finger (Minor Fracture)",
        current_game=5
    )

    assert player_obj.injury_log.active_injury and len(player_obj.injury_log.injuries) == 1


def test_advance_year(player_obj):
    """
    Test the advance year function.
    """
    player_obj.advance_year()
    assert player_obj.age == 29 and player_obj.years_in_league == 3 and player_obj.years_remaining == 1