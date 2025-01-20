"""
Name: teams.py
Description: This file contains code for the Team classes
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 10:38AM PST
"""

class Team():

    """
    Team Structure:
    Coaching Staff:
        - Head Coach

    Starters:
        - 3 Offense
        - 3 Midfield
        - 3 Defense
        - 1 Goalie
    
    Bench
        - 2 Offense
        - 2 Midfield
        - 2 Defense
        - 1 Goalie
    
    Reserve
    """


    def __init__(self):
        self.starters = {
            "Offense": [None],
            "Midfielders": [None],
            "Defense": [None],
            "Goalie": None
        }

        self.bench = {
            "Offense": [None],
            "Midfielders": [None],
            "Defense": [None],
            "Goalie": None
        }

        self.reserve = [None]

        raise NotImplementedError


    def from_sheet(self):
        # Get names for each position
        # SO1, SO2, SO3, SM1, SM2, SM3, SD1, SD2, SD3, SG1, BO1, BO2, BM1, BM2, BD1, BD2, BG1, IO1, IM1, ID1, IG1
        names = []

        raise NotImplementedError
    

    