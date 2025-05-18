"""
Name: game_simulator.py
Description: This file will generate all necessary information to simulate games 
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
        Chase Harrington (chasehh@gmail.com)
Date: 1/20/2025 12:32PM PST
"""
## For each player, in each team, draw a random contribution to offense and defense
## Team A offense - Team B defense -> shots on goal, multiply by goalie save 
# Pass length correlates with amount of time off clock
import numpy as np

from utils import ProbabilityStack
from simulation_vars import REGULATION_TIME, K

class GameSimulator():
    def __init__(self, home_team, away_team):
        self.home_team = home_team
        self.away_team = away_team
        self.home_score = 0
        self.away_score = 0

        self.home_stats, self.away_stats = self.init_stats()

        self.ball_position = 20
        self.time = 0

        self.prob_stack = ProbabilityStack()

        self.offense_stats = None
        self.defense_stats = None

    def init_stats(self):
        # See game_mechanics.txt

        # Home Team Stats
        home_offense = 0
        home_defense = 0
        for player in self.home_team.starters:
            home_offense += np.random.normal(player.offense, player.variance)
            home_defense += np.random.normal(player.defense, player.variance)
        for player in self.home_team.bench:
            home_offense += np.random.normal(player.offense, player.variance)
            home_defense += np.random.normal(player.defense, player.variance)
        home_ratio = home_offense / (home_offense + away_defense)
        home_goalie = np.random.normal(self.home_team.goalie.goalie_score, self.home_team.goalie.variance)
        
        # Away Team Stats
        away_offense = 0
        away_defense = 0
        for player in self.away_team.starters:
            away_offense += np.random.normal(player.offense, player.variance)
            away_defense += np.random.normal(player.defense, player.variance)
        for player in self.away_team.bench:
            away_offense += np.random.normal(player.offense, player.variance)
            away_defense += np.random.normal(player.defense, player.variance)
        away_ratio = away_offense / (away_offense + home_defense)
        away_goalie = np.random.normal(self.away_team.goalie.goalie_score, self.away_team.goalie.variance)

        return (
            {"offense": home_offense, "defense": home_defense, "ratio": home_ratio, "goalie": home_goalie},
            {"offense": away_offense, "defense": away_defense, "ratio": away_ratio, "goalie": away_goalie}, 
        )


    def offensive_posession(self):
        """
        Run the offensive posession until scored or turned over
        """
        turnover_position = False
        scored = False

        #TODO: increment time and provide a way to short circuit mid posession

        # Evaluate shots and passes until something happens
        while True:
            if self.prob_stack.pop() < 1 / (1 + np.e ^ (-0.3 * (self.ball_position-34))): # odds of taking a shot
                # Take a shot
                if self.prob_stack.pop() < np.e ^ (-K * (40-self.ball_position)): # If shot was taken, was it on goal?
                    if self.prob_stack.pop() < self.offense_stats["offense"] / (self.offense_stats["offense"] + self.defense_stats["defense"] + self.defense_stats["goalie"]): #if shot was on goal, was a goal scored?
                        scored = True
                        # TODO: Attribute Score
                        break
                    elif self.prob_stack.pop() < 0.1: pass # offensive recovery
                    else: # turnover
                        turnover_position = self.ball_position + (100 - self.ball_position) * self.prob_stack.pop()
                        break
                elif self.prob_stack.pop() < 0.1: pass # offensive recovery
                else: # turnover
                        turnover_position = self.ball_position + (100 - self.ball_position) * self.prob_stack.pop()
                        break
            else:
                # Pass the ball
                if np.random.uniform(0,1) < self.offense_stats["ratio"]:
                    ball_position += min(100-self.ball_position, np.random.normal(4, 1.5)) # normal pass completed and advanced

        return scored, turnover_position


    def simulate_half(self):
            
            while self.time < REGULATION_TIME/2:

                scored, turnover_position = self.offensive_posession()

                # If scored, add point to the respecitve team
                if scored:
                    if self.home_posession:
                        self.home_score += 1
                    else:
                        self.away_score += 1

                # Change who has the ball
                self.home_posession = not self.home_posession
                temp = offense_stats
                offense_stats = defense_stats
                defense_stats = temp

                # Set the position of the ball (if turnover, put at specific location. else, put at the end of the court for an inbound)
                # we will always be going from 0 -> 100 for ball position.  Dont have one team go 100->0 (too complicated)
                if turnover_position:
                    self.ball_position = 100-turnover_position
                elif scored: 
                    self.ball_position = 20


    def simulate_game(self):
        k = 0.5

        ## Coin Flip
        if self.prob_stack.pop() <= 0.5:
            self.home_flip_winner = True
            self.home_posession = True
            self.offense_stats = self.home_stats
            self.defense_stats = self.away_stats
        else:
            self.home_flip_winner = False
            self.home_posession = False
            self.offense_stats = self.away_stats
            self.defense_stats = self.home_stats

        self.simulate_half()

        ## At the beginning of halftime, flip the possessions from the coinflip
        if self.home_flip_winner:
            self.offense_stats = self.away_stats
            self.defense_stats = self.home_stats
        else:
            self.offense_stats = self.home_stats
            self.defense_stats = self.away_stats

        self.simulate_half()

