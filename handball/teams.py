"""
Name: teams.py
Description: This file contains code for the Team classes
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 10:38AM PST
"""
from utils import dict_to_str
from sheets_handler import SheetHandler

class TeamInfo():
    """
    The TeamInfo class holds all of the Information about the team, but not the actual player objects.
    This class is used to directly communicate with the google sheet.

    Convert a TeamInfo object to a Team object to get the inputs to simulate a game.
    This willl require turning player names (str) into player objects (Player)

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


    def __init__(self, team_name, coaches, starters, bench, reserves):
        """
        Team Class constructor

        Inputs
            1. team_name (str): team name is just the location (e.g. "Boston" not "Boston Foxes")
            2. coaches (list): 1x3 list of coaching staff [HC, OC, DC]
            3. starters (dict): dictionary holding lists for forwards, midfielders, and defense, and one goalie string
            4. bench (dict): dictionary holding lists for forwards, midfielders, and defense, and one goalie string
            5. reserve (list): 1x4 list of reserve player names

        --Starters/Bench--
        {
            "Forwards": [...],
            "Midfielders": [...],
            "Defense": [...],
            "Goalie": <str>
         }
        """
        self.team_name = team_name 
        self.coaches = coaches
        self.starters = starters
        self.bench = bench
        self.reserves = reserves

    def __str__(self):

        lines = [
            f"######  {self.team_name.upper()}  ######",
            f"Head Coach: {self.coaches[0]}",
            f"OC: {self.coaches[1]}, DC: {self.coaches[2]}",
            f"",
            f"--- STARTERS ---",
            dict_to_str(self.starters),
            f"",
            f"--- BENCH ---",
            dict_to_str(self.bench),
            f"",
            f"--- RESERVES ---",
            ", ".join(self.reserves),
        ]
        return "\n".join(lines)

    @classmethod
    def from_sheet(cls, sheet_handler, team_name):
        """
        Get all of the information for the specified team from the google sheet

        Inputs:
            1. sheet_handler (SheetHandler): object holding credentials for the Google Sheet
            2. team_name (str): team name is just the location (e.g. "Boston" not "Boston Foxes")

        Return:
            (Team): team object for the specified team
        """

        team_info = sheet_handler.get_full_team_values(team_name)
        coaches = [team_info[0][2], team_info[1][2], team_info[2][2]]
        starters = {
            "Forwards":[team_info[5][1], team_info[6][1], team_info[7][1]],
            "Midfielders":[team_info[8][1], team_info[9][1], team_info[10][1]],
            "Defense":[team_info[11][1], team_info[12][1], team_info[13][1]],
            "Goalie":team_info[14][1]
        }
        bench = {
            "Forwards":[team_info[17][1], team_info[18][1]],
            "Midfielders":[team_info[19][1], team_info[20][1]],
            "Defense":[team_info[21][1], team_info[22][1]],
            "Goalie":team_info[23][1]
        }
        reserves = [team_info[26][1], team_info[27][1], team_info[28][1], team_info[29][1]]

        return TeamInfo(
            team_name=team_name,
            coaches=coaches,
            starters=starters,
            bench=bench, 
            reserves=reserves
        )
    
    def prepare_players(self):
        """
        Link all of the players on the team to their player cards
        """
        # Implement this once players have been implemented

        raise NotImplementedError



    