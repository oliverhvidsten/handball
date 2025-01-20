"""
Name: record_keeper.py
Description: This file will generate all necessary information to operate the Record Keeper 
- Have a section for each player and every game they have ever played (record public stats and # of goals scored)
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 12:39PM PST
"""


class RecordKeeper():
    def __init__(self):
        raise NotImplementedError
    
    def to_json(self):
        raise NotImplementedError

    def from_json(self, json_string):
        raise NotImplementedError
    
    def add_game_information(self):
        """
        This should add the information from a game into the players' historical records
        
        """
        raise NotImplementedError
