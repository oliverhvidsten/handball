"""
Name: operations_handler.py
Description: High-level orchestration for schedules and season simulations.

This module defines an OperationsHandler class that can:
  - Create a new schedule for a given season year.
  - Run simulations for one of five 11-week periods in a 55-week season.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from handball.game_simulator import GameSimulator
from handball.schedule_generator import ScheduleGenerator
from handball.sheets_handler import SheetHandler
from handball.teams import Team, TeamInfo


TEAM_NAMES: List[str] = [
    "Boston",
    "New York",
    "Philadelphia",
    "Washington",
    "Charlotte",
    "Atlanta",
    "Miami",
    "Tampa Bay",
    "Toronto",
    "Detroit",
    "Cleveland",
    "Chicago",
    "Cincinnati",
    "Louisville",
    "Nashville",
    "Indianapolis",
    "Milwaukee",
    "Minneapolis",
    "St. Louis",
    "Kansas City",
    "Oklahoma City",
    "New Orleans",
    "Dallas",
    "Houston",
    "Phoenix",
    "Los Angeles",
    "San Diego",
    "San Francisco",
    "Las Vegas",
    "Denver",
    "Seattle",
    "Vancouver",
]


CURRENT_SCHEDULE_PATH = (
    "/Users/oliverhvidsten/Documents/handball/handball/datafiles/current_schedule.json"
)


class OperationsHandler:
    """
    High-level operations handler for league seasons.

    Responsibilities:
      - Creating a new schedule for a given season year.
      - Running simulations for one of five periods (11 weeks each).
    """

    def __init__(self, cred_path: Optional[str] = None) -> None:
        """
        Initialize the operations handler.

        cred_path (optional): path to a file containing the Google Sheet ID
        on the first line. If not provided, uses the project-default path.
        """
        if cred_path is None:
            cred_path = "/Users/oliverhvidsten/Documents/handball/cred.txt"

        with open(cred_path, "r") as f:
            sheet_id = f.readline().strip()

        self.sheet_handler = SheetHandler(sheet_id=sheet_id)
        # In-memory state for the current season only
        self._schedule_data: Optional[Dict[str, Any]] = None
        self._teams: Optional[Dict[str, Team]] = None

    def _load_all_teams(self) -> Dict[str, Team]:
        """Load all 32 teams from the Google Sheet / JSON data."""
        teams: Dict[str, Team] = {}
        for name in TEAM_NAMES:
            team_info = TeamInfo.from_sheet(
                sheet_handler=self.sheet_handler,
                team_name=name,
                get_draft_picks=False,
            )
            team = Team.from_TeamInfo(team_info)
            teams[name] = team
        return teams

    def create_season_schedule(self, year: int, seed: Optional[int] = None) -> None:
        """
        Create and store a new schedule for the given season year.

        If the season already exists, this will overwrite the existing
        schedule and teams for that year.
        """
        # Generate a new schedule with OR-Tools and persist it to JSON
        schedule = ScheduleGenerator(seed=seed)
        self._schedule_data = schedule.to_json_serializable()

        import json
        import os

        os.makedirs(
            os.path.dirname(CURRENT_SCHEDULE_PATH), exist_ok=True  # type: ignore[arg-type]
        )
        with open(CURRENT_SCHEDULE_PATH, "w") as f:
            json.dump(self._schedule_data, f, indent=2)



    def _ensure_schedule_loaded(self) -> None:
        """Ensure schedule JSON is loaded into memory."""
        if self._schedule_data is not None:
            return

        import json
        import os

        if not os.path.exists(CURRENT_SCHEDULE_PATH):
            raise FileNotFoundError(
                f"No current schedule found at {CURRENT_SCHEDULE_PATH}. "
                "Run create_season_schedule first."
            )
        with open(CURRENT_SCHEDULE_PATH, "r") as f:
            self._schedule_data = json.load(f)

    def run_period_simulation(self, period: int) -> None:
        """
        Run simulations for one of the five 11-week periods in the season.

        period: integer from 1 to 5
            1 -> weeks 1-11
            2 -> weeks 12-22
            3 -> weeks 23-33
            4 -> weeks 34-44
            5 -> weeks 45-55
        """
        if period < 1 or period > 5:
            raise ValueError("period must be an integer in [1, 5]")

        # Lazily load schedule and teams if not already in memory
        self._ensure_schedule_loaded()
        if self._teams is None:
            self._teams = self._load_all_teams()

        schedule_data = self._schedule_data  # type: ignore[assignment]
        weeks: List[List[Dict[str, Any]]] = schedule_data["weeks"]  # type: ignore[index]
        teams = self._teams

        start_week = (period - 1) * 11 + 1
        end_week = period * 11

        for week_index in range(start_week, end_week + 1):
            if week_index < 1 or week_index > len(weeks):
                continue
            week_games = weeks[week_index - 1]
            for g in week_games:
                home_name = str(g["team1"])
                away_name = str(g["team2"])
                home_team = teams[home_name]
                away_team = teams[away_name]

                game = GameSimulator(home_team, away_team)
                game.simulate_game()

