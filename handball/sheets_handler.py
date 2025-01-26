"""
Name: sheets_handler.py
Description: This file holds code for the SheetHandler class which will directly interface with the NHA Google Sheet
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/25/2025 1:39PM PST
"""

import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from constants import TEAM_RANGE, PLAYERS_RANGE, SHEET_ID_NUM

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
#SHEET_ID = '1MqfFG71GlBdGwXlEtdgygbcxIjpcpUFi71dfvKr2oYQ'
SERVICE_ACCOUNT_FILE = './gs_key.json'

class SheetHandler:
    def __init__(self):
        self.sheet_id = input("Enter Spreadsheet ID: ")

        credentails = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE)
        service = build("sheets", 'v4', credentials=credentails)
        self.sheet = service.spreadsheets()



    # The "Values" functions will only get the cell values that 
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
        team_sheet = self.sheet.get(
            spreadsheetId=self.sheet_id, 
            range=range,
            ).execute()
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

        result = self.sheet.get(
            spreadsheetId=self.sheet_id, 
            ranges=f"{team_name}!{PLAYERS_RANGE}", 
            includeGridData=True
            ).execute()
        
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
            2. new_notes (list): Conains the notes for each player with gaps where there are breaks in the google sheet
                    -- There are 2 spaces in between the starers and bench and the bench and reserves

        Outputs:
            None
        """
        # First, reformat the notes
        rows = []
        for note in new_notes:
            rows.append({"values": [{"note": note}]})




        # Fetch the spreadsheet metadata to get the sheet IDs
        spreadsheet = self.sheet.get(spreadsheetId=self.sheet_id).execute()

        # Print all sheet names and their sheet IDs
        sheets = spreadsheet.get('sheets', [])
        print(sheets)
        for sheet in sheets:
            print(f"{sheet['properties']['title']}: {sheet['properties']['sheetId']}")
        
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
        

"""
------TEST OUT NOTE READ AND WRITE------
         
sheet_handler = SheetHandler()
notes, names = sheet_handler.get_player_notes("Boston")

for note, name in zip(notes, names):
    print(f"{name}: {note}")

for i in range(len(notes)):
    # notes[i] = f"<WRITE SOMETHING HERE>"
    # notes[i] = "" # This deletes notes

sheet_handler.update_player_notes("Boston", notes)

"""