"""
def old__simulate_game(home_team, away_team):
    
    (home_score, away_score, 
    home_offense, home_defense, home_ratio, home_goalie, 
    away_offense, away_defense, away_ratio, away_goalie,
    has_posession, ball_at) = initialize_game(home_team, away_team)

 
    prob = np.random.rand(1, 1000)
    i = 0
    k = 0.5 ######### TO TUNE

    while True: ###This has to be amended to run until time runs out
        while True:
            if np.random.uniform(0,1) < home_ratio: #Is a pass successful?
                
                if np.random.uniform(0,1) < odds_of_taking_shot(ball_at): #If pass would be successful, was the pass a shot?
                    
                    if np.random.uniform(0,1) < np.e ^ (-k * (40-ball_at)): #If shot was taken, was it on goal?
                        #if shot was on goal, was a goal scored?
                        if np.random.uniform(0,1) < home_offense / (home_offense + away_defense + away_goalie): #if shot was on goal, was a goal scored?
                            home_score +=1 #goal scored
                            break
                        elif np.random.uniform(0,1) < 0.1: pass #offensive recovery
                        else: break #turnover
                    elif np.random.uniform(0,1) < 0.1: pass #offensive recovery
                    else: break #turnover
                else: ball_at += min(40,np.random.normal(4, 1.5)) #normal pass completed and advanced
            else: break #turnover

        while True:
            if np.random.uniform(0,1) < away_ratio: #Is a pass successful?
                
                if np.random.uniform(0,1) < 1-odds_of_taking_shot(ball_at): #If pass would be successful, was the pass a shot?
                    
                    if np.random.uniform(0,1) < np.e ^ (-k * (ball_at)): #If shot was taken, was it on goal?
                        #if shot was on goal, was a goal scored?
                        if np.random.uniform(0,1) < away_offense / (away_offense + home_defense + home_goalie): #if shot was on goal, was a goal scored?
                            away_score +=1 #goal scored
                            break
                        elif np.random.uniform(0,1) < 0.1: pass #offensive recovery
                        else: break #turnover
                    elif np.random.uniform(0,1) < 0.1: pass #offensive recovery
                    else: break #turnover
                else: ball_at -= max(0,np.random.normal(4, 1.5)) #normal pass completed and advanced
            else: break #turnover

    raise NotImplementedError
"""

def odds_of_taking_shot(yard):
    return 1 / (1 + np.e ^ (-0.3 * (yard-34)))
    

def generate_game_analysis(home_team, visiting_team, individual_performances, results_dump):

    #

    raise NotImplementedError

