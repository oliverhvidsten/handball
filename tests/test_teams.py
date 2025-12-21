"""
Name: test_players.py
Description: Holds unit tests for funcitons in players.py
Author: Oliver Hvidsten
Date: 8/3/2025 8:11PM PST
"""
import pytest
from handball.teams import TeamInfo, Team, Subroster, SheetHandler

def test_load_team_from_sheet():
    """
    Load the example team and check if everything went smoothly
    """
    with open("../cred.txt", "r") as f:
        line = f.readline()

    sheet_handler = SheetHandler(sheet_id=line)

    team_info = TeamInfo.from_sheet(
        sheet_handler=sheet_handler, 
        team_name="EXAMPLE", 
        get_draft_picks=False)
    
    team_obj = Team.from_TeamInfo(team_info=team_info)

    assert (team_obj.team_name == "<Location>" and 
            isinstance(team_obj.starters, Subroster) and
            isinstance(team_obj.bench, Subroster) and 
            team_obj.reserves == [""]
            )


def test_load_teaminfo_from_sheet():
    """
    Load the example team and check if everything went smoothly
    """
    with open("../cred.txt", "r") as f:
        line = f.readline()

    print(f"Credentials: {line}")
    sheet_handler = SheetHandler(sheet_id=line)

    team_info = TeamInfo.from_sheet(
        sheet_handler=sheet_handler, 
        team_name="EXAMPLE", 
        get_draft_picks=False)
    

    assert (team_info.team_name == "EXAMPLE" and 
            team_info.coaches == ["HC Name", "OC Name", "DC Name"] and
            len(team_info.starters) == 4 and 
            len(team_info.bench) == 4 and
            len(team_info.reserve) == 4 and
            team_info.record == [0,0,0] and
            team_info.total_salaries == 8
            )

