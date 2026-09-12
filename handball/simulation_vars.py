"""
Name: simulation_vars.py
Description: Similar purpos to constants.py but reserved for game information
Author: Oliver Hvidsten
"""

# New-player overall skill is sampled directly from a normal distribution
# (no rating tiers), then capped at STAT_CAP.
NEW_PLAYER_MEAN = 5.0
NEW_PLAYER_STD = 1.5
STAT_CAP = 10.0

REGULATION_TIME = 60 * 60            # a 60-minute game, in seconds

STARTER_MINUTES = 45
BENCH_MINUTES = 22.5
BACKUP_GOALIE_MINUTES = 15           # the backup opens the 2nd half; the starter comes back
                                     # in at the 15-minutes-remaining mark

# Possession timings. A possession is ~4 passes up the court plus a shot, so these
# add up to ~31 seconds and give ~50 possessions per team per game.
TIME_PER_PASS = 6
TIME_PER_SHOT = 7
TIME_AFTER_SCORE = 15

MAIN_STAT = 3
SECONDARY_STAT = 1
MIDDIE_STATS = 2


# --- court ---------------------------------------------------------------
COURT_LENGTH = 40                    # meters; the ball always travels 0 -> 40
INBOUND_POSITION = 20                # where the ball starts, and restarts after a goal
GOALIE_MULTIPLIER = 4                # scales goalie_skill onto the team-defense scale


# --- shot selection ------------------------------------------------------
# Probability of shooting rather than passing, as a sigmoid of ball position.
SHOT_SIGMOID_STEEPNESS = 0.30
SHOT_SIGMOID_MIDPOINT = 34.0


# --- pass completion -----------------------------------------------------
# The raw offense/(offense+defense) ratio sits at ~0.5 for every real matchup, which
# would make every pass a coin flip. Instead it is remapped through a logistic:
# BASE sets the level, GAIN sets how strongly an offense/defense edge moves it.
PASS_COMPLETION_BASE = 0.94
PASS_COMPLETION_GAIN = 5.0
PASS_COMPLETION_MIN = 0.78
PASS_COMPLETION_MAX = 0.985


# --- shot on goal --------------------------------------------------------
ON_GOAL_MAX = 0.85                   # on-goal rate at the goal mouth for an average shooter
K = 0.06                             # decay of on-goal odds with distance from goal
SHOOTER_SKILL_REF = 5.0              # the offense rating that yields a 1.0 multiplier
SHOOTER_SKILL_SLOPE = 0.06
SHOOTER_SKILL_MIN = 0.30
SHOOTER_SKILL_MAX = 1.60


# --- goal conversion -----------------------------------------------------
# Same logistic treatment: the quality ratio is scale-invariant, so BASE holds the
# league's scoring level steady even as player ratings inflate over seasons.
GOAL_CONVERSION_BASE = 0.50          # conversion for a league-average attack
GOAL_CONVERSION_GAIN = 5.0           # how much a quality edge matters
GOAL_CONVERSION_REF = 0.266          # league-average quality ratio; scripts/calibrate_sim.py
                                     # prints the observed value so this can be re-centred
GOAL_TEAM_WEIGHT = 0.5
GOAL_SHOOTER_WEIGHT = 1.25

OFFENSIVE_REBOUND_CHANCE = 0.10


# overall season structure
GAMES_IN_SEASON = 50


# player info
MINOR_INJURIES = [
    "Finger (Sprain)", "Knee (Strain)", "Ankle (Sprain)"
    ]
MODERATE_INJURIES = [
    "Finger (Minor Fracture)", "Knee (Sprain)", "Shoulder (Sprain)"
    ]
MAJOR_INJURIES = [
    "Finger (Major Fracture)", "ACL (Tear)", "MCL (Tear)"
    ]


def injury_severity(injury_type):
    """Classify an injury type as 'minor', 'moderate', 'major', or 'unknown'."""
    if injury_type in MAJOR_INJURIES:
        return "major"
    if injury_type in MODERATE_INJURIES:
        return "moderate"
    if injury_type in MINOR_INJURIES:
        return "minor"
    return "unknown"


# Injuries are rolled and applied at the END of each season chunk (1/5 of the
# season; see season.PERIODS). Duration is measured in CHUNKS, not games, and a
# player sits out that many subsequent chunks before recovering. Unknown-severity
# injuries fall back to one chunk.
INJURY_CHUNK_DURATION = {"minor": 1, "moderate": 2, "major": 3, "unknown": 1}

# A player's per-game injury_risk is rolled once per chunk; scale it up so the
# season-long injury rate stays in the same ballpark as the old per-game model.
INJURY_CHUNK_RISK_SCALE = 5


# Major injuries can damage a player's development.
MAJOR_INJURY_IMPACT_CHANCE = 0.5   # chance a major injury affects trajectory
INJURY_GROWTH_PENALTY = 0.9          # young players: multiply max stats (slows growth)
INJURY_DECLINE_MULTIPLIER = 1.3      # older players: multiply decline_rate
MAX_DECLINE_RATE = 0.5               # cap on decline_rate


# League table. Points decide the standings, the playoff seeding, and (reversed) the
# draft order -- see handball/standings.py for the full tiebreak chain.
POINTS_PER_WIN = 3
POINTS_PER_TIE = 1

