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
import random
from dataclasses import dataclass
import numpy as np

from handball.simulation_vars import GAMES_IN_SEASON, MINOR_INJURIES, MODERATE_INJURIES, MAJOR_INJURIES, STAT_GEN


@dataclass
class InjuryReport():

    active_injury: bool
    injuries: list # list of tuples: (year, injury_type, injury duration, start_game, current)
    
    def __repr__(self):
        str_dump = [f"This player has sustained {len(self.injuries)} injuries."]
        for injury in self.injuries:
            if injury[3] + injury[2] >= GAMES_IN_SEASON:
                end_date = "END OF SEASON"
            elif injury[4]:
                end_date = "CURRENT"
            else:
                end_date = f"Game {injury[3] + injury[2]}"
            str_dump.append(f"{injury[0]}: {injury} (Game {injury[3]} – {end_date})")
        return "\n".join(str_dump)

    def __len__(self):
        return len(self.injuries)
    
    def add(self, year, injury_type, start_game):
        """ Add an injury to the player's report """
        if self.active_injury:
            return False # A player should not be able to get reinjured (since they are not playing)
        self.active_injury = True
        
        # Determine injury duration
        if injury_type in MINOR_INJURIES:
            injury_duration = max(0, np.round(np.random.normal(2, 1)))
        elif injury_type in MODERATE_INJURIES:
            injury_duration = max(0, np.round(np.random.normal(5, 2)))
        elif injury_type in MAJOR_INJURIES:
            injury_duration = max(0, np.round(np.random.normal(10, 3)))

        # Add the injury description
        self.injuries.append(
            (year, injury_type, injury_duration, start_game, True)
        )
    
        return injury_duration
    

    def update(self, game_number):
        """ Update status of an active injury """
        if self.active_injury:
            if game_number > (self.injuries[-1][2] + self.injuries[-1][3]):
                self.injuries[-1][4] = False
                self.active_injury = False


    def to_dict(self):
        """ Prepare object to be saved as a JSON, tuples are not supported by JSON """
        return {
            "active_injury": self.active_injury,
            "injuries": [list(injury) for injury in self.injuries]
        }
    
    @classmethod
    def from_dict(cls, d):
        """ Create injury report object from dicionary that was previouly jsonified
            tuples are not JSON supported, so they will have been made into tuples """
        d["injuries"] = [tuple(injury) for injury in d["injuries"]]
        return cls(**d)



