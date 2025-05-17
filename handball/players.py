"""
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

class Player():
    def __init__(self, name, humor):
        #Biographical
        self.name = name
        self.age = random.normalvariate(27, 3)
        self.years_in_league = max(0, round(self.age - random.uniform(18,21)))
        self.height = random.normalvariate(71, 2)
        self.weight = random.normalvariate(175, 15)

        #Position
        temp_position = random.uniform(0,1)
        if temp_position < 0.04:
            self.position = 'Goalie'
        elif temp_position < 0.36:
            self.position = 'Fullback'
        elif temp_position < 0.52:
            self.position = 'Center'
        elif temp_position < 0.68:
            self.position = 'Pivot'
        else:
            self.position = 'Winger'

        #Stat Generation
        if humor == 0:
            self.offense = max(0, random.normalvariate(2, 1.5))
            self.defense = max(0, random.normalvariate(2, 1.5))
            if self.position == 'Goalie':
                self.goalie_skill = max(0, random.normalvariate(2, 1.5))
            else:
                self.goalie_skill = 0.1
        elif humor == 1:
            self.offense = max(0, random.normalvariate(4, 1.75))
            self.defense = max(0, random.normalvariate(4, 1.75))
            if self.position == 'Goalie':
                self.goalie_skill = max(0, random.normalvariate(4, 1.75))
            else:
                self.goalie_skill = 0.1
        elif humor == 2:
            self.offense = max(0, random.normalvariate(6, 1.75))
            self.defense = max(0, random.normalvariate(6, 1.75))
            if self.position == 'Goalie':
                self.goalie_skill = max(0, random.normalvariate(6, 1.75))
            else:
                self.goalie_skill = 0.1
        else:
            self.offense = max(0, random.normalvariate(8, 1.5))
            self.defense = max(0, random.normalvariate(8, 1.5))
            if self.position == 'Goalie':
                self.goalie_skill = max(0, random.normalvariate(8, 1.5))
            else:
                self.goalie_skill = 0.1

        self.max_offense = max(self.offense, self.offense + random.normalvariate(4, 0.5))
        self.max_defense = max(self.defense, self.defense + random.normalvariate(4, 0.5))
        self.max_goalie = max(self.goalie_skill, self.goalie_skill + random.normalvariate(4, 0.5))

        self.variance = max(0, random.normalvariate(1.5, 0.5))
        
        #Injury
        self.is_injured = False
        self.has_been_injured = False
        self.year_of_injury = None 
        self.injury_risk = max(0.0005, random.normalvariate(0.001, 0.001))
        self.injury_log = {}

        #Contract: NEEDS ELABORATION
        self.contract_term = 0
        self.contract_value = 0
        self.years_remaining = 0
        self.amount_paid = 0
        self.rookie_contract = False
        self.restricted_free_agent = False

        #Trajectory



        #Misc
        self.awards_won = []

    def advance_year(self):
        self.age += 1
        self.years_in_league += 1
    
    def injure(self):
        self.is_injured = True
        self.has_been_injured = True
        self.year_of_injury = None ###############
        self.injury_log[self.year_of_injury] = 'Injury Type'
        ###Injury type and duration###############