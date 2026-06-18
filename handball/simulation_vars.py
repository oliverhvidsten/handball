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


# Major injuries can damage a player's development.
MAJOR_INJURY_IMPACT_CHANCE = 0.5   # chance a major injury affects trajectory
INJURY_GROWTH_PENALTY = 0.9          # young players: multiply max stats (slows growth)
INJURY_DECLINE_MULTIPLIER = 1.3      # older players: multiply decline_rate
MAX_DECLINE_RATE = 0.5               # cap on decline_rate

