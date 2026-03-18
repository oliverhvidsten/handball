"""
Name: test_teams.py
Description: Holds unit tests for functions in teams.py
Author: Oliver Hvidsten
Date: 8/3/2025 8:11PM PST
"""
import json
from pathlib import Path

import pytest

from handball.players import Player
from handball.teams import TeamInfo, Team, Subroster, SheetHandler

def test_load_team_from_sheet():
    """
    Load the example team and check if everything went smoothly
    """
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    with cred_path.open("r") as f:
        line = f.readline()

    sheet_handler = SheetHandler(sheet_id=line)

    team_info = TeamInfo.from_sheet(
        sheet_handler=sheet_handler, 
        team_name="EXAMPLE", 
        get_draft_picks=False)
    
    team_obj = Team.from_TeamInfo(team_info=team_info)

    assert team_obj.team_name == "EXAMPLE"
    assert isinstance(team_obj.starters, Subroster)
    assert isinstance(team_obj.bench, Subroster)
    assert isinstance(team_obj.reserves, list)


def test_load_teaminfo_from_sheet():
    """
    Load the example team and check if everything went smoothly
    """
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    with cred_path.open("r") as f:
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


@pytest.mark.parametrize("team_name", ["Boston", "San Francisco"])
def test_real_team_json_can_be_loaded(team_name):
    """
    Ensure that real team JSON files exist and can be loaded into Player objects.

    This verifies that the persisted team_dict for real teams is present and
    structurally compatible with the current Player schema.
    """
    datafile_path = Path("/Users/oliverhvidsten/Documents/handball/handball/datafiles") / f"{team_name.lower()}.json"
    assert datafile_path.exists(), f"Expected team datafile for {team_name} at {datafile_path}"

    with datafile_path.open("r") as f:
        team_dict = json.load(f)

    assert isinstance(team_dict, dict) and team_dict, "Team JSON should be a non-empty dict"

    # Attempt to construct Player objects for a sample of entries
    for i, (name, pdata) in enumerate(team_dict.items()):
        player = Player.from_dict(pdata)
        assert player.name == name
        # Only need to validate a few entries to keep the test fast
        if i >= 4:
            break


@pytest.mark.parametrize("team_name", ["Boston", "San Francisco"])
def test_real_team_can_be_loaded_from_sheet(team_name):
    """
    Ensure that real teams can be loaded from the Google Sheet into TeamInfo.

    This exercises the SheetHandler + TeamInfo.from_sheet path for real teams.
    """
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    assert cred_path.exists(), f"Missing credentials file at {cred_path}"

    with cred_path.open("r") as f:
        sheet_id = f.readline().strip()

    sheet_handler = SheetHandler(sheet_id=sheet_id)

    team_info = TeamInfo.from_sheet(
        sheet_handler=sheet_handler,
        team_name=team_name,
        get_draft_picks=False,
    )

    # Basic structural checks
    assert team_info.team_name == team_name
    assert isinstance(team_info.coaches, list) and len(team_info.coaches) == 3
    assert isinstance(team_info.starters, dict)
    assert isinstance(team_info.bench, dict)
    assert isinstance(team_info.reserve, list)


@pytest.mark.parametrize("team_name", ["Boston", "San Francisco"])
def test_real_teaminfo_and_team_round_trip(team_name):
    """
    Load TeamInfo from the Google Sheet and then build a full Team from it.

    This exercises the full pipeline:
    SheetHandler -> TeamInfo.from_sheet -> Team.from_TeamInfo
    and ensures that real teams are fully constructible.
    """
    cred_path = Path(__file__).resolve().parent.parent / "cred.txt"
    assert cred_path.exists(), f"Missing credentials file at {cred_path}"

    with cred_path.open("r") as f:
        sheet_id = f.readline().strip()

    sheet_handler = SheetHandler(sheet_id=sheet_id)

    team_info = TeamInfo.from_sheet(
        sheet_handler=sheet_handler,
        team_name=team_name,
        get_draft_picks=False,
    )

    team = Team.from_TeamInfo(team_info)

    # Basic structural checks on Team
    assert team.team_name == team_name
    assert isinstance(team.starters, Subroster)
    assert isinstance(team.bench, Subroster)
    assert isinstance(team.reserves, list)
    assert isinstance(team.record, list) and len(team.record) == 3