# Playoffs: each conference sends its division winners (seeds 1..N) plus the best
# remaining teams, to fill this many seeds.
PLAYOFF_TEAMS_PER_CONFERENCE = 8

# Every playoff round is a best-of-seven: first to 4 wins, up to 7 games. The higher
# seed hosts games 1, 2, 5, 6 and 7 (the 2-2-1-1-1 pattern in handball/playoffs.py).
PLAYOFF_SERIES_WINS_NEEDED = 4


# Offseason rollover ("advance season").
RETIREMENT_CANDIDATE_AGE = 35   # players older than this are offered to the commissioner
                                # as retirement candidates (the commissioner decides).
DRAFT_ROUNDS = 2                # rounds of draft-pick order seeded from final standings


# Salary cap & contracts. All dollar figures are in millions/year (matching
# Player.contract_value). See handball/salary_cap.py for how these compose into
# a team's cap situation and what a given team may sign.
MAX_CONTRACT_YEARS = 5               # longest contract term allowed
MAX_CONTRACT_VALUE = 45              # highest annual salary allowed ($M/yr)
MIN_CONTRACT_VALUE = 0               # a "minimum" contract is $0M/yr, so it never
                                     # counts against the cap (total_salaries sums value)

SALARY_CAP = 150                     # soft cap: a team may exceed it only to re-sign
                                     # its own players (Bird rights) or via the MLE
FIRST_LUXURY_TAX_THRESHOLD = 175     # payroll below this -> FIRST_MLE available
SECOND_LUXURY_TAX_THRESHOLD = 200    # payroll below this -> SECOND_MLE available
HARD_CAP = 250                       # payroll may NEVER exceed this, under any circumstance

FIRST_MLE = 10                       # mid-level exception for teams below the first threshold
SECOND_MLE = 5                       # mid-level exception for teams below the second threshold

# Offseason free agency (handball/free_agency.py). How long a team may sit on a turn
# it owns -- an RFA match window or its turn in sequential bidding -- before the
# league acts for it. Sequential bidding is strictly ordered, so one manager who
# stops answering halts the board and, through it, the whole round; the clock is what
# makes the market finish without the commissioner having to chase anybody. It is
# generous on purpose: managers are people with jobs, and forfeiting a player because
# somebody slept is worse than a slow auction.
FA_TURN_LIMIT_HOURS = 48


# --- the draft room ------------------------------------------------------
# The draft is live and turn-based (handball/draft.py) and runs on the same lazy
# clock as free agency: there is no scheduler in this deployment, so the state read
# sweeps an expired turn before it answers. Shorter than FA_TURN_LIMIT_HOURS because
# a draft is one sitting with everyone watching, not a market that runs for days.
DRAFT_TURN_LIMIT_HOURS = 24

# Lottery odds for the 16 non-playoff teams, WORST TEAM FIRST, in percent. The draw is
# sequential: weight i belongs to the i-th worst team still in the pool, the winner is
# removed, and the remaining weights are renormalised for the next slot -- so these are
# the odds on the FIRST pick only, and every later slot is conditional on what came
# before. They sum to 100 for readability; nothing requires it.
LOTTERY_WEIGHTS = (25, 20, 15, 10, 5, 5, 5, 5, 2, 2, 1, 1, 1, 1, 1, 1)

# The rookie scale: (first overall pick, last overall pick, years, $M/yr). A drafted
# player's contract is not negotiated -- where you were taken IS the deal, which is what
# makes a traded pick a knowable asset. Rookie contracts are exempt from the cap at
# signing (see salary_cap), so a team can always sign its picks.
ROOKIE_SCALE = (
    (1, 10, 5, 5),
    (11, 20, 5, 4),
    (21, 32, 5, 3),
    (33, 48, 2, 2),
    (49, 64, 2, 1),
)


# --- award voting --------------------------------------------------------
# The managers vote (handball/voting.py). Top Scorer and Top Goalie stay auto-computed
# stat titles and are deliberately NOT in this list -- they are facts, not opinions.
AWARDS = ("Most Valuable Player", "Rookie of the Year", "Defensive Player of the Year",
          "Eleventh Man of the Year", "Most Improved Player", "Coach of the Year")
AWARD_BALLOT_SIZE = 5                # ranked places on one ballot
AWARD_POINTS = (10, 7, 5, 3, 1)      # points for 1st..5th place; ties break on 1st-place votes
AWARD_VOTING_OPENS_AFTER_PERIOD = 5  # i.e. once the regular season is complete

# The All-Star ballot is POSITIONAL, one per conference: this many names per position.
# The top 3/3/3/1 by votes start the exhibition, the rest come off the bench.
ALL_STAR_BALLOT = {"Forward": 5, "Midfielder": 5, "Defense": 5, "Goalie": 2}
ALL_STAR_AFTER_PERIOD = 3            # the break falls between periods 3 and 4


# --- extensions & the deadline -------------------------------------------
# An extension is agreed in one window per season and starts at the NEXT rollover
# (handball/extensions.py). Only players in the last year of a deal are eligible, so
# an extension is always a decision about a player about to leave.
EXTENSION_WINDOW_AFTER_PERIOD = 1
EXTENSION_ELIGIBLE_YEARS_REMAINING = 1

# No trade may be proposed or accepted once this many periods have run; a trade already
# accepted may still be approved, since period 5 already requires a clear queue.
TRADE_DEADLINE_AFTER_PERIOD = 4
