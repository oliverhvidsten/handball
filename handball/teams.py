"""
Name: teams.py
Description: This file contains code for the Team classes
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 10:38AM PST
"""
from utils import dict_to_str
from sheets_handler import SheetHandler
from dataclasses import dataclass
from players import Player

import json

@dataclass
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
    

    --Starters/Bench--
    {
        "Forwards": [...],
        "Midfielders": [...],
        "Defense": [...],
        "Goalie": <str>
        }
    """
    team_name: str  # just the location (e.g. "Boston" not "Boston Foxes")
    coaches: list # 1x3 list of coaching staff [HC, OC, DC]
    starters: dict # dictionary holding lists for forwards, midfielders, and defense, and one goalie string
    bench: dict # dictionary holding lists for forwards, midfielders, and defense, and one goalie string
    reserve: list # 1x4 list of reserve player names
    draft_picks: dict # 2 element dict containing 1xN (1st round) and 1xM (2nd round) lists of draft picks that a team owns
    record: list # 1x3 list holding wins,losses,ties
    raw_data: tuple

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
    def from_sheet(cls, sheet_handler, team_name, get_draft_picks):
        #TODO: put record and salary information in TeamInfo Object
        """
        Get all of the information for the specified team from the google sheet

        Inputs:
            1. sheet_handler (SheetHandler): object holding credentials for the Google Sheet
            2. team_name (str): team name is just the location (e.g. "Boston" not "Boston Foxes")
            3. get_draft_picks (boook): If True, access the draft picks. Otherwise, do not access

        Return:
            (Team): team object for the specified team
        """

        team_info = sheet_handler.get_full_team_values(team_name)
        if get_draft_picks:
            draft_info = sheet_handler.get_draft_pics(team_name)
            draft_picks = {
                "1st Round": [pick for pick in draft_info[:, 0] if pick is not None and len(pick) > 0], 
                "2nd Round": [pick for pick in draft_info[:, 1] if pick is not None and len(pick) > 0]}
        else:
            draft_picks = None
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
            reserves=reserves,
            draft_picks=draft_picks,
            raw_data=(team_info, draft_info)
        )
    
    def update_sheet(self, sheet_handler:SheetHandler, update_draft_picks=False):
        """ Updates the google sheet to reflect any alterations that have occurred """
        # let's edit the raw data that we saved earlier
        team_info = self.raw_data[0]
        draft_info = self.raw_data[1]
        
        
        for i in range(3):
            team_info[i][2] = self.coaches[i] # update coaches
            team_info[i+5][1] = self.starters["Forwards"][i]
            team_info[i+8][1] = self.starters["Midfielders"][i]
            team_info[i+11][1] = self.starters["Defense"][i]

        for i in range(2):
            team_info[i+17][1] = self.bench["Forwards"][i]
            team_info[i+19][1] = self.bench["Midfielders"][i]
            team_info[i+21][1] = self.bench["Defense"][i]

        for i in range(4):
            team_info[i+26][1] = self.reserves[i]

        team_info[14][1] = self.starters["Goalie"]
        team_info[23][1] = self.bench["Goalie"]

        sheet_handler.update_full_team_values(team_name=self.team_name, edited_data=team_info)

        # If requested fill in the draft picks
        if update_draft_picks:
            for i,first_round in enumerate(self.draft_picks["1st Round"]):
                draft_info[i][0] = first_round
            
            for i, second_round in enumerate(self.draft_picks["2nd Round"]):
                draft_info[i][1] = second_round
            
            sheet_handler.update_draft_picks(team_name=self.team_name, edited_data=draft_info)




@dataclass
class Subroster():
    # Lists hold player objects
    forwards: list
    midfielders: list
    defense: list
    goalie: Player

    def from_TeamInfo(cls, subroster_type, team_dict):
        """ Create each subroster object from the data contained in the Team Info object """
        return cls(
            forwards=[Player.from_dict(team_dict[player_name]) for player_name in subroster_type["Forwards"]],
            midfielders=[Player.from_dict(team_dict[player_name]) for player_name in subroster_type["Midfielders"]],
            defense=[Player.from_dict(team_dict[player_name]) for player_name in subroster_type["Defense"]],
            goalie=Player.from_dict(team_dict[subroster_type["Goalie"]])
        )
    
    def update_team_dict(self, new_dict):
        for (forward, middie, defender) in zip(self.forwards, self.midfielders, self.defense):
            new_dict[forward.name] = forward.to_dict()
            new_dict[middie.name] = middie.to_dict()
            new_dict[defender.name] = defender.to_dict()
        new_dict[self.goalie.name] = self.goalie.to_dict()
            
    


    
@dataclass
class Team():

    team_name: str
    starters: Subroster
    bench: Subroster
    reserves: list # list of player objects
    draft_picks: list # list of strings
    record: list # wins, losses, ties

    def win(self):
        self.record[0] += 1
        
    def lose(self):
        self.record[1] += 1

    def tie(self):
        self.record[2] += 1



    def from_TeamInfo(cls, team_info:TeamInfo):
        """ Get Team object from Team Info object """
        with open(f"datafiles/{team_info.team_name.lower()}.json", "r") as f:
            team_dict = json.load(f)

        return cls(
            team_name=team_info.team_name,
            starters=Subroster.from_TeamInfo(team_info.starters, team_dict),
            bench=Subroster.from_TeamInfo(team_info.bench, team_dict),
            reserves=[Player.from_dict(team_dict[name]) for name in team_info.reserves],
            draft_picks=team_info.draft_picks # might be None if TeamInfo did not scrape this data
        )
    
    def update_team_dict(self):
        """ Write data from Team object to JSON"""
        updated_dict = dict()
        self.starters.update_team_dict(updated_dict)
        self.bench.update_team_dict(updated_dict)
        for reserve in self.reserves:
            updated_dict[reserve.name] = reserve.to_dict() 

        with open(f"datafiles/{self.team_name.lower()}.json", "w") as f:
            json.dump(f)


    def update_performances(self, performances):
        """ Add the individual game performances to the players' objects """
        for i, performance in enumerate(performances):
            if i < 3:
                self.starters.forwards[i].current_season_log["performances"].append(performance)
            elif i < 6:
                self.starters.midfielders[i-3].current_season_log["performances"].append(performance)
            elif i < 9:
                self.starters.defense[i-6].current_season_log["performances"].append(performance)
            elif i < 11:
                self.bench.forwards[i-9].current_season_log["performances"].append(performance)
            elif i < 13:
                self.bench.midfielders[i-11].current_season_log["performances"].append(performance)
            else:
                self.bench.defense[i-13].current_season_log["performances"].append(performance)
        
        # Reserves do not play, set performances to 0
        for reserve in self.reserves:
            reserve.current_season_log["performances"].append(0)





    def update_offensive_stats(self, goals_scored, shots_taken):
        """ 
        Add the goals scored by all of the eligible goal scorers as well as how many shots they took
        Additionally, put 0 for all offensive records of non-offensive players
        """
        
        for i, (goals, shots) in enumerate(zip(goals_scored, shots_taken)):
            if i < 3:
                self.starters.forwards[i].current_season_log["goals"].append(goals)
                self.starters.forwards[i].current_season_log["shots_taken"].append(shots)
            elif i < 6:
                self.starters.midfielders[i-3].current_season_log["goals"].append(goals)
                self.starters.midfielders[i-3].current_season_log["shots_taken"].append(shots)
            elif i < 8:
                self.bench.forwards[i-6].current_season_log["goals"].append(goals)
                self.bench.forwards[i-6].current_season_log["shots_taken"].append(shots)
            else:
                self.bench.midfielders[i-8].current_season_log["goals"].append(goals)
                self.bench.midfielders[i-8].current_season_log["shots_taken"].append(shots)

        # set all goals scored to 0 for defenders and reserves
        for defender in self.starters.defense:
            defender.current_season_log["goals"].append(0)
            defender.current_season_log["shots_taken"].append(0)
        for defender in self.bench.defense:
            defender.current_season_log["goals"].append(0)
            defender.current_season_log["shots_taken"].append(0)
        for reserve in self.reserves:
            reserve.current_season_log["goals"].append(0)
            reserve.current_season_log["shots_taken"].append(0)