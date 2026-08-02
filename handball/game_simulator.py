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

from handball.utils import ProbabilityStack
from handball.domain import Team
from handball.simulation_vars import (
    REGULATION_TIME, K, TIME_PER_PASS, TIME_PER_SHOT, MAIN_STAT, SECONDARY_STAT, MIDDIE_STATS,
    TIME_AFTER_SCORE, STARTER_MINUTES, BENCH_MINUTES, BACKUP_GOALIE_MINUTES,
    COURT_LENGTH, INBOUND_POSITION, GOALIE_MULTIPLIER,
    SHOT_SIGMOID_STEEPNESS, SHOT_SIGMOID_MIDPOINT,
    PASS_COMPLETION_BASE, PASS_COMPLETION_GAIN, PASS_COMPLETION_MIN, PASS_COMPLETION_MAX,
    ON_GOAL_MAX, SHOOTER_SKILL_REF, SHOOTER_SKILL_SLOPE, SHOOTER_SKILL_MIN, SHOOTER_SKILL_MAX,
    GOAL_CONVERSION_BASE, GOAL_CONVERSION_GAIN, GOAL_CONVERSION_REF,
    GOAL_TEAM_WEIGHT, GOAL_SHOOTER_WEIGHT, OFFENSIVE_REBOUND_CHANCE,
    )

# Overtime is sudden death and burns no clock, so it terminates only probabilistically.
# This caps it in the (astronomically unlikely) event neither side ever scores.
MAX_OVERTIME_POSSESSIONS = 1000

# Indices into StatTracker's per-keeper goalie tallies.
STARTER, BACKUP = 0, 1


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _logit(p):
    return np.log(p / (1 - p))


def _remap(base, gain, edge, lo, hi):
    """
    Shift a baseline probability by `gain * edge` in log-odds space, then clamp.

    `base` sets the level (what an evenly matched pairing sees) and `gain` sets the
    slope (how much an advantage is worth). Keeping the two separable is what lets
    scoring level and competitive balance be tuned independently.
    """
    return float(np.clip(_sigmoid(_logit(base) + gain * edge), lo, hi))


