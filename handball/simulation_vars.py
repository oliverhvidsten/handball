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

REGULATION_TIME = 60*3600

STARTER_MINUTES = 45
BENCH_MINUTES = 22.5

TIME_PER_PASS = 2
TIME_PER_SHOT = 5
TIME_AFTER_SCORE = 10

MAIN_STAT = 3
SECONDARY_STAT = 1
MIDDIE_STATS = 2


# tune probabilities
K = 0.35


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


# Offseason rollover ("advance season").
RETIREMENT_CANDIDATE_AGE = 35   # players older than this are offered to the commissioner
                                # as retirement candidates (the commissioner decides).
DRAFT_ROUNDS = 2                # rounds of draft-pick order seeded from final standings

