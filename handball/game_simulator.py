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
from itertools import chain

from utils import ProbabilityStack
from teams import Team
from simulation_vars import (
    REGULATION_TIME, K, TIME_PER_PASS, TIME_PER_SHOT, MAIN_STAT, SECONDARY_STAT, MIDDIE_STATS, TIME_AFTER_SCORE, STARTER_MINUTES, BENCH_MINUTES
    )

class GameSimulator():
    def __init__(self, home_team:Team, away_team:Team, allow_tie=True):
        self.home_team = home_team
        self.away_team = away_team
        self.allow_tie = allow_tie

        self.home_score = 0
        self.away_score = 0

        self.home_stats, home_scorer_stats, self.away_stats, away_scorer_stats = self.init_stats()

        self.ball_position = 20
        self.game_clock = GameClock()

        self.stat_tracker = StatTracker(
                                home_team=home_team, 
                                home_scorer_stats=home_scorer_stats, 
                                away_team=away_team, 
                                away_scorer_stats=away_scorer_stats
                                )
        self.prob_stack = ProbabilityStack()

        self.offense_stats = None
        self.defense_stats = None

    def init_stats(self):
        # See game_mechanics.txt

        def calculate_stats(team_obj:Team):
            """ Calculate individual performances and save those in overall team performances """

            minutes_list = [STARTER_MINUTES if i < 9 else BENCH_MINUTES for i in range(15)]

            ## Calculate offense stats for each player
            start_forward_offense = [np.random.normal(forward.offense, forward.variance) for forward in team_obj.starters.forwards] * MAIN_STAT
            start_middie_offense = [np.random.normal(middie.offense, middie.variance) for middie in team_obj.starters.midfielders] * MIDDIE_STATS
            start_defense_offense = [np.random.normal(defense.offense, defense.variance) for defense in team_obj.starters.defense] * SECONDARY_STAT
            bench_forward_offense = [np.random.normal(forward.offense, forward.variance) for forward in team_obj.bench.forwards] * MAIN_STAT
            bench_middie_offense = [np.random.normal(middie.offense, middie.variance) for middie in team_obj.bench.midfielders] * MIDDIE_STATS
            bench_defense_offense = [np.random.normal(defense.offense, defense.variance) for defense in team_obj.bench.defense] * SECONDARY_STAT

            # Get the stats for just the possible scorers (will be used to assign probability of scoring later on)
            scorer_stats = [start_forward_offense, start_middie_offense, bench_forward_offense, bench_middie_offense]
            scorer_stats = [stat for stats in scorer_stats for stat in stats]

            # Get the offense stat by summing over all stats
            combined_list = [start_forward_offense, start_middie_offense, start_defense_offense, bench_forward_offense, bench_middie_offense, bench_defense_offense]
            team_obj.update_performances(combined_list)
            offense = sum(np.array([stat for stats in combined_list for stat in stats]) * (np.array(minutes_list) / 60))


            ## Calculate defense stats for each player
            start_forward_defense = [np.random.normal(forward.defense, forward.variance) for forward in team_obj.starters.forwards] * SECONDARY_STAT
            start_middie_defense = [np.random.normal(middie.defense, middie.variance) for middie in team_obj.starters.midfielders] * MIDDIE_STATS
            start_defense_defense = [np.random.normal(defense.defense, defense.variance) for defense in team_obj.starters.defense] * MAIN_STAT
            bench_forward_defense = [np.random.normal(forward.defense, forward.variance) for forward in team_obj.bench.forwards] * SECONDARY_STAT
            bench_middie_defense = [np.random.normal(middie.defense, middie.variance) for middie in team_obj.bench.midfielders] * MIDDIE_STATS
            bench_defense_defense = [np.random.normal(defense.defense, defense.variance) for defense in team_obj.bench.defense] * MAIN_STAT

            combined_list = [start_forward_defense, start_middie_defense, start_defense_defense, bench_forward_defense, bench_middie_defense, bench_defense_defense]
            defense = sum(np.array([stat for stats in combined_list for stat in stats]) * (np.array(minutes_list) / 60))

            return offense, defense, scorer_stats

        home_offense, home_defense, home_scorer_stats = calculate_stats(self.home_team)
        away_offense, away_defense, away_scorer_stats = calculate_stats(self.away_team)

 
        home_ratio = home_offense / (home_offense + away_defense)
        home_goalie = np.random.normal(self.home_team.goalie.goalie_score, self.home_team.goalie.variance)
        home_goalie_reserve = np.random.normal(self.home_team.bench.goalie.goalie_skill, self.home_team.bench.goalie.variance)

        away_ratio = away_offense / (away_offense + home_defense)
        away_goalie = np.random.normal(self.away_team.starters.goalie.goalie_skill, self.away_team.starters.goalie.variance)
        away_goalie_reserve = np.random.normal(self.away_team.bench.goalie.goalie_skill, self.away_team.bench.goalie.variance)

        return (
            {"offense": home_offense, "defense": home_defense, "ratio": home_ratio, "goalie": home_goalie, "bench_goalie": home_goalie_reserve},
            home_scorer_stats,
            {"offense": away_offense, "defense": away_defense, "ratio": away_ratio, "goalie": away_goalie, "bench_goalie": away_goalie_reserve},
            away_scorer_stats
        )


    def simulate_game(self):

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

        # Set the clock and simulate first half
        self.game_clock.set_time(REGULATION_TIME/2)
        self.simulate_half()

        ## HALFTIME, 
        # 1) flip the possessions from the coinflip
        if self.home_flip_winner:
            self.offense_stats = self.away_stats
            self.defense_stats = self.home_stats
        else:
            self.offense_stats = self.home_stats
            self.defense_stats = self.away_stats

        # 2) Tell the stat tracker that its the 2nd half now
        self.stat_tracker.halftime()


        # 3) Sub in backup goalies at the beginning of the 2nd half (take them back out later)
        #   - Put the starting goalie into the "bench goalie" position
        temp = self.home_stats["goalie"]
        self.home_stats["goalie"] = self.home_stats["bench_goalie"]
        self.home_stats["bench_goalie"] = temp

        temp = self.away_stats["goalie"]
        self.away_stats["goalie"] = self.away_stats["bench_goalie"]
        self.away_stats["bench_goalie"] = temp

        # Set the clock and simulate seond half
        self.game_clock.set_time(REGULATION_TIME/2)
        self.simulate_half(second_half=True)

        # Game is done! Retrieve information from objects
        self.postgame()


    def postgame(self):
        
        # Add win and loss to the correct teams' records
        if self.home_score > self.away_score:
            self.home_team.win()
            self.away_team.lose()
        elif self.away_score > self.home_score:
            self.home_team.lose()
            self.away_team.win()
        else: # In case of a tie
            if self.allow_tie:
                self.home_team.tie()
                self.away_team.tie()
            else:
                # TODO: Figure out what happens in the case of a tie
                raise NotImplementedError




        # TODO: Store the score tracker paragraph from the StatTracker
        # TODO: Save a descriptive one-liner about the game

        # Update player objects with final offensive stats
        self.home_team.update_offensive_stats(
            goals_scored=self.stat_tracker.home_goals, 
            shots_taken=self.stat_tracker.home_shots
        )
        self.away_team.update_offensive_stats(
            goals_scored=self.stat_tracker.away_goals,
            shots_taken=self.stat_tracker.away_shots
        )


    def simulate_half(self, second_half=False):
            swap_goalie = second_half
            
            while self.game_clock.time_left > 0:

                if swap_goalie and self.game_clock.time_left <= (REGULATION_TIME/2)+30:
                    # Switch the starting goalie back in around the 15 minute mark
                    temp = self.home_stats["goalie"]
                    self.home_stats["goalie"] = self.home_stats["bench_goalie"]
                    self.home_stats["bench_goalie"] = temp

                    temp = self.away_stats["goalie"]
                    self.away_stats["goalie"] = self.away_stats["bench_goalie"]
                    self.away_stats["bench_goalie"] = temp

                    # Finished swap, dont swap again
                    swap_goalie = False


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
                # we will always be going from 0 -> 40 for ball position.  Dont have one team go 40->0 (too complicated)
                if turnover_position:
                    self.ball_position = 40-turnover_position
                elif scored: 
                    self.ball_position = 20
                    self.game_clock.decrement(TIME_AFTER_SCORE)


    def offensive_posession(self):
        """
        Run the offensive posession until scored or turned over
        """
        turnover_position = False
        scored = False

        # Evaluate shots and passes until something happens
        while True:
            if self.prob_stack.pop() < 1 / (1 + np.e ^ (-0.3 * (self.ball_position-34))): # odds of taking a shot
                try: # Decrement 
                    self.game_clock.decrement(TIME_PER_SHOT)
                except:
                    # This shot will evaluate as normal, acting like a buzzer beater shot
                    pass
                
                # Take a shot with the stat tracker object
                scored, off_recovery, turnover = self.stat_tracker.take_shot(
                    self.prob_stack,
                    self.offense_stats,
                    self.defense_stats,
                    self.home_posession,
                    self.game_clock.time_left,
                )
                if turnover: 
                    # Put in info for where the turnover took place (don't track turnovers due to missed shots)
                    turnover_position = self.ball_position + (40 - self.ball_position)*self.prob_stack.pop()
                    break
                if scored:
                    break
                if off_recovery:
                    pass
            else:
                # Pass the ball
                if np.random.uniform(0,1) < self.offense_stats["ratio"]:
                    ball_position += min(40-self.ball_position, np.random.normal(4, 1.5)) # normal pass completed and advanced
                    try: # Decrement game clock
                        self.game_clock.decrement(TIME_PER_PASS)
                    except:
                        # Time has run out, immediately take a buzzer beater shot
                        scored, _, _ = self.stat_tracker.take_shot(
                            self.prob_stack,
                            self.offense_stats,
                            self.defense_stats,
                            self.home_posession,
                            self.game_clock.time_left,
                        )
                        break

                else:
                    turnover_position = self.ball_position + min(40-self.ball_position, np.random.normal(4, 1.5))*self.prob_stack.pop()
                    # Record passing turnover and break out of loop
                    if self.home_posession:
                        self.stat_tracker.home_turnovers += 1
                    else:
                        self.stat_tracker.away_turnovers += 1
                    try: # Decrement 
                        self.game_clock.decrement(TIME_PER_PASS)
                    except:
                        # Cannot take buzzer beater due to turnover
                        pass
                    
                    break

        return scored, turnover_position

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
class GameClock():
    def __init__(self):
        self.time_left = 0

    def set_time(self, time):
        self.time_left = time
    
    def decrement(self, amount):
        self.time_left = max(0, self.time_left-amount)
        10/self.time_left  # This will cause the game clock to raise an error when it reaches 0

    def time_to_str(seconds):
        """ Convert seconds (int) to mm:ss format (str)"""
        mm = seconds // 60
        ss = seconds % 60
        return f"{mm:02d}:{ss:02d}"




