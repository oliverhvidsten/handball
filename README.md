# Handball
This github contains the main backend components to run the National Handball Association, a completely simulated sports league. 

## Functionality

### constants.py
Contains constants that specify interactions with the Google Sheet

### draft_simulator.py
To be implemented (limited existing functionality is outdated)

### game_simulator.py
`GameSimulator`: Class that holds all of the relevant data to simulate a single game with two teams (home and away)
- `init_stats`: This method will sample from a player's normal distribution to determine how they will play during a certain game.
- `simulate_game`: Simulates the game given the stats that were determined during object instatiation. Handles coin flip, stat updates, and halftime goalie substitutions.
- `simulate_half`: Called by `simulate_game`, simulates one half of play. Handles the scoring and possession flipping after turnovers. Advances the game clock after events such as a score.
- `offensive_posession`: Called by `simulate_half`, uses information regarding the current offense and defense to evaluate success/failure of passes and shots. Advances the game clock after passes and shots. Terminates upon turnover or score or clock expiration.
- `postgame`: Handles the game results and adds cumulative offensive stats (shots taken and goals) to the player objects of the offensive players.

`GameClock`: Holds the time left in the game and handles clock progression. Divides by zero when time expires to alleviate necessity of checking an if/else at every decrement.

`StatTracker`: Keeps track of players stats throughout the match (goals, shots taken, goalie saves(to be added)). Iteratively updates a scoring tracker that keeps a descriptive log of the scoring plays.
- `halftime`: Make halftime adjustments
- `get_score_info`: Get a log of scoring plays after game is finished
- `take_shot`: Determines which player took the shot and the outcome of the shot. Adds outcome to player's stats.

### players.py
`Player`: Class that holds a complete set of information about a player
- `create_new_player`: Create a new player that has never been in the league. Mostly useful for creating a draft class.
- `to_dict`: Saves player object as a python dictionary in order to prepare for the information to be saved in JSON format.
- `from_dict`: Loads player object from a dictionary representation.
- `advance_year`: Updates to player info due to new season (age, contract, etc)
- `injure`: Handles an injury happening to a player. Adds info to the log
- `total_season_goals`: Property lambda function. Returns total goals up and to this point in the season.

`Injury Report`: Holds player injury information (current and historical)
- Most methods are pretty straight forward here

### record_keeper.py
To be implemented

### sheets_handler.py
`SheetsHandler`: Class that interacts direcly with the Google Sheet
- `get_full_team_values`: Special case of `get_team_values` that accesses a pre-determined range held in `TEAM_RANGE` from `constants.py`
- `update_full_team_values`: Special case of `update_team_values` that accesses a pre-determined range held in `TEAM_RANGE` from `constants.py`
- `get_team_values`: Accesses all of the information from a specified range in the sheet for the specified team. Outputs a 2D array (format: arr[row,col])
- `update_team_values`: Updates all of the information from a specified range in the sheet for the specified team. Takes in a 2D array (format: arr[row,col])
- `get_draft_picks`: Special case of `get_team_values` that accesses a pre-determined range held in `DRAFT_PICKS_RANGE` from `constants.py`
-`update_draft_picks`: Special case of `update_team_values` that accesses a pre-determined range held in `DRAFT_PICKS_RANGE` from `constants.py`
- `get_player_notes`: Gets the notes from all cells within a specified range in the sheet for a specified team.
- `update_player_notes`: Writes the notes for all cells within a specified range in the sheet for a specified team. Notes are to be used to attach data that is helpful to know, but not necessary enough to clutter the Google Sheet.

### simulation_vars.py
Holds constants pertinent to the running of the simulation


### teams.py
`TeamInfo`: Lightweight class that is built from information contained in the Google Sheet. Ideally can be used to to minor superficial reformatting.
- `from_sheet`: Instantiates a `TeamInfo` object from information held in the specified team's google sheet.
- `update_sheet`: Writes information contained in the `TeamInfo` object back to the team's google sheet.

`Subroster`: Holds starter or bench players for a `Team` object.
- `from_TeamInfo`: constructor for this instance during the creation of a `Team` object from a `TeamInfo` object.
- `update_team_dict`: Saves this object into python dicionary format to be saved in JSON format.

`Team`: Heavier class that contains the full player objects. This class will be used to evaluate most team functionality -- namely the simulation of games. Created from a team's `TeamInfo` object and JSON file.
- `win`: Adds a win to the team's record
- `lose`: Adds a loss to the team's record
- `tie`: Adds a tie to the team's record
- `from_TeamInfo`: Instantiates a `Team` object from information held in a `TeamInfo` object. Also accesses the team's JSON file which contains player data.
- `update_team_dict`: Save relevant information to the team's JSON file.
- `update_performances`: Save a player's overall performance in a game to their season stats
- `update_offensive_stats`: Save a players offensive stats (goals, shots taken) in a game to their season stats. (Add functionality to save goalie metrics as well)


### utils.py
Holds useful functions

`dict_to_str`: Creates a string represetantion of a dictionary (for debugging)

`ProbabilityStack`: Holds randomly generated values between 0-1 to be used in the randomized parts of the simulation. Preallocates for speed. 
