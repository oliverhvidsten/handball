"""
Name: draft_simulator.py
Description: This file will generate all necessary information to operate the draft 
- Generate New Players
- Generate Draft Order (depending what teams have what draft picks)
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 11:07AM PST
"""

def player_generation(names_list, age_list, position_list):
    """
    Generate player objects from names
    
    Input:
        1. new_names_list (list): list of the new draft class names
    Outputs:
        (list of Players): new Player Objects containing all of the "visible" and "invisible" stats
    """
    # Classify how good a player will be based on their name
    classes = classify_players(names_list)
    # Use their class to determine the attributes of the player
    all_player_attributes = [randomize_attributes(player_class, player_age, player_position) for player_class, player_age, player_position in zip(classes, age_list, position_list)]

    ## create Player Objects from this list of attributes 
    all_player_objects = [generate_player_objects(name, attributes, age) for name, attributes, age in zip(names_list, all_player_attributes, age_list)]
    
    raise NotImplementedError



def classify_players(new_names):
    """
    Feed new names into the pre-trained ML algorithm to classify if they will be good, bad, etc

    Input:
        1. new_names (list): list of the new draft class names
    Outputs
        (list): name classifications 
    """

    raise NotImplementedError

def randomize_attributes(player_class, player_age, player_position):
    """
    Based of off how good they "should" be based off of their name and some randomization that will make 
    them theoretically better or worse, generate their stats. 
    Take into account age (older players should be able to perform at a higher level, but age should not affect physical maxima)

    Input:
        1. player_class (int): Player Ability Class
        2. player_age (int): Player Age
        3. player_position (str): Player Position

    Output:
        (???): the attributes of the relevant player
    """
    raise NotImplementedError

def generate_player_objects(attributes, name, age):
    raise NotImplementedError


def generate_college_stats(all_player_objects):
    """
    Somehow generate player stats from college (these should theoretically be greater than what they would be expected to do in the NHA)

    Input:

    """
    raise NotImplementedError