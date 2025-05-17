"""
Name: game_simulator.py
Description: This file will generate all necessary information to simulate games 
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 12:32PM PST
"""
## For each player, in each team, draw a random contribution to offense and defense
## Team A offense - Team B defense -> shots on goal, multiply by goalie save 
# Pass length correlates with amount of time off clock
import numpy as np

def simulate_game(home_team, away_team):
    home_score = 0
    away_score = 0

    # See game_mechanics.txt
    home_offense = 0
    home_defense = 0
    for player in home_team.starters:
        home_offense += np.random.normal(player.offense, player.variance)
        home_defense += np.random.normal(player.defense, player.variance)
    for player in home_team.bench:
        home_offense += np.random.normal(player.offense, player.variance)
        home_defense += np.random.normal(player.defense, player.variance)
    home_ratio = home_offense / (home_offense + away_defense)
    home_goalie = np.random.normal(home_team.goalie.goalie_score, home_team.goalie.variance)
    
    away_offense = 0
    away_defense = 0
    for player in away_team.starters:
        away_offense += np.random.normal(player.offense, player.variance)
        away_defense += np.random.normal(player.defense, player.variance)
    for player in away_team.bench:
        away_offense += np.random.normal(player.offense, player.variance)
        away_defense += np.random.normal(player.defense, player.variance)
    away_ratio = away_offense / (away_offense + home_defense)
    away_goalie = np.random.normal(away_team.goalie.goalie_score, away_team.goalie.variance)

    has_posession = 1

    ball_at = 20


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

def odds_of_taking_shot(yard):
    return 1 / (1 + np.e ^ (-0.3 * (yard-34)))
    

def generate_game_analysis(home_team, visiting_team, individual_performances, results_dump):

    #

    raise NotImplementedError