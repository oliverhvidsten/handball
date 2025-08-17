"""
Name: creation_functions.py
Description: This file holds functions that do one of jobs like creating the intial batch of players for each team.
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 8/10/2025 4:53PM PST
"""

from handball.teams import Team, TeamInfo, Subroster
from handball.players import Player, PlayerInfo
from handball.sheets_handler import SheetHandler

def create_team(team_name, coach_list, player_list, current_year, sheet_handler):

    """
    team_name (str): 
    coach_list (list[str]): 
    player_list (list[tuple]):
    current_year (int):
    sheet_hander (SheetHandler):
    
    """

    total_salaries = 0

    # each element of the player list should be: (name, position, and humor) in a tuple
    player_dict = {
        "forwards": list(),
        "midfielders": list(),
        "defenders": list(), 
        "goalies": list()
    }
    for player_items in player_list:
        player = Player.create_new_player(*player_items)

        total_salaries += player.contract_value
        
        # change the position into the position key ("Forward -> forwards")
        pos_key = f"{player_items[2].lower()}s"
        player_dict[pos_key].append(player)

    
    # Get starters
    starters = Subroster(
        forwards=player_dict["forwards"][:3],
        defense=player_dict["defenders"][:3],
        midfielders=player_dict["midfielders"][:3],
        goalie=player_dict["goalies"][0]

    )
    # Delete the starters from the lists
    for key in ["forwards", "defenders", "midfielders"]:
        player_dict[key][3:]
    del player_dict["goalies"][0]

    # Get bench players
    bench = Subroster(
        forwards=player_dict["forwards"][:3],
        defense=player_dict["defenders"][:3],
        midfielders=player_dict["midfielders"][:3],
        goalie=player_dict["goalies"][0]
    )
    # Delete the bench players from the lists
    for key in ["forwards", "defenders", "midfielders"]:
        player_dict[key][3:]
    del player_dict["goalies"][0]

    # Put the rest of the players into the reserves
    reserves = list()
    for player_list in player_dict.values():
        for player in player_list:
            reserves.append(player)

    draft_picks = {
        "First Round": [f"{team_name} {current_year + i}" for i in range(10)], 
        "Second Round": [f"{team_name} {current_year + i}" for i in range(10)]
    }
        
    team_obj = Team(
        team_name=team_name, 
        starters=starters, 
        bench=bench, 
        reserves=reserves, 
        draft_picks=draft_picks,
        record=[0,0,0]
    )

    teaminfo_obj = TeamInfo.from_sheet(
        sheet_handler=sheet_handler,
        team_name=team_name, 
        get_draft_picks=True
        )
    
    teaminfo_obj.coaches=coach_list
    teaminfo_obj.starters = {
            "Forwards": [PlayerInfo.from_Player(player) for player in starters.forwards], # type: ignore
            "Midfielders": [PlayerInfo.from_Player(player) for player in starters.midfielders], # type: ignore
            "Defense": [PlayerInfo.from_Player(player) for player in starters.defense] # type: ignore
        },
    teaminfo_obj.bench = {
            "Forwards": [PlayerInfo.from_Player(player) for player in bench.forwards], # type: ignore
            "Midfielders": [PlayerInfo.from_Player(player) for player in bench.midfielders], # type: ignore
            "Defense": [PlayerInfo.from_Player(player) for player in bench.defense] # type: ignore
        },
    teaminfo_obj.reserve=[PlayerInfo.from_Player(player) for player in reserves] # type: ignore
    teaminfo_obj.draft_picks = draft_picks
    teaminfo_obj.record = [0,0,0]
    teaminfo_obj.total_salaries = total_salaries


    # Save Team Object as a json
    team_obj.update_team_dict()

    # Write TeamInfo Object to the google sheet
    teaminfo_obj.update_sheet(sheet_handler=sheet_handler)
