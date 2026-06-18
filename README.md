# Handball
This github contains the main backend components to run the National Handball Association, a completely simulated sports league. 

## Functionality

### constants.py
Contains constants that specify interactions with the Google Sheet

### draft_simulator.py
Generates new draft-class players with randomly assigned stats (no name-based modeling, no rating tiers). Each prospect's overall skill is sampled directly from a normal distribution inside `Player.create_new_player` (mean `NEW_PLAYER_MEAN`=5, std `NEW_PLAYER_STD`=1.5, capped at `STAT_CAP`=10 in `simulation_vars.py`), then split into concrete offense/defense/goalie stats.
- `generate_draft_class`: Top-level entry point — generate a full draft class from a list of names (positions optional/random, seedable for reproducibility).
- `player_generation`: Generate players from parallel name/position lists.
- `create_draft_player`: Create a single new player.
- `assign_random_position`: Weighted random position selection.
- `load_prospect_names`: Load prospect names from a file (one per line, or a CSV with a `Name` column and optional `Position`).

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
`RecordKeeper`: Live, per-game store written during simulation. Holds the game summaries produced by `GameSimulator.get_game_summary()` plus per-player game-by-game lines.
- `add_game`: Record a finished game, returning a (auto-generated or supplied) `game_id`.
- `add_player_game_record`: Record a single player's line (goals/shots or saves/goals_allowed) for a game.
- `get_player_career_stats`: Aggregate a player's stats across every recorded game.
- `get_recent_games`: Return the most recent N games.
- `to_json` / `save_json` / `from_json`: Persist and reload the keeper as a single JSON blob.

`GameRecord`: One player's performance in one game (field-player and goalie stats).

`SeasonArchive`: Season-level aggregation layer. One JSON file per season, written by `OperationsHandler` at key milestones (post-draft, post-contracts, end of regular season, end of playoffs). Holds standings, season player stats, trades, injuries, draft picks, contracts, and the playoff bracket.

`PeriodReport` + `RecordKeeper.build_period_report`: Per-team, per-period report of team and individual performances (record, goals, per-player goals/shots/saves). Period results are samples of true ability and live only in these reports — they are never written back to the sheet as ratings.

### operations_handler.py
`OperationsHandler`: High-level season orchestrator tying the subsystems together.
- `create_season_schedule(year)`: Generate and persist a 55-week schedule (tags the season year).
- `run_period_simulation(period)`: Simulate one of five 11-week periods; records games, persists player season stats to team JSON, writes W-L-T standings to the sheet, emits period reports (including an Injury Report), rolls/recovers injuries, and (at period 5) archives the regular season (including injuries). Injured players are substituted next-man-up by position (starter hurt: bench→starter, reserve→bench, injured→reserves; bench hurt: reserve→bench), with the roster reclassification + an "Injured" tag written to the sheet. If there's no healthy reserve at the position, the player plays hurt. Injury durations tick down per game (season-agnostic); a major injury can damage development (young players' growth slows; older players' decline accelerates). On medical recovery the injured tag is cleared but the player stays in reserves — returning them to the lineup is a manual manager action.
- `restore_player(team, player)`: Manually return a recovered player to their pre-injury slot (a between-periods action), reversing the substitution and updating the sheet. Errors if the player hasn't recovered yet.
- `run_playoffs(year)`: Seed the top 8 per conference and run a single-elimination bracket (8→4→2 per conference, then a final). Games run on deep-copied teams so canonical records/stats are untouched; results go to a `PlayoffBracket` in the archive.
- `run_draft(names_path, year, rounds)`: Reverse-standings rookie draft. Prospect names come from a file (one per line, or a CSV with `Name`/optional `Position`). Records `DraftPick` + rookie `ContractRecord` entries to the archive and stores drafted players to a per-year draftees JSON.
- `execute_trade(package)` / `release_player(team, player)` / `available_free_agents(position)` / `sign_free_agent(team, player)`: User-specified free-agency and trade operations (no AI decision-making) wrapping `TradeHandler` and `FreeAgencyHandler`.
- `advance_season(year)`: Roll the league over — age every player (which resets their season log) and persist team JSON.
- `run_full_season(year, names_path)`: Convenience that runs the whole lifecycle: schedule → 5 periods → playoffs → draft → advance. FA/trade operations are performed by callers between phases.

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
- `pop`: Access a random number and remove it from the stack. If this function runs out of random numbers, it immediately regenerates itself.