@dataclass
class Player():
    name: str
    age: int

    years_in_league: int
    height: int
    weight: int
    position: str

    offense: float
    defense: float
    goalie_skill: float
    max_offense: float
    max_defense: float
    max_goalie_skill: float
    variance: float

    is_injured: bool
    injury_risk: float
    injury_log: InjuryReport

    contract_term: int
    contract_value: int
    years_remaining: int
    amount_paid: int
    rookie_contract: bool
    restricted_free_agent: bool

    awards_won: list

    current_season_log: dict

    @classmethod
    def create_new_player(cls, name, humor):
        """
        Create a player for the draft. This player not previously been in the league
        Take in player name and relevant humor
        """
        stats = dict()
        #Biographical
        stats["name"] = name
        stats["age"] = random.uniform(18,23)
        #stats["years_in_league"] = max(0, round(stats["age"] - random.uniform(18,21)))
        stats["years_in_league"] = 0
        stats["height"] = random.normalvariate(71, 2)
        stats["weight"] = random.normalvariate(175, 15)


        # Get overall score
        overall = max(0, random.normalvariate(*STAT_GEN[humor]))

        def split_rating(overall, is_midfielder):
            """
            Split the overall score into individual scores
            """
            if is_midfielder:
                std = 0.5
            else:
                std = 1
            
            first_score = min(10, max(0, random.uniform(overall, std)))
            second_score = (2*overall) - first_score

            scores = [first_score, second_score]

            if is_midfielder:
                random.shuffle(scores)
            else:
                scores.sort()

            return scores[0], scores[1]

        #Position
        temp_position = random.uniform(0,1)
        if temp_position < 0.118:
            stats["position"] = 'Goalie'
            offense = 0.1
            defense = 0.1
            goalie_skill = overall
        elif temp_position < 0.412:
            stats["position"] = 'Defense'
            defense, offense, = split_rating(overall, False)
            goalie_skill = 0.1
        elif temp_position < 0.706:
            stats["position"] = 'Offense'
            offense, defense, = split_rating(overall, False)
            goalie_skill = 0.1
        else:
            stats["position"] = "Midfielder"
            offense, defense, = split_rating(overall, True)
            goalie_skill = 0.1


        if humor == 0:
            offense = max(0, random.normalvariate(2, 1.5))
            defense = max(0, random.normalvariate(2, 1.5))
            if stats["position"] == 'Goalie':
                goalie_skill = max(0, random.normalvariate(2, 1.5))
            else:
                goalie_skill = 0.1
        elif humor == 1:
            offense = max(0, random.normalvariate(3, 1.75))
            defense = max(0, random.normalvariate(3, 1.75))
            if stats["position"] == 'Goalie':
                goalie_skill = max(0, random.normalvariate(3, 1.75))
            else:
                goalie_skill = 0.1
        elif humor == 2:
            offense = max(0, random.normalvariate(5, 1.75))
            defense = max(0, random.normalvariate(5, 1.75))
            if stats["position"] == 'Goalie':
                goalie_skill = max(0, random.normalvariate(5, 1.75))
            else:
                goalie_skill = 0.1
        else:
            offense = max(0, random.normalvariate(7, 1.5))
            defense = max(0, random.normalvariate(7, 1.5))
            if stats["position"] == 'Goalie':
                goalie_skill = max(0, random.normalvariate(7, 1.5))
            else:
                goalie_skill = 0.1

        # assign all stats, put a hard cap at skill level == 10
        stats["offense"] = min(10, offense)
        stats["defense"] = min(10, defense)
        stats["goalie_skill"] = min(10, goalie_skill)

        stats["max_offense"] = min(10, max(offense, offense + random.normalvariate(4, 0.5)))
        stats["max_defense"] = min(10, max(defense, defense + random.normalvariate(4, 0.5)))
        stats["max_goalie_skill"] = min(10, max(goalie_skill, goalie_skill + random.normalvariate(4, 0.5)))

        stats["variance"] = max(0, random.normalvariate(1.5, 0.5))
        
        #Injury
        stats["is_injured"] = False
        stats["injury_risk"] = max(0.0005, random.normalvariate(0.001, 0.001))
        stats["injury_log"] = InjuryReport(active_injury=False, injuries=list())

        #Contract: NEEDS ELABORATION
        stats["contract_term"] = 0
        stats["contract_value"] = 0
        stats["years_remaining"] = 0
        stats["amount_paid"] = 0
        stats["rookie_contract"] = True
        stats["restricted_free_agent"] = True

        #Trajectory

        #Misc
        stats["current_season_log"] = {
            "shots_taken": list(), # list of number of shots taken in each game (to get a shooting percentage)
            "goals": list(), # list of number of goals scored in each game
            "performances": list(), # list of overall game performances (contributions to overall team score, bench players scaled to full contributions)
        }
        stats["awards_won"] = []

        return cls(**stats)
    
    def __eq__(self, otherPlayer):
        """
        Override the equals method, have it check every attribute
        Print a message as to what is different between the two player objects
        """

        # Immediately throw a False if the comparator is None or not correct type
        if otherPlayer is None:
            return False
        elif not isinstance(otherPlayer, Player):
            return False
        
        diff_dict = dict()
        for key in self.__dict__:
            if self.__dict__[key] != otherPlayer.__dict__[key]:
                diff_dict[key] = [self.__dict__[key], otherPlayer.__dict__[key]]

        if len(diff_dict) > 0:
            print("Differences between Players:")
            for key, val in diff_dict.items():
                print(f"{key} ==> Player1: {val[0]}, Player2: {val[1]}")
            return False
        
        return True
                
    
    def to_dict(self):
        """ Prepare object to be saved as a json"""

        return {
            "name": self.name,
            "age": self.age,
            "years_in_league": self.years_in_league,
            "height": self.height,
            "weight": self.weight,
            "position": self.position,
            "offense": self.offense,
            "defense": self.defense,
            "goalie_skill": self.goalie_skill,
            "max_offense": self.max_offense,
            "max_defense": self.max_defense,
            "max_goalie_skill": self.max_goalie_skill,
            "variance": self.variance,
            "is_injured": self.is_injured,
            "injury_risk": self.injury_risk,
            "injury_log": self.injury_log.to_dict(),
            "contract_term": self.contract_term,
            "contract_value": self.contract_value,
            "years_remaining": self.years_remaining,
            "amount_paid": self.amount_paid,
            "rookie_contract": self.rookie_contract,
            "restricted_free_agent": self.restricted_free_agent,
            "awards_won": self.awards_won,
            "current_season_log": self.current_season_log
        }
    
    @classmethod
    def from_dict(cls, d):
        """ Create player object from dictionary representation """
        d["injury_log"] = InjuryReport.from_dict(d["injury_log"])
        return cls(**d)


    def advance_year(self):
        """ update information when year is advanced """
        self.age += 1
        self.years_in_league += 1
        self.years_remaining -= 1
    
    def injure(self, year, injury_type, current_game):
        """ injure player """
        self.is_injured = True
        self.has_been_injured = True
        self.injury_log.add(year, injury_type, current_game)
        ###Injury type and duration###############

    @property
    def total_season_goals(self):
        return sum(self.current_season_log["goals"])

        
