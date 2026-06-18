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
from dataclasses import dataclass, field
import numpy as np

from handball.simulation_vars import (
    MINOR_INJURIES, MODERATE_INJURIES, MAJOR_INJURIES,
    NEW_PLAYER_MEAN, NEW_PLAYER_STD, STAT_CAP,
    INJURY_GROWTH_PENALTY, INJURY_DECLINE_MULTIPLIER, MAX_DECLINE_RATE,
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
    # Aging and decline parameters. These have sensible defaults so that older
    # saved data and tests that construct Player directly remain valid.
    peak_age: int = 27
    decline_age: int = 30
    decline_rate: float = 0.15

    is_injured: bool = False
    injury_risk: float = 0.001
    injury_log: InjuryReport = field(default_factory=lambda: InjuryReport(active_injury=False, injuries=list()))

    contract_term: int = 0
    contract_value: int = 0
    years_remaining: int = 0
    amount_paid: int = 0
    rookie_contract: bool = True
    restricted_free_agent: bool = True

    awards_won: list = field(default_factory=list)

    current_season_log: dict = field(default_factory=lambda: {
        "shots_taken": [],
        "goals": [],
        "performances": [],
        "saves": [],  # for goalies: saves made per game
        "goals_allowed": [],  # for goalies: goals allowed per game
    })

    @classmethod
    def create_new_player(cls, name, position):
        """
        Create a player for the draft. This player not previously been in the league.
        Takes in player name and position; the player's overall skill is sampled
        directly from a normal distribution (no rating tiers).
        """
        stats = dict()
        #Biographical
        stats["name"] = name
        stats["age"] = int(random.uniform(18,23))

        #stats["years_in_league"] = max(0, round(stats["age"] - random.uniform(18,21)))
        stats["years_in_league"] = 0
        stats["height"] = int(round(random.normalvariate(71, 2)))
        stats["weight"] = int(round(random.normalvariate(175, 15)))


        # Get overall score: sampled from N(NEW_PLAYER_MEAN, NEW_PLAYER_STD),
        # floored at 0 and capped at STAT_CAP.
        overall = min(STAT_CAP, max(0, random.normalvariate(NEW_PLAYER_MEAN, NEW_PLAYER_STD)))

        def split_overall(overall, is_midfielder):
            """
            Split the overall score into individual offense/defense scores
            """
            # Midfielders should have lower difference between offense and defense scores
            if is_midfielder:
                std = 0.5
            else:
                std = 1
            
            first_score = min(10, max(0, random.uniform(overall, std)))
            # Clamp the complementary score to [0, 10] too; for a low overall
            # the raw complement (2*overall - first_score) can go negative.
            second_score = min(10, max(0, (2 * overall) - first_score))
            scores = [first_score, second_score]

            # Offense and defense scores are randomized for midfielders
            # For forwards and defenders, higher score is assigend to main stat
            if is_midfielder:
                random.shuffle(scores)
            else:
                scores.sort(reverse=True)

            return scores[0], scores[1]

        #Position
        stats["position"] = position
        match position:
            case "Goalie":
                offense, defense = 0.1, 0.1
                goalie_skill = overall
            case "Defense":
                defense, offense, = split_overall(overall, is_midfielder=False)
                goalie_skill = 0.1
            case "Forward":
                offense, defense, = split_overall(overall, is_midfielder=False)
                goalie_skill = 0.1
            case "Midfielder":
                offense, defense, = split_overall(overall, is_midfielder=True)
                goalie_skill = 0.1
                

        # assign all stats, put a hard cap at skill level == 10
        # Max scores can exceed 10 as this will affect the growth rate of the players
        # - Though the player will in practice never get to exceed 10
        stats["offense"] = min(10.0, offense)
        stats["defense"] = min(10.0, defense)
        stats["goalie_skill"] = min(10.0, goalie_skill)

        stats["max_offense"] = max(offense, offense + random.normalvariate(2, 0.75))
        stats["max_defense"] = max(defense, defense + random.normalvariate(2, 0.75))
        stats["max_goalie_skill"] = min(10.0, max(goalie_skill, goalie_skill + random.normalvariate(2, 0.75)))

        if position == "Goalie":
            stats["max_offense"] = 0.1
            stats["max_defense"] = 0.1
        else:
            stats["max_goalie_skill"] = 0.1


        stats["variance"] = max(0, random.normalvariate(0.5, 0.5))
        stats["decline_rate"] = max(0.01, random.normalvariate(0.15, 0.1))

        stats["peak_age"] = int(random.normalvariate(25, sigma=1))
        stats["decline_age"] = stats["peak_age"] + int(random.uniform(1, 4))
        

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
            "saves": list(), # for goalies: saves made per game
            "goals_allowed": list(), # for goalies: goals allowed per game
        }
        stats["awards_won"] = []

        player = cls(**stats)

        # if the player is above 18, boost their stats a bit
        player.update_stats(player.age-18, rate_scale=0.25)

        return player
    
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
            "decline_rate": self.decline_rate,
            "peak_age": self.peak_age,
            "decline_age": self.decline_age,
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
        # Ensure current_season_log has all required keys (backwards compatibility)
        if "current_season_log" in d:
            log = d["current_season_log"]
            if "saves" not in log:
                log["saves"] = []
            if "goals_allowed" not in log:
                log["goals_allowed"] = []
        return cls(**d)


    def advance_year(self):
        """
        update information and stats when year is advanced
        new_year (int): The upcoming season
        """
        self.update_stats(years=1, rate_scale=1) # increment stats by 1 year with no rate scaling
        self.age += 1
        self.years_in_league += 1
        self.years_remaining -= 1

        # A new season starts with a clean stat sheet; last season's per-game
        # log should not carry over (otherwise stats accumulate forever).
        self.current_season_log = {
            "shots_taken": [],
            "goals": [],
            "performances": [],
            "saves": [],
            "goals_allowed": [],
        }

    
    def injure(self, year, injury_type):
        """ Injure the player: mark them injured and log the injury. Returns the
            injury duration in games. """
        self.is_injured = True
        return self.injury_log.add(year, injury_type)

    def tick_injury(self):
        """
        Advance the player's active injury by one game (decrement games
        remaining) and sync ``is_injured`` with the injury log. Called once per
        game the player's team plays while they are hurt.
        """
        self.injury_log.tick()
        self.is_injured = self.injury_log.active_injury

    def apply_injury_impact(self):
        """
        Degrade the player's development trajectory after a major injury,
        scaled by where they are in their career:
          - younger than peak: growth slows (lower the stat ceilings)
          - at/after peak: decline accelerates (raise decline_rate, capped)
        """
        if self.age < self.peak_age:
            self.max_offense = max(self.offense, self.max_offense * INJURY_GROWTH_PENALTY)
            self.max_defense = max(self.defense, self.max_defense * INJURY_GROWTH_PENALTY)
            self.max_goalie_skill = max(
                self.goalie_skill, self.max_goalie_skill * INJURY_GROWTH_PENALTY
            )
        else:
            self.decline_rate = min(MAX_DECLINE_RATE, self.decline_rate * INJURY_DECLINE_MULTIPLIER)

    def update_contract(self, contract_term, contract_salary, rookie):
        """ Set contract information """
        self.contract_term = contract_term
        self.contract_value = contract_salary

        if not rookie:
            self.rookie_contract = False
            self.restricted_free_agent = False


    def update_stats(self, years:int, rate_scale:float):
        """
        Update the player's stats going into next season (Note: age has not been incremented)
        This should be based off of a few things: 
            - General Progression (upwards until peak, noisy until decline age)

            TODO: Potential Future Items
            - Injuries Slow Progression
            - Starting Positions bolster progression, Reserver Positions hinder progression
        
        years (int): number of years to increment the stats by
        rate_scale (float): apply a scaling factor to the amount of progression made
        """

        # If younger than peak age, apply linear progression between current stats at max stats 
        # If at peak age but not going to hit decline age, apply random noise
        # If going to hit decline age or older, apply decline rate

        if (self.age < self.peak_age):
            offense_slope = (self.max_offense - self.offense)/(self.peak_age - self.age)
            defense_slope = (self.max_defense - self.defense)/(self.peak_age - self.age)
            goalie_slope = (self.max_goalie_skill - self.goalie_skill)/(self.peak_age - self.age)

            self.offense = min(10.0,self.offense + (offense_slope * years * rate_scale))
            self.defense = min(10.0, self.defense + (defense_slope * years * rate_scale))
            self.goalie_skill = min(10.0, self.goalie_skill + (goalie_slope * years * rate_scale))

        elif (self.age >= self.peak_age) and (self.age < self.decline_age - 1):
            if self.position != "Goalie":
                self.offense = min(10.0, self.offense + (random.normalvariate(0.0, 0.25) * rate_scale))
                self.defense = min(10.0, self.defense + (random.normalvariate(0.0, 0.25) * rate_scale))
            else:
                self.goalie_skill = min(10.0, self.goalie_skill + (random.normalvariate(0.0, 0.25) * rate_scale))
        else:
            if self.position == "Goalie":
                self.goalie_skill = self.goalie_skill * (1 - self.decline_rate)
            else:
                self.offense = min(10.0, self.offense * (1 - self.decline_rate)) # TODO: apply rate scale to decline
                self.defense = min(10.0, self.defense * (1 - self.decline_rate)) # TODO: apply rate scale to decline
    
    @property
    def total_season_goals(self):
        return sum(self.current_season_log["goals"])

    @property
    def total_season_saves(self):
        return sum(self.current_season_log["saves"])

    @property
    def total_season_goals_allowed(self):
        return sum(self.current_season_log["goals_allowed"])

    @property
    def save_percentage(self):
        total_shots_faced = self.total_season_saves + self.total_season_goals_allowed
        if total_shots_faced == 0:
            return 0.0
        return self.total_season_saves / total_shots_faced

        

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
    def from_Player(cls, player_obj:Player):
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


    def update_from_Player(self, player_obj:Player):
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
