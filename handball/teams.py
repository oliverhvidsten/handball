"""
Name: teams.py
Description: This file contains code for the Team classes
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 10:38AM PST
"""
from __future__ import annotations

from handball.utils import dict_to_str
from handball.sheets_handler import SheetHandler
from handball.players import Player, PlayerInfo

from dataclasses import dataclass
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

    Record
    Total Salaries
    Raw Info
    

    --Starters/Bench--
    {
        "Forwards": [...],
        "Midfielders": [...],
        "Defense": [...],
        "Goalie": <str>
        }
    """
    team_name: str  # just the location (e.g. "Boston" not "Boston Foxes")
    coaches: list[str] # 1x3 list of coaching staff [HC, OC, DC]
    starters: dict[str, list[PlayerInfo] | PlayerInfo] # dictionary holding lists for forwards, midfielders, and defense, and one goalie
    bench: dict[str, list[PlayerInfo] | PlayerInfo] # dictionary holding lists for forwards, midfielders, and defense, and one goalie
    reserve: list[PlayerInfo] # 1x4 list of reserve player names
    draft_picks: dict|None # 2 element dict containing 1xN (1st round) and 1xM (2nd round) lists of draft picks that a team owns
    record: list[int] # 1x3 list holding wins,losses,ties
    total_salaries: int # total salaries (in millions)
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
            ", ".join([playerinfo.name for playerinfo in self.reserve]),
        ]
        return "\n".join(lines)

    @classmethod
    def from_sheet(cls, sheet_handler:SheetHandler, team_name, get_draft_picks):
        #TODO: put record and salary information in TeamInfo Object
        """
        Get all of the information for the specified team from the google sheet

        Inputs:
            1. sheet_handler (SheetHandler): object holding credentials for the Google Sheet
            2. team_name (str): team name is just the location (e.g. "Boston" not "Boston Foxes")
            3. get_draft_picks (bool): If True, access the draft picks. Otherwise, do not access

        Return:
            (TeamInfo): team object for the specified team
        """

        team_info = sheet_handler.get_full_team_values(team_name)
        player_notes = sheet_handler.get_player_notes(team_name)
        player_notes_dict = dict()
        for player_name, note in player_notes:
            if len(player_name) > 1:
                player_notes_dict[player_name] = note

        if get_draft_picks:
            draft_info = sheet_handler.get_draft_picks(team_name)
            draft_picks = {
                "1st Round": [pick for pick in draft_info[:, 0] if pick is not None and len(pick) > 0], 
                "2nd Round": [pick for pick in draft_info[:, 1] if pick is not None and len(pick) > 0]}
        else:
            draft_info = None
            draft_picks = None
        coaches = [team_info[0][2], team_info[1][2], team_info[2][2]]
        starters = {
            "Forwards":[
                PlayerInfo.from_sheet(team_info[5], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[6], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[7], player_notes_dict)
            ],
            "Midfielders":[
                PlayerInfo.from_sheet(team_info[8], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[9], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[10], player_notes_dict)
                ],
            "Defense":[
                PlayerInfo.from_sheet(team_info[11], player_notes_dict),
                PlayerInfo.from_sheet(team_info[12], player_notes_dict),
                PlayerInfo.from_sheet(team_info[13], player_notes_dict)
                ],
            "Goalie": PlayerInfo.from_sheet(team_info[14], player_notes_dict)
        }
        bench = {
            "Forwards":[
                PlayerInfo.from_sheet(team_info[17], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[18], player_notes_dict)
                ],
            "Midfielders":[
                PlayerInfo.from_sheet(team_info[19], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[20], player_notes_dict)
                ],
            "Defense":[
                PlayerInfo.from_sheet(team_info[21], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[22], player_notes_dict)
                ],
            "Goalie":PlayerInfo.from_sheet(team_info[23], player_notes_dict)
        }
        reserve = [
            PlayerInfo.from_sheet(team_info[26], player_notes_dict),
            PlayerInfo.from_sheet(team_info[27], player_notes_dict),
            PlayerInfo.from_sheet(team_info[28], player_notes_dict),
            PlayerInfo.from_sheet(team_info[29], player_notes_dict)
        ]

        record = [int(num) for num in team_info[0][5].split("-")] # make W-L-T into [W, L, T]
        total_salaries = int(team_info[1][5][1:-1]) # get everything except for the "$" at the start "M" at the end

        return TeamInfo(
            team_name=team_name,
            coaches=coaches,
            starters=starters,
            bench=bench, 
            reserve=reserve,
            draft_picks=draft_picks,
            record=record,
            total_salaries=total_salaries,
            raw_data=(team_info, draft_info, player_notes_dict)
        )
    
    
    def update_sheet(self, sheet_handler:SheetHandler, update_draft_picks=False):
        """ Updates the google sheet to reflect any alterations that have occurred """
        # let's edit the raw data that we saved earlier
        team_info = self.raw_data[0]
        draft_info = self.raw_data[1]

        ## TODO: lots of type errors here because we switch between list[PlayerInfo] and just PlayerInfo 
        ## values in the dictionaries --> think about ways to recitify this

        ## For now, putting type ignore flags
        
        # create list of empty strings to fill in with updated notes
        new_notes = [""] * 25
        
        
        for i in range(3):
            team_info[i][2] = self.coaches[i] # update coaches
            team_info[i+5][1:4] = self.starters["Forwards"][i].name_and_stats() # type: ignore
            team_info[i+8][1:4] = self.starters["Midfielders"][i].name_and_stats() # type: ignore
            team_info[i+11][1:4] = self.starters["Defense"][i].name_and_stats() # type: ignore

            new_notes[i] = self.starters["Forwards"][i].get_notes() # type: ignore
            new_notes[i+3] = self.starters["Midfielders"][i].get_notes() # type: ignore
            new_notes[i+6] = self.starters["Defense"][i].get_notes() # type: ignore

        for i in range(2):
            team_info[i+17][1:4] = self.bench["Forwards"][i].name_and_stats() # type: ignore
            team_info[i+19][1:4] = self.bench["Midfielders"][i].name_and_stats() # type: ignore
            team_info[i+21][1:4] = self.bench["Defense"][i].name_and_stats() # type: ignore

            new_notes[i+12] = self.bench["Forwards"][i].get_notes() # type: ignore
            new_notes[i+14] = self.bench["Midfielders"][i].get_notes() # type: ignore
            new_notes[i+16] = self.bench["Defense"][i].get_notes() # type: ignore
            

        for i in range(4):
            team_info[i+26][1:4] = self.reserve[i].name_and_stats()
            new_notes[i+21] = self.reserve[i].get_notes()

        team_info[14][1] = self.starters["Goalie"].name_and_stats() # type: ignore
        new_notes[9] = self.starters["Goalie"].get_notes() # type: ignore
        team_info[23][1] = self.bench["Goalie"].name_and_stats() # type: ignore
        new_notes[18] = self.starters["Goalie"].get_notes() # type: ignore
        
        team_info[0][5] = "-".join([str(value) for value in self.record])
        team_info[1][5] = f"${self.total_salaries}M"

        # update the values and notes
        sheet_handler.update_full_team_values(team_name=self.team_name, edited_data=team_info)
        sheet_handler.update_player_notes(team_name=self.team_name, new_notes=new_notes)

        # If requested fill in the draft picks
        if update_draft_picks:
            for i,first_round in enumerate(self.draft_picks["1st Round"]): # type: ignore
                draft_info[i][0] = first_round
            
            for i, second_round in enumerate(self.draft_picks["2nd Round"]): # type: ignore
                draft_info[i][1] = second_round
            
            sheet_handler.update_draft_picks(team_name=self.team_name, edited_data=draft_info)

    def get_updates_from_Team(self, team_obj:Team):
        """
        Get relevant values from Team Objects
        """
        for i, player in enumerate(team_obj.starters.forwards):
            self.starters["Forwards"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.starters.midfielders):
            self.starters["Midfielders"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.starters.defense):
            self.starters["Defense"][i].update_from_Player(player) # type: ignore
        self.starters["Goalie"].update_from_Player(team_obj.starters.goalie) # type: ignore

        for i, player in enumerate(team_obj.bench.forwards):
            self.starters["Forwards"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.bench.midfielders):
            self.starters["Midfielders"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.bench.defense):
            self.starters["Defense"][i].update_from_Player(player) # type: ignore
        self.starters["Goalie"].update_from_Player(team_obj.bench.goalie) # type: ignore

        for i, player in enumerate(team_obj.reserves):
            self.reserve[i].update_from_Player(player)

        self.record = team_obj.record

        # TODO: Should we update draft picks here? Unlikely...




@dataclass
class Subroster():
    # Lists hold player objects
    forwards: list
    midfielders: list
    defense: list
    goalie: Player

    @classmethod
    def from_TeamInfo(cls, subroster_type, team_dict):
        """ Create each subroster object from the data contained in the Team Info object """
        return cls(
            forwards=[Player.from_dict(team_dict[playerinfo.name]) for playerinfo in subroster_type["Forwards"]],
            midfielders=[Player.from_dict(team_dict[playerinfo.name]) for playerinfo in subroster_type["Midfielders"]],
            defense=[Player.from_dict(team_dict[playerinfo.name]) for playerinfo in subroster_type["Defense"]],
            goalie=Player.from_dict(team_dict[subroster_type["Goalie"].name])
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
    draft_picks: dict|None # keys: "1st Round" and "2nd Round"
    record: list[int] # wins, losses, ties

    def win(self):
        self.record[0] += 1
        
    def lose(self):
        self.record[1] += 1

    def tie(self):
        self.record[2] += 1


    @classmethod
    def from_TeamInfo(cls, team_info:TeamInfo):
        """ Get Team object from Team Info object """
        with open(f"datafiles/{team_info.team_name.lower()}.json", "r") as f:
            team_dict = json.load(f)

        return cls(
            team_name=team_info.team_name,
            starters=Subroster.from_TeamInfo(team_info.starters, team_dict),
            bench=Subroster.from_TeamInfo(team_info.bench, team_dict),
            reserves=[Player.from_dict(team_dict[name]) for name in team_info.reserve],
            draft_picks=team_info.draft_picks, # might be None if TeamInfo did not scrape this data
            record=team_info.record
        )
    
    def update_team_dict(self):
        """ Write data from Team object to JSON"""
        updated_dict = dict()
        self.starters.update_team_dict(updated_dict)
        self.bench.update_team_dict(updated_dict)
        for reserve in self.reserves:
            updated_dict[reserve.name] = reserve.to_dict() 

        with open(f"datafiles/{self.team_name.lower()}.json", "w") as f:
            json.dump(updated_dict, f)


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
            elif i < 15:
                self.bench.defense[i-13].current_season_log["performances"].append(performance)
            elif i == 15:
                self.starters.goalie.current_season_log["performances"].append(performance)
            else:
                self.bench.goalie.current_season_log["performances"].append(performance)
        
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

        # set all goals scored to 0 for defenders, goalies, and reserves
        for defender in self.starters.defense:
            defender.current_season_log["goals"].append(0)
            defender.current_season_log["shots_taken"].append(0)
        for defender in self.bench.defense:
            defender.current_season_log["goals"].append(0)
            defender.current_season_log["shots_taken"].append(0)

        self.starters.goalie.current_season_log["goals"].append(0)
        self.starters.goalie.current_season_log["shots_taken"].append(0)
        self.bench.goalie.current_season_log["goals"].append(0)
        self.bench.goalie.current_season_log["shots_taken"].append(0)

        for reserve in self.reserves:
            reserve.current_season_log["goals"].append(0)
            reserve.current_season_log["shots_taken"].append(0)