class StatTracker():
    """
    Keeps track of players stats throughout the match
    """
    def __init__(self, home_team, home_scorer_stats, away_team, away_scorer_stats):

        # Scoring Updates
        self.scoring_tracker = []

        # what half it is
        self.first_half = True

        ## SET UP HOME TEAM INFO
        self.home_team_name = home_team.name
        self.home_scorers = list(chain(
            home_team.starters.forwards, # 3 players
            home_team.starters.midfielders, # 3 players
            home_team.bench.forwards, # 2 players
            home_team.bench.midfielders, # 2 players
            ))
        
        # Weight by the minutes played and overall contribution to the offense
        self.home_scorers_likelihood = np.array([45, 45, 45, 45, 45, 45, 22.5, 22.5, 22.5, 22.5]) * np.array(home_scorer_stats)
        self.home_scorers_likelihood = self.home_scorers_likelihood / sum(self.home_scorers_likelihood)
        self.home_scorers_stats = home_scorer_stats

        self.home_goals = np.array([0]*10)
        self.home_shots = np.array([0]*10)

        self.home_off_recov = 0
        self.home_turnovers = 0


        ## SET UP AWAY TEAM INFO
        self.away_team_name = away_team.name
        self.away_scorers = list(chain(
            away_team.starters.forwards, # 3 players
            away_team.starters.midfielders, # 3 players
            away_team.bench.forwards, # 2 players
            away_team.bench.midfielders, # 2 players
            ))
        
        # Weight by the minutes played and overall contribution to the offense
        self.away_scorers_likelihood = np.array([45, 45, 45, 45, 45, 45, 22.5, 22.5, 22.5, 22.5]) * np.array(away_scorer_stats)
        self.away_scorers_likelihood = self.away_scorers_likelihood / sum(self.away_scorers_likelihood)
        self.away_scorers_stats = away_scorer_stats

        self.away_goals = np.array([0]*10)
        self.away_shots = np.array([0]*10)

        self.away_off_recov = 0
        self.away_turnovers = 0

    def halftime(self):
        """ Update information """
        self.first_half = False
        self.scoring_tracker.append("--- HALFTIME ---")

    def get_score_info(self):
        info = [f"{self.away_team_name} @ {self.home_team_name}\n", "--- START OF REGULATION ---"]
        info.extend(self.scoring_tracker)
        info.extend("--- END OF REGULATION ---")
        return "\n".join(info)


    def take_shot(self, prob_stack, offense_stats, defense_stats, home_posession, time_left):
        """ Handles the shot taking mechanics and records relevant information """
        scored, off_recovery, turnover = False, False, False

        # Which team has posession?
        if home_posession:
            scorers = self.home_scorers
            likelihood = self.home_scorers_likelihood
            scorers_stats = self.home_scorers_stats
            goals = self.home_goals
            shots = self.home_shots
            off_recov = self.home_off_recov
            team_name = self.home_team_name
        else:
            scorers = self.away_scorers
            likelihood = self.away_scorers_likelihood
            scorers_stats = self.away_scorers_stats
            goals = self.away_goals
            shots = self.away_shots
            off_recov = self.away_off_recov
            team_name = self.away_team_name

        # Who shot the ball
        idx = np.random.choice(np.arange(len(scorers)), likelihood)
        shots[idx] += 1

        if prob_stack.pop() < scorers_stats[idx].offense * np.e ^ (-K * (40-self.ball_position)): # If shot was taken, was it on goal?
            # Shot taken was on goal
            # Evaluate the result of the shot (weight the offense of the scorer more)
            if prob_stack.pop() < (0.5*offense_stats["offense"] + 1.25*scorers_stats[idx].offense)/ (offense_stats["offense"] + defense_stats["defense"] + defense_stats["goalie"]):
                scored = True
                goals[idx] += 1
                self.scoring_tracker.append(
                    f"{team_name}: {scorers[idx].name} scores with {GameClock.time_to_str(time_left)} in the {'1st' if self.first_half else '2nd'} half!"
                )
            elif prob_stack.pop() < 0.1: 
                off_recovery = True
            else:
                turnover = True

        elif prob_stack.pop() < 0.1:
            off_recovery = True
            off_recov += 1
        else: 
            turnover = True

        return scored, off_recovery, turnover
    


def odds_of_taking_shot(yard):
    return 1 / (1 + np.e ^ (-0.3 * (yard-34)))
    

def generate_game_analysis(home_team, visiting_team, individual_performances, results_dump):
    
    raise NotImplementedError