class GameSimulator():
    def __init__(self, home_team:Team, away_team:Team, allow_tie=False):
        self.home_team = home_team
        self.away_team = away_team
        self.allow_tie = allow_tie

        self.home_score = 0
        self.away_score = 0

        self.home_stats, home_scorer_stats, self.away_stats, away_scorer_stats = self.init_stats()

        self.ball_position = INBOUND_POSITION
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

            ## Calculate offense stats for each player (weighted by position)
            # Sample stats for each player. An injured player contributes nothing
            # (multiplied by 0); the normal is still drawn so RNG draw counts --
            # and seeded reproducibility -- are unaffected by who is injured.
            start_forward_offense_raw = [np.random.normal(forward.offense, forward.variance) * (not forward.is_injured) for forward in team_obj.starters["Forward"]]
            start_middie_offense_raw = [np.random.normal(middie.offense, middie.variance) * (not middie.is_injured) for middie in team_obj.starters["Midfielder"]]
            start_defense_offense_raw = [np.random.normal(defense.offense, defense.variance) * (not defense.is_injured) for defense in team_obj.starters["Defense"]]
            bench_forward_offense_raw = [np.random.normal(forward.offense, forward.variance) * (not forward.is_injured) for forward in team_obj.bench["Forward"]]
            bench_middie_offense_raw = [np.random.normal(middie.offense, middie.variance) * (not middie.is_injured) for middie in team_obj.bench["Midfielder"]]
            bench_defense_offense_raw = [np.random.normal(defense.offense, defense.variance) * (not defense.is_injured) for defense in team_obj.bench["Defense"]]

            # Weight each stat by position importance and multiply by minutes played
            start_forward_offense = [stat * MAIN_STAT for stat in start_forward_offense_raw]
            start_middie_offense = [stat * MIDDIE_STATS for stat in start_middie_offense_raw]
            start_defense_offense = [stat * SECONDARY_STAT for stat in start_defense_offense_raw]
            bench_forward_offense = [stat * MAIN_STAT for stat in bench_forward_offense_raw]
            bench_middie_offense = [stat * MIDDIE_STATS for stat in bench_middie_offense_raw]
            bench_defense_offense = [stat * SECONDARY_STAT for stat in bench_defense_offense_raw]

            # Get the Player objects for scorers (forwards and midfielders only)
            # StatTracker needs Player objects to access .offense attribute
            scorer_players = (list(team_obj.starters["Forward"]) + list(team_obj.starters["Midfielder"]) + 
                            list(team_obj.bench["Forward"]) + list(team_obj.bench["Midfielder"]))
            
            # Get the sampled stats for scorers (for weighting likelihood)
            scorer_stats = start_forward_offense_raw + start_middie_offense_raw + bench_forward_offense_raw + bench_middie_offense_raw

            # Combine all weighted offensive stats into a single list (one entry per player)
            all_offense_stats = (start_forward_offense + start_middie_offense + start_defense_offense + 
                               bench_forward_offense + bench_middie_offense + bench_defense_offense)
            
            # Calculate total offense by multiplying each player's weighted stat by their minutes
            offense = sum(np.array(all_offense_stats) * (np.array(minutes_list) / 60))


            ## Calculate defense stats for each player (weighted by position)
            # Sample stats for each player (injured players contribute nothing).
            start_forward_defense_raw = [np.random.normal(forward.defense, forward.variance) * (not forward.is_injured) for forward in team_obj.starters["Forward"]]
            start_middie_defense_raw = [np.random.normal(middie.defense, middie.variance) * (not middie.is_injured) for middie in team_obj.starters["Midfielder"]]
            start_defense_defense_raw = [np.random.normal(defense.defense, defense.variance) * (not defense.is_injured) for defense in team_obj.starters["Defense"]]
            bench_forward_defense_raw = [np.random.normal(forward.defense, forward.variance) * (not forward.is_injured) for forward in team_obj.bench["Forward"]]
            bench_middie_defense_raw = [np.random.normal(middie.defense, middie.variance) * (not middie.is_injured) for middie in team_obj.bench["Midfielder"]]
            bench_defense_defense_raw = [np.random.normal(defense.defense, defense.variance) * (not defense.is_injured) for defense in team_obj.bench["Defense"]]

            # Weight each stat by position importance
            start_forward_defense = [stat * SECONDARY_STAT for stat in start_forward_defense_raw]
            start_middie_defense = [stat * MIDDIE_STATS for stat in start_middie_defense_raw]
            start_defense_defense = [stat * MAIN_STAT for stat in start_defense_defense_raw]
            bench_forward_defense = [stat * SECONDARY_STAT for stat in bench_forward_defense_raw]
            bench_middie_defense = [stat * MIDDIE_STATS for stat in bench_middie_defense_raw]
            bench_defense_defense = [stat * MAIN_STAT for stat in bench_defense_defense_raw]

            # Combine all weighted defensive stats into a single list (one entry per player)
            all_defense_stats = (start_forward_defense + start_middie_defense + start_defense_defense + 
                               bench_forward_defense + bench_middie_defense + bench_defense_defense)
            
            # Calculate total defense by multiplying each player's weighted stat by their minutes
            defense = sum(np.array(all_defense_stats) * (np.array(minutes_list) / 60))


            ## Calculate goalies' stats (an injured goalie contributes nothing)
            start_goalie = team_obj.starters["Goalie"][0]
            bench_goalie = team_obj.bench["Goalie"][0]
            goalie = np.random.normal(start_goalie.goalie_skill, start_goalie.variance) * GOALIE_MULTIPLIER * (not start_goalie.is_injured)
            goalie_reserve = np.random.normal(bench_goalie.goalie_skill, bench_goalie.variance) * GOALIE_MULTIPLIER * (not bench_goalie.is_injured)

            # Build combined performance list (15 non-goalies + 2 goalies = 17 players)
            # Goalies don't contribute to offense/defense, so their offense contribution is 0
            combined_list_off = all_offense_stats + [0, 0]
            combined_list_def = all_defense_stats + [goalie, goalie_reserve]
            combined_list = np.array(combined_list_off) + np.array(combined_list_def)

            team_obj.update_performances(combined_list)

            # Return Player objects for scorers (StatTracker needs to access .offense attribute)
            return offense, defense, goalie, goalie_reserve, scorer_players

        home_offense, home_defense, home_goalie, home_goalie_reserve, home_scorer_stats = calculate_stats(self.home_team)
        away_offense, away_defense, away_goalie, away_goalie_reserve, away_scorer_stats = calculate_stats(self.away_team)

        # Pass completion. The raw ratio is ~0.5 for every real matchup -- using it
        # directly would make every pass a coin flip and produce hundreds of turnovers
        # a game -- so it is remapped onto a realistic band around PASS_COMPLETION_BASE.
        # The input is a ratio, so league-wide rating inflation leaves this untouched.
        home_completion = pass_completion(home_offense, away_defense)
        away_completion = pass_completion(away_offense, home_defense)


        return (
            {"offense": home_offense, "defense": home_defense, "pass_completion": home_completion, "goalie": home_goalie, "bench_goalie": home_goalie_reserve},
            home_scorer_stats,
            {"offense": away_offense, "defense": away_defense, "pass_completion": away_completion, "goalie": away_goalie, "bench_goalie": away_goalie_reserve},
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

        self.stat_tracker.set_keeper_in_net(BACKUP)

        # Set the clock and simulate seond half
        self.game_clock.set_time(REGULATION_TIME/2)
        self.simulate_half(second_half=True)

        # OVERTIME: Sudden death if tied and ties not allowed
        if self.home_score == self.away_score and not self.allow_tie:
            self._simulate_overtime()

        # Game is done! Retrieve information from objects
        self.postgame()

    def _simulate_overtime(self):
        """
        Sudden death overtime: first team to score wins.
        Alternating possessions until someone scores.
        """
        self.stat_tracker.start_overtime()

        # Coin flip for first OT possession
        if self.prob_stack.pop() <= 0.5:
            self.home_posession = True
            self.offense_stats = self.home_stats
            self.defense_stats = self.away_stats
        else:
            self.home_posession = False
            self.offense_stats = self.away_stats
            self.defense_stats = self.home_stats

        # Reset ball position
        self.ball_position = INBOUND_POSITION

        # Keep playing until someone scores
        possessions = 0
        while self.home_score == self.away_score and possessions < MAX_OVERTIME_POSSESSIONS:
            possessions += 1
            scored, turnover_position = self.offensive_posession_overtime()

            if scored:
                if self.home_posession:
                    self.home_score += 1
                else:
                    self.away_score += 1
                break

            # Change possession
            self.home_posession = not self.home_posession
            temp = self.offense_stats
            self.offense_stats = self.defense_stats
            self.defense_stats = temp

            # Set ball position after turnover
            if turnover_position is not None:
                self.ball_position = COURT_LENGTH - turnover_position
            else:
                self.ball_position = INBOUND_POSITION

    def offensive_posession_overtime(self):
        """
        Overtime possession - no clock management, just play until scored or turnover.
        """
        turnover_position = None
        scored = False

        while True:
            if self.prob_stack.pop() < odds_of_taking_shot(self.ball_position):
                # Take a shot
                scored, off_recovery, turnover = self.stat_tracker.take_shot(
                    ball_position=self.ball_position,
                    prob_stack=self.prob_stack,
                    offense_stats=self.offense_stats,
                    defense_stats=self.defense_stats,
                    home_posession=self.home_posession,
                    time_left=0,  # OT has no clock display
                )
                if turnover:
                    turnover_position = self.ball_position + (COURT_LENGTH - self.ball_position) * self.prob_stack.pop()
                    break
                if scored:
                    break
                if off_recovery:
                    pass
            else:
                # Pass the ball
                if np.random.uniform(0, 1) < self.offense_stats["pass_completion"]:
                    self.ball_position += min(COURT_LENGTH - self.ball_position, np.random.normal(4, 1.5))
                else:
                    turnover_position = self.ball_position + min(COURT_LENGTH - self.ball_position, np.random.normal(4, 1.5)) * self.prob_stack.pop()
                    if self.home_posession:
                        self.stat_tracker.home_turnovers += 1
                    else:
                        self.stat_tracker.away_turnovers += 1
                    break

        return scored, turnover_position


    def simulate_half(self, second_half=False):
            swap_goalie = second_half
            
            while self.game_clock.time_left > 0:

                if swap_goalie and self.game_clock.time_left <= (REGULATION_TIME/2) - BACKUP_GOALIE_MINUTES*60:
                    # Switch the starting goalie back in at the 15-minutes-remaining mark.
                    # (Comparing against REGULATION_TIME/2 directly would fire on the very
                    # first iteration, which is why the backup used to never play at all.)
                    temp = self.home_stats["goalie"]
                    self.home_stats["goalie"] = self.home_stats["bench_goalie"]
                    self.home_stats["bench_goalie"] = temp

                    temp = self.away_stats["goalie"]
                    self.away_stats["goalie"] = self.away_stats["bench_goalie"]
                    self.away_stats["bench_goalie"] = temp

                    self.stat_tracker.set_keeper_in_net(STARTER)

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
                temp = self.offense_stats
                self.offense_stats = self.defense_stats
                self.defense_stats = temp

                # Set the position of the ball (if turnover, put at specific location. else, put at the end of the court for an inbound)
                # we will always be going from 0 -> 40 for ball position.  Dont have one team go 40->0 (too complicated)
                if turnover_position is not None:
                    self.ball_position = COURT_LENGTH-turnover_position
                elif scored:
                    self.ball_position = INBOUND_POSITION
                    self.game_clock.decrement(TIME_AFTER_SCORE)



    def offensive_posession(self):
        """
        Run the offensive posession until scored or turned over
        """
        turnover_position = None
        scored = False

        # Evaluate shots and passes until something happens
        while True:
            if self.prob_stack.pop() < odds_of_taking_shot(self.ball_position): # odds of taking a shot
                # If the clock expires here the shot still resolves, as a buzzer beater
                self.game_clock.decrement(TIME_PER_SHOT)

                # Take a shot with the stat tracker object
                scored, off_recovery, turnover = self.stat_tracker.take_shot(
                    ball_position=self.ball_position,
                    prob_stack=self.prob_stack,
                    offense_stats=self.offense_stats,
                    defense_stats=self.defense_stats,
                    home_posession=self.home_posession,
                    time_left=self.game_clock.time_left,
                )
                if turnover:
                    # Put in info for where the turnover took place (don't track turnovers due to missed shots)
                    turnover_position = self.ball_position + (COURT_LENGTH - self.ball_position)*self.prob_stack.pop()
                    break
                if scored:
                    break
                if off_recovery:
                    pass
            else:
                # Pass the ball
                if np.random.uniform(0,1) < self.offense_stats["pass_completion"]: # type: ignore
                    self.ball_position += min(COURT_LENGTH-self.ball_position, np.random.normal(4, 1.5)) # normal pass completed and advanced
                    if not self.game_clock.decrement(TIME_PER_PASS):
                        # Time has run out, immediately take a buzzer beater shot
                        scored, _, _ = self.stat_tracker.take_shot(
                            ball_position=self.ball_position,
                            prob_stack=self.prob_stack,
                            offense_stats=self.offense_stats,
                            defense_stats=self.defense_stats,
                            home_posession=self.home_posession,
                            time_left=self.game_clock.time_left,
                        )
                        break

                else:
                    turnover_position = self.ball_position + min(COURT_LENGTH-self.ball_position, np.random.normal(4, 1.5))*self.prob_stack.pop()
                    # Record passing turnover and break out of loop
                    if self.home_posession:
                        self.stat_tracker.home_turnovers += 1
                    else:
                        self.stat_tracker.away_turnovers += 1
                    # Cannot take a buzzer beater after a turnover, so the clock result is ignored
                    self.game_clock.decrement(TIME_PER_PASS)

                    break

        return scored, turnover_position
    
    def postgame(self):
        # Add win and loss to the correct teams' records
        if self.home_score > self.away_score:
            self.home_team.record_result("W")
            self.away_team.record_result("L")
        elif self.away_score > self.home_score:
            self.home_team.record_result("L")
            self.away_team.record_result("W")
        else:  # In case of a tie (only possible if allow_tie=True)
            self.home_team.record_result("T")
            self.away_team.record_result("T")

        # Update player objects with final offensive stats
        self.home_team.update_offensive_stats(
            goals_scored=self.stat_tracker.home_goals,
            shots_taken=self.stat_tracker.home_shots
        )
        self.away_team.update_offensive_stats(
            goals_scored=self.stat_tracker.away_goals,
            shots_taken=self.stat_tracker.away_shots
        )

        # Update goalie stats. Each keeper is credited with exactly the shots they
        # faced, so a backup who was beaten repeatedly in his 15 minutes owns that.
        self.home_team.update_goalie_stats(
            saves_by_keeper=self.stat_tracker.home_goalie_saves_by_keeper,
            goals_allowed_by_keeper=self.stat_tracker.home_goalie_goals_allowed_by_keeper,
        )
        self.away_team.update_goalie_stats(
            saves_by_keeper=self.stat_tracker.away_goalie_saves_by_keeper,
            goals_allowed_by_keeper=self.stat_tracker.away_goalie_goals_allowed_by_keeper,
        )

        # Build game summary for RecordKeeper
        self.game_summary = {
            "home_team": self.home_team.id,
            "away_team": self.away_team.id,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "went_to_overtime": self.stat_tracker.in_overtime,
            "scoring_log": self.stat_tracker.get_score_info(),
            "home_goals_by_player": {
                self.stat_tracker.home_scorers[i].name: int(self.stat_tracker.home_goals[i])
                for i in range(len(self.stat_tracker.home_scorers))
                if self.stat_tracker.home_goals[i] > 0
            },
            "away_goals_by_player": {
                self.stat_tracker.away_scorers[i].name: int(self.stat_tracker.away_goals[i])
                for i in range(len(self.stat_tracker.away_scorers))
                if self.stat_tracker.away_goals[i] > 0
            },
            # Shots keyed by every player who attempted at least one shot, so
            # RecordKeeper can build complete per-player game lines (including
            # players who shot but did not score).
            "home_shots_by_player": {
                self.stat_tracker.home_scorers[i].name: int(self.stat_tracker.home_shots[i])
                for i in range(len(self.stat_tracker.home_scorers))
                if self.stat_tracker.home_shots[i] > 0
            },
            "away_shots_by_player": {
                self.stat_tracker.away_scorers[i].name: int(self.stat_tracker.away_shots[i])
                for i in range(len(self.stat_tracker.away_scorers))
                if self.stat_tracker.away_shots[i] > 0
            },
            "home_goalie_saves": self.stat_tracker.home_goalie_saves,
            "away_goalie_saves": self.stat_tracker.away_goalie_saves,
        }

    def get_game_summary(self):
        """Return the game summary dict for RecordKeeper integration."""
        return getattr(self, 'game_summary', None)

class GameClock():
    def __init__(self):
        self.time_left = 0

    def set_time(self, time):
        self.time_left = time
    
    def decrement(self, amount):
        """Run the clock down. Returns False once time has expired."""
        self.time_left = max(0, self.time_left-amount)
        return self.time_left > 0

    @staticmethod
    def time_to_str(seconds):
        """ Convert seconds (int) to mm:ss format (str)"""
        seconds = int(seconds)  # Cast to int in case of float
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
        self.in_overtime = False

        ## SET UP HOME TEAM INFO
        self.home_team_name = home_team.id
        self.home_scorers = list(chain(
            home_team.starters["Forward"], # 3 players
            home_team.starters["Midfielder"], # 3 players
            home_team.bench["Forward"], # 2 players
            home_team.bench["Midfielder"], # 2 players
            ))

        # Weight by the minutes played and overall contribution to the offense.
        # Use the offense values from the Player objects on the team, so this
        # works whether the caller passes in Player objects or simple ratings.
        home_scorer_offense_values = np.array([player.offense for player in self.home_scorers])
        self.home_scorers_likelihood = np.array([45, 45, 45, 45, 45, 45, 22.5, 22.5, 22.5, 22.5]) * home_scorer_offense_values
        self.home_scorers_likelihood = self.home_scorers_likelihood / sum(self.home_scorers_likelihood)

        self.home_goals = np.array([0]*10)
        self.home_shots = np.array([0]*10)

        self.home_off_recov = 0
        self.home_turnovers = 0

        # Goalie stats for home team, split by which keeper was actually in net when
        # the shot arrived. STARTER / BACKUP index into these; the team totals are the
        # home_goalie_saves / home_goalie_goals_allowed properties below.
        self.home_goalie_saves_by_keeper = [0, 0]
        self.home_goalie_goals_allowed_by_keeper = [0, 0]


        ## SET UP AWAY TEAM INFO
        self.away_team_name = away_team.id
        self.away_scorers = list(chain(
            away_team.starters["Forward"], # 3 players
            away_team.starters["Midfielder"], # 3 players
            away_team.bench["Forward"], # 2 players
            away_team.bench["Midfielder"], # 2 players
            ))

        # Weight by the minutes played and overall contribution to the offense.
        # Use the offense values from the Player objects on the team.
        away_scorer_offense_values = np.array([player.offense for player in self.away_scorers])
        self.away_scorers_likelihood = np.array([45, 45, 45, 45, 45, 45, 22.5, 22.5, 22.5, 22.5]) * away_scorer_offense_values
        self.away_scorers_likelihood = self.away_scorers_likelihood / sum(self.away_scorers_likelihood)

        self.away_goals = np.array([0]*10)
        self.away_shots = np.array([0]*10)

        self.away_off_recov = 0
        self.away_turnovers = 0

        # Goalie stats for away team (see the home comment above)
        self.away_goalie_saves_by_keeper = [0, 0]
        self.away_goalie_goals_allowed_by_keeper = [0, 0]

        # Which keeper is between the posts right now. Both teams swap at the same
        # moments -- halftime, then back at the BACKUP_GOALIE_MINUTES mark -- so a
        # single index covers the whole game.
        self.keeper_in_net = STARTER

    @property
    def home_goalie_saves(self):
        """Saves made by the home team's keepers, both combined."""
        return sum(self.home_goalie_saves_by_keeper)

    @property
    def home_goalie_goals_allowed(self):
        return sum(self.home_goalie_goals_allowed_by_keeper)

    @property
    def away_goalie_saves(self):
        return sum(self.away_goalie_saves_by_keeper)

    @property
    def away_goalie_goals_allowed(self):
        return sum(self.away_goalie_goals_allowed_by_keeper)

    def set_keeper_in_net(self, keeper):
        """Record a goalie change so subsequent shots are credited to the right keeper."""
        self.keeper_in_net = keeper

    def halftime(self):
        """ Update information """
        self.first_half = False
        self.scoring_tracker.append("--- HALFTIME ---")

    def start_overtime(self):
        """ Mark the start of overtime """
        self.in_overtime = True
        self.scoring_tracker.append("--- OVERTIME (SUDDEN DEATH) ---")

    def get_score_info(self):
        info = [f"{self.away_team_name} @ {self.home_team_name}\n", "--- START OF REGULATION ---"]
        info.extend(self.scoring_tracker)
        info.extend("--- END OF REGULATION ---")
        return "\n".join(info)


    def take_shot(self, ball_position, prob_stack, offense_stats, defense_stats, home_posession, time_left):
        """ Handles the shot taking mechanics and records relevant information """
        scored, off_recovery, turnover = False, False, False

        # Which team has posession?
        if home_posession:
            scorers = self.home_scorers
            likelihood = self.home_scorers_likelihood
            goals = self.home_goals
            shots = self.home_shots
            team_name = self.home_team_name
            # The away team is defending, so their keeper faces this shot
            keeper_saves = self.away_goalie_saves_by_keeper
            keeper_allowed = self.away_goalie_goals_allowed_by_keeper
        else:
            scorers = self.away_scorers
            likelihood = self.away_scorers_likelihood
            goals = self.away_goals
            shots = self.away_shots
            team_name = self.away_team_name
            keeper_saves = self.home_goalie_saves_by_keeper
            keeper_allowed = self.home_goalie_goals_allowed_by_keeper

        # Who shot the ball
        idx = np.random.choice(np.arange(len(scorers)), p=likelihood)
        shots[idx] += 1

        if prob_stack.pop() < on_goal_probability(scorers[idx].offense, ball_position): # If shot was taken, was it on goal?
            # Shot taken was on goal
            # Evaluate the result of the shot (weight the offense of the scorer more)
            if prob_stack.pop() < goal_probability(offense_stats, defense_stats, scorers[idx].offense):
                scored = True
                goals[idx] += 1
                # Charge the goal to whichever keeper was actually in net
                keeper_allowed[self.keeper_in_net] += 1

                # Determine period label for scoring tracker
                if self.in_overtime:
                    period_label = "OT"
                elif self.first_half:
                    period_label = "1st half"
                else:
                    period_label = "2nd half"

                self.scoring_tracker.append(
                    f"{team_name}: {scorers[idx].name} scores with {GameClock.time_to_str(time_left)} in the {period_label}!"
                )
            elif prob_stack.pop() < OFFENSIVE_REBOUND_CHANCE:
                off_recovery = True
                self._credit_rebound(home_posession)
            else:
                # Shot on goal was saved by whichever keeper was actually in net
                keeper_saves[self.keeper_in_net] += 1
                turnover = True

        elif prob_stack.pop() < OFFENSIVE_REBOUND_CHANCE:
            off_recovery = True
            self._credit_rebound(home_posession)
        else:
            turnover = True

        return scored, off_recovery, turnover

    def _credit_rebound(self, home_posession):
        """Record an offensive rebound against the team that has the ball."""
        if home_posession:
            self.home_off_recov += 1
        else:
            self.away_off_recov += 1
    


def odds_of_taking_shot(yard):
    """Probability a player shoots rather than passes, given the ball's position."""
    return _sigmoid(SHOT_SIGMOID_STEEPNESS * (yard - SHOT_SIGMOID_MIDPOINT))


def pass_completion(offense, opposing_defense):
    """
    Probability a pass is completed rather than turned over.

    `offense / (offense + opposing_defense)` sits at ~0.5 for every realistic matchup,
    so it is used only as an *edge* around parity and remapped onto a believable band.
    Because the input is a ratio it is scale-free: if every rating in the league drifts
    upward over seasons, completion rates -- and therefore scoring -- stay put.
    """
    edge = offense / (offense + opposing_defense) - 0.5
    return _remap(PASS_COMPLETION_BASE, PASS_COMPLETION_GAIN, edge,
                  PASS_COMPLETION_MIN, PASS_COMPLETION_MAX)


def on_goal_probability(shooter_offense, ball_position):
    """
    Probability a shot is on goal: distance decay, scaled by the shooter's skill.

    See game_mechanics.txt rule 6. The skill term is a modest multiplier around an
    average shooter rather than the raw 0-10 rating, which would exceed 1.0 near goal.
    """
    skill = float(np.clip(1 + SHOOTER_SKILL_SLOPE * (shooter_offense - SHOOTER_SKILL_REF),
                          SHOOTER_SKILL_MIN, SHOOTER_SKILL_MAX))
    return min(0.98, ON_GOAL_MAX * np.exp(-K * (COURT_LENGTH - ball_position)) * skill)


def goal_probability(offense_stats, defense_stats, shooter_offense):
    """
    Probability a shot on goal beats the keeper.

    The quality ratio weighs the attack (team plus a heavier weight on the shooter)
    against the defense and the goalie. Like pass completion it is remapped in log-odds
    space around the league average, so GOAL_CONVERSION_BASE sets how much scoring
    happens and GOAL_CONVERSION_GAIN sets how much being the better team is worth.
    """
    quality = (
        (GOAL_TEAM_WEIGHT * offense_stats["offense"] + GOAL_SHOOTER_WEIGHT * shooter_offense)
        / (offense_stats["offense"] + defense_stats["defense"] + defense_stats["goalie"])
    )
    return _remap(GOAL_CONVERSION_BASE, GOAL_CONVERSION_GAIN, quality - GOAL_CONVERSION_REF,
                  0.0, 1.0)
