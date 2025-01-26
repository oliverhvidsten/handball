"""
Name: constants.py
Description: This file holds constant to be referenced throuhout the codebase. Beneficial because if the values do change,
we only have to update this file
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/25/2025 1:28PM PST
"""

# Google Sheets formatting values (values in reference to PLAYER_RANGES)

## General Information
TEAM_RANGE = "A3:D32"

STARTERS_ROW = 5 # These are the indicies of the first player in the section
BENCH_ROW = 17
RESERVES_ROW = 26

NAME_COL = 1
OFF_COL = 2
DEF_COL = 3

HEAD_COACH_LOC = (0, 1)
OFF_COACH_LOC = (1, 1)
DEF_COACH_LOC = (2, 1)

SHEET_ID_NUM = {
"Overview": 0,
"Boston": 1041798491,
"New York": 1573880573,
"Philadelphia": 1170392677,
"Washington": 408593565,
"Charlotte": 743786023,
"Atlanta": 1717452812,
"Miami": 1438549604,
"Tampa Bay": 1361867512,
"Toronto": 1170088507,
"Detroit": 578455838,
"Cleveland": 567656831,
"Chicago": 763675625,
"Cincinnati": 1045648603,
"Louisville": 22869661,
"Nashville": 1352305794,
"Indianapolis": 1824743909,
"Milwaukee": 1369727113,
"Minneapolis": 1873493404,
"St. Louis": 1704956772,
"Kansas City": 1130830915,
"Oklahoma City": 2104883565,
"New Orleans": 1297016396,
"Dallas": 136148379,
"Houston": 1864395313,
"Phoenix": 833543054,
"Los Angeles": 1504622000,
"San Diego": 1183749616,
"San Francisco": 912216680,
"Las Vegas": 2057538913,
"Denver": 1025618215,
"Seattle": 582952534,
"Vancouver": 1227986819,
"Free Agents": 767228655
}

## This is for notes pulling
PLAYERS_RANGE = 'B8:B32'
STARTER_RANGE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
BENCH_RANGE = [12, 13, 14, 15, 16, 17, 18]
RESERVE_RANGE = [21, 22, 23, 24]