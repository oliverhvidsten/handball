"""
Name: players.py
Description: Holds the Player class and other relvant items for holding player information
Author: Chase Harrington, Oliver Hvidsten 


Non-Exhaustive List of Player attributes
- Physical Attributes (age, height, weight)
- Position
- Visible Stats
    - Offense Stat
    - Defense Stat
    - Goalie Stat (for goalies, only this will be shown)
* I think it would e
- Invisible Static Attributes
    - Max Offense Stat
    - Max Defense Stat
    - Max Goalie Stat (very low for most players)
- Invisilble Non-Static Attributes
    - Growth Rate
    - Injury Risk

"""
from dataclasses import dataclass
import numpy as np

from handball.simulation_vars import (
    MINOR_INJURIES, MODERATE_INJURIES, MAJOR_INJURIES,
)


@dataclass
class InjuryReport():

    active_injury: bool
    injuries: list  # records: [year, injury_type, duration, games_remaining, current]

    def __repr__(self):
        str_dump = [f"This player has sustained {len(self.injuries)} injuries."]
        for year, itype, duration, remaining, current in self.injuries:
            status = f"{remaining} games remaining" if current else "recovered"
            str_dump.append(f"{year}: {itype} (duration {duration}, {status})")
        return "\n".join(str_dump)

    def __len__(self):
        return len(self.injuries)

    def add(self, year, injury_type):
        """
        Add an injury to the player's report. Duration is sampled in *games*
        from a severity-based distribution; the injury then ticks down one game
        at a time via tick(). Returns the duration, or False if already injured.
        """
        if self.active_injury:
            return False  # can't be re-injured while already out

        self.active_injury = True
        if injury_type in MINOR_INJURIES:
            duration = int(np.round(np.random.normal(2, 1)))
        elif injury_type in MODERATE_INJURIES:
            duration = int(np.round(np.random.normal(5, 2)))
        elif injury_type in MAJOR_INJURIES:
            duration = int(np.round(np.random.normal(10, 3)))
        else:
            duration = 1
        duration = max(1, duration)  # every injury sidelines at least one game

        # [year, type, duration, games_remaining, current]
        self.injuries.append([year, injury_type, duration, duration, True])
        return duration

    def tick(self):
        """
        Advance one game: decrement the active injury's games-remaining and
        mark it recovered once it reaches zero. Season-agnostic (no dependence
        on an absolute game counter), so injuries carry across periods/seasons.
        """
        if self.active_injury:
            last = self.injuries[-1]
            last[3] = max(0, last[3] - 1)  # games_remaining
            if last[3] <= 0:
                last[4] = False
                self.active_injury = False

    @property
    def games_remaining(self):
        """Games left on the current injury (0 if healthy)."""
        if self.active_injury and self.injuries:
            return self.injuries[-1][3]
        return 0

    def to_dict(self):
        """ Prepare object to be saved as JSON. """
        return {
            "active_injury": self.active_injury,
            "injuries": [list(injury) for injury in self.injuries]
        }

    @classmethod
    def from_dict(cls, d):
        """ Create injury report object from a previously jsonified dictionary.
            Records are kept as mutable lists so tick() can update them. """
        d["injuries"] = [list(injury) for injury in d["injuries"]]
        return cls(**d)


@dataclass
class PlayerInfo():
    """
    Class that holds all of the information held in the google sheet.
    Lighter class to be used when full player class is not necessary

    Note: position refers to original position, not positions currently being played
    """
    name: str
    position: str
    age: int
    contract: str
    injured: bool
    offense: float
    defense: float
    goalie_skill: float

    def __str__(self):
        return f"{self.name} (pos={self.position}, age={self.age}, contract={self.contract}, injured={self.injured})"
    def __repr__(self):
        return self.__str__()
    def __eq__(self, other):
        if not isinstance(other, PlayerInfo):
            return False
        return self.name == other.name and self.position == other.position and self.age == other.age and self.contract == other.contract and self.injured == other.injured and self.offense == other.offense and self.defense == other.defense and self.goalie_skill == other.goalie_skill
    def __hash__(self):
        return hash((self.name, self.position, self.age, self.contract, self.injured, self.offense, self.defense, self.goalie_skill))

    @classmethod
    def from_sheet(cls, sheet_row:list, notes_dict:dict):
        """
        Create a PlayerInfo object from the information derived from the google sheet.
        Notably, this contains the players name, stats, and any info in the notes section
        Tests for this should be rigorous about what is found in the notes section as this is 
        not meant to be flexible
        """
        name = sheet_row[1]
        note = notes_dict[name]
 
        lines = note.split("\n")
        attributes = dict()
        for line in lines:
            attr_name, attr_value = line.lower().split(" ", 1) # split on first space

            if attr_name[:-1] in ["age"]: # attributes to be casted to int
                attr_value = int(attr_value)
            elif attr_name[:-1] in ["injured"]: # attributes to be casted to bool
                attr_value = bool(attr_value)
            attributes[attr_name[:-1]] = attr_value

        # Assign stats (given position information)
        if attributes["position"].lower() == "goalie":
            attributes["offense"] = 0.1
            attributes["defense"] = 0.1
            attributes["goalie_skill"] = sheet_row[2]
        else:
            attributes["offense"] = sheet_row[2]
            attributes["defense"] = sheet_row[3]
            attributes["goalie_skill"] = 0.1

        return cls(name=name,**attributes)
    
    def to_sheet(self):
        """
        Return the same info out as the from_sheet method requests
        Once again, this is not a flexible method
        """
        stats = (self.offense, self.defense, self.goalie_skill)
        note = "\n".join([
            f"Age: {self.age}",
            f"Position: {self.position}",
            f"Contract: {self.contract}", 
            f"Injured: {self.injured}"
            ])
        
        return self.name, note, stats
    
    @classmethod
    def from_Player(cls, player_obj):
        """
        Create PlayerInfo object from Player object
        """


        return PlayerInfo(
            name=player_obj.name,
            position=player_obj.position,
            age=player_obj.age,
            contract=
            f"{player_obj.contract_term}/${player_obj.contract_value}{' (R)' if player_obj.rookie_contract else ''}",
            injured=player_obj.is_injured,
            offense=player_obj.offense,
            defense=player_obj.defense,
            goalie_skill=player_obj.goalie_skill
        )


    def update_from_Player(self, player_obj):
        """
        Update PlayerInfo attributes (name and position will never be updated)
        """
        self.age = player_obj.age
        self.contract = f"{player_obj.contract_term}/${player_obj.contract_value}"
        self.injured = player_obj.is_injured

        self.offense = player_obj.offense
        self.defense = player_obj.defense
        self.goalie_skill = player_obj.goalie_skill

    def name_and_stats(self, is_goalie=False):
        """
        Return the player's name and stats
        """
        if is_goalie:
            return [self.name, round(self.goalie_skill, 2)]
        else:
            return [self.name, round(self.offense, 2), round(self.defense, 2)]
        
    def get_notes(self):
        return"\n".join([
            f"Age: {self.age}",
            f"Position: {self.position}",
            f"Contract: {self.contract}", 
            f"Injured: {self.injured}"
            ])
