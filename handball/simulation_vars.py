"""
Name: simulation_vars.py
Description: Similar purpos to constants.py but reserved for game information
Author: Oliver Hvidsten
"""



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

