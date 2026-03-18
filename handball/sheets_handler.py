"""
Name: sheets_handler.py
Description: This file holds code for the SheetHandler class which will directly interface with the NHA Google Sheet
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/25/2025 1:39PM PST
"""

import os
import time
import random
from pathlib import Path
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from handball.constants import TEAM_RANGE, PLAYERS_RANGE, DRAFT_PICKS_RANGE, SHEET_ID_NUM, free_agents_ranges

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
#SHEET_ID = '1MqfFG71GlBdGwXlEtdgygbcxIjpcpUFi71dfvKr2oYQ'

# Find gs_key.json in project root (parent of handball package)
_current_file = Path(__file__)
_project_root = _current_file.parent.parent
SERVICE_ACCOUNT_FILE = str(_project_root / 'gs_key.json')

class SheetHandler:
    def __init__(self, sheet_id):
        self.sheet_id = sheet_id

        credentails = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE)
        service = build("sheets", 'v4', credentials=credentails)
        self.sheet = service.spreadsheets()
    
    @classmethod
    def from_user_input(cls):
        sheet_id = input("Enter Spreadsheet ID: ")
        return cls(sheet_id)

    # The "Values" functions will only get the cell values that 

    def _execute_with_retries(self, request, description: str):
        """
        Execute a Google Sheets API request with basic retry logic for
        rate-limit (HTTP 429) errors. Other errors are propagated immediately.
        """
        max_retries = 6
        base_delay = 2.0

        for attempt in range(max_retries):
            try:
                return request.execute()
            except HttpError as e:
                # Only retry on rate limit errors
                if e.resp is not None and getattr(e.resp, "status", None) == 429:
                    if attempt == max_retries - 1:
                        raise
                    # Exponential backoff with jitter
                    sleep_for = base_delay * (2 ** attempt)
                    sleep_for *= random.uniform(0.8, 1.2)
                    time.sleep(sleep_for)
                else:
                    # For non-rate-limit errors, fail fast
                    raise

    def get_full_team_values(self, team_name):
        """
        Special case of get_team_values()
        """
        return self.get_team_values(team_name, TEAM_RANGE)
    
    def update_full_team_values(self, team_name, edited_data):
        """
        Special case of update_team_values()
        """
        self.update_team_values(team_name, edited_data, TEAM_RANGE)

    def get_team_values(self, team_name, range):
        """
        Gets a range of cell values for a specified team

        Inputs:
            1. team_name (str): The name of the team for which information is being pulled
            2. range (str): The range of cells from which this function will request data

        Outputs:
            (Sheet Object?): A sheet object to be read in a specific order
        """
        range = f"{team_name}!{range}"
        request = self.sheet.values().get(
            spreadsheetId=self.sheet_id,
            range=range,
        )
        team_sheet = self._execute_with_retries(request, f"get_team_values({team_name}, {range})")
        return team_sheet.get("values", [])
    
    
    def update_team_values(self, team_name, edited_data, range):
        """
        Updates a range of cell values for a specified team

        Inputs:
            1. team_name (str): The name of the team for which information is being updated 
            2. range (str): The range of cells for which this function provide update data
            3. edited_data (list): This list of list should contain the updated value for the desired range
        
        Outputs: 
            None 
        """
        body = {"values": edited_data}
        range = f"{team_name}!{range}"
        self.sheet.values().update(
            spreadsheetId=self.sheet_id, 
            range=range, 
            valueInputOption= "RAW", 
            body=body
            ).execute()
        

    def get_draft_picks(self, team_name):
        """
        Gets all the draft_picks that a team owns

        Inputs:
            1. team_name (str): The name of the team for which information is being pulled

        Outputs:
            (list): strings denoting the draft picks
        """
        return self.get_team_values(team_name, DRAFT_PICKS_RANGE)

    
    def update_draft_picks(self, team_name, edited_data):
        """ Update draft picks on google sheet """

        self.update_team_values(team_name, edited_data, DRAFT_PICKS_RANGE)


        


    def get_player_notes(self, team_name):
        """
        Gets the notes for a range of cell values for a specified team

        ** Note: This method is optimized for getting notes and player names for a single team
            This operates under the assumption that all notes are contained in the player name cells

        Inputs:
            1. team_name (str): The name of the team for which the player notes are being pulled

        Outputs:
            (list): A list containing the notes for the players of the requested team
            (list): A list containing the names of the players of the requested team
        """
        notes = []
        names = []

        request = self.sheet.get(
            spreadsheetId=self.sheet_id,
            ranges=f"{team_name}!{PLAYERS_RANGE}",
            includeGridData=True,
        )
        result = self._execute_with_retries(request, f"get_player_notes({team_name})")
        
        grid_data = result.get('sheets', [])[0].get('data', [])[0]  # Sheet data
        values = grid_data.get('rowData', [])
        for row in values:
            for cell in row.get('values', []):
                value = cell.get("formattedValue", "")
                note = cell.get("note", "")
                
                notes.append(note)
                names.append(value)
        
        return notes, names
    
    def update_player_notes(self, team_name, new_notes):
        """
        Gets the notes for a range of cell values for a specified team

        ** Note: This method is optimized for getting notes and player names for a single team
            This operates under the assumption that all notes are contained in the player name cells

        Inputs:
            1. team_name (str): The name of the team for which the player notes are being pulled
            2. new_notes (list): Contains the notes for each player with gaps where there are breaks in the google sheet
                    -- There are 2 spaces in between the starters and bench and the bench and reserves

        Outputs:
            None
        """
        # First, reformat the notes
        rows = []
        for note in new_notes:
            rows.append({"values": [{"note": note}]})

        
        # Now, format into a request
        requests = [
            {
                "updateCells": {
                    "rows": rows,
                    "fields": "note",
                    "range": {
                        "sheetId": SHEET_ID_NUM[team_name],
                        "startRowIndex": 7,
                        "endRowIndex": 32,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2
                    }
                }
            }
        ]

        body = {"requests": requests}

        # Execute the batch update
        self.sheet.batchUpdate(
            spreadsheetId=self.sheet_id,
            body=body
        ).execute()

    def read_free_agents(self, position):
        """
        Read free agent data (cell values + notes) from the Free Agents sheet
        for a single position group.

        position: one of "Forwards", "Midfielders", "Defenders", "Goalies"

        Returns a list of PlayerInfo objects.
        """
        from handball.players import PlayerInfo

        col_map = {
            "Forwards":    {"value_range": "A3:C500", "name_range": "A3:A500"},
            "Midfielders": {"value_range": "E3:G500", "name_range": "E3:E500"},
            "Defenders":   {"value_range": "I3:K500", "name_range": "I3:I500"},
            "Goalies":     {"value_range": "M3:N500", "name_range": "M3:M500"},
        }
        if position not in col_map:
            raise ValueError(f"Unknown position group '{position}'. "
                             f"Expected one of {list(col_map.keys())}.")

        cfg = col_map[position]

        # 1) Read cell values (name + stats)
        values = self.get_team_values("Free Agents", cfg["value_range"])

        # 2) Read cell notes via gridData (notes live on the name column cells)
        request = self.sheet.get(
            spreadsheetId=self.sheet_id,
            ranges=f"Free Agents!{cfg['name_range']}",
            includeGridData=True,
        )
        result = self._execute_with_retries(request, f"read_free_agents({position})")
        grid_data = result.get("sheets", [])[0].get("data", [])[0]
        row_data = grid_data.get("rowData", [])

        notes = []
        for row in row_data:
            cells = row.get("values", [])
            note = cells[0].get("note", "") if cells else ""
            notes.append(note)

        # 3) Combine into PlayerInfo objects
        players: list[PlayerInfo] = []
        for i, row in enumerate(values):
            name = row[0] if len(row) > 0 else ""
            if not name:
                continue

            note = notes[i] if i < len(notes) else ""
            attributes: dict = {}
            if note:
                for line in note.split("\n"):
                    parts = line.split(" ", 1)
                    if len(parts) != 2:
                        continue
                    attr_name = parts[0].lower().rstrip(":")
                    attr_value = parts[1]
                    if attr_name == "age":
                        attr_value = int(attr_value)
                    elif attr_name == "injured":
                        attr_value = attr_value.strip().lower() == "true"
                    attributes[attr_name] = attr_value

            pos = attributes.get("position", position.rstrip("s"))
            is_goalie = str(pos).lower() == "goalie"

            if is_goalie:
                offense = 0.1
                defense = 0.1
                goalie_skill = float(row[1]) if len(row) > 1 else 0.0
            else:
                offense = float(row[1]) if len(row) > 1 else 0.0
                defense = float(row[2]) if len(row) > 2 else 0.0
                goalie_skill = 0.1

            players.append(PlayerInfo(
                name=name,
                position=str(pos),
                age=int(attributes.get("age", 0)),
                contract=str(attributes.get("contract", "")),
                injured=bool(attributes.get("injured", False)),
                offense=offense,
                defense=defense,
                goalie_skill=goalie_skill,
            ))

        return players

    def write_free_agents(self, free_agents_list, position):
        """ Write free agents to google sheet """
        print(f"writing {len(free_agents_list)} free agents")

        # Write the free agents to the google sheet
        range_index = free_agents_ranges[position](len(free_agents_list)+2)
        range = f"Free Agents!{range_index}"
        if position == "Goalies":
            edited_data = [
                [free_agent.name, round(free_agent.goalie_skill, 2)] 
                for free_agent in free_agents_list
            ]
        else:
            edited_data = [
                [free_agent.name, round(free_agent.offense, 2), round(free_agent.defense, 2)] 
                for free_agent in free_agents_list
            ]

        self.sheet.values().update(
            spreadsheetId=self.sheet_id, 
            range=range, 
            valueInputOption= "RAW", 
            body={"values": edited_data}
            ).execute()
        
        # Write the notes to the google sheet
        rows = []
        for free_agent in free_agents_list:
            rows.append({"values": [{"note": free_agent.get_notes()}]})

        
        # Now, format into a request
        requests = [
            {
                "updateCells": {
                    "rows": rows,
                    "fields": "note",
                    "range": {
                        "sheetId": SHEET_ID_NUM["Free Agents"],
                        "startRowIndex": 2,
                        "endRowIndex": len(free_agents_list)+3,
                        "startColumnIndex": ord(range_index[0])-ord('A'),
                        "endColumnIndex": ord(range_index[0])-ord('A')+1
                    }
                }
            }
        ]
        # Execute the batch update
        self.sheet.batchUpdate(
            spreadsheetId=self.sheet_id,
            body={"requests": requests}
        ).execute()

"""

#------TEST OUT NOTE READ AND WRITE------
import random
sheet_handler = SheetHandler()

values = sheet_handler.get_team_values("Boston", PLAYERS_RANGE)
i = 0

new_names = []
for j, value in enumerate(values):
    if j in [10,11,19,20]:
        new_names.append(values[j])
    else:
        i += 1
        new_names.append([f"Player {i}"])

sheet_handler.update_team_values("Boston", new_names, PLAYERS_RANGE)

notes, names = sheet_handler.get_player_notes("Boston")

for i in range(len(notes)):
    if i in [10,11,19,20]:
        continue
    age = random.randint(25, 32) 
    money = random.randint(5,25)
    years = random.randint(1,5)
    notes[i] = f"Age: {age}\nContract: {years}/${money}"

    # notes[i] = "" # This deletes notes

sheet_handler.update_player_notes("Boston", notes)

"""