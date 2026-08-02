"""
Name: league_structure.py
Description: Who plays in which conference and division -- the league's shape,
    as plain data.

    This lived in schedule_generator.py, which imports ortools at module scope.
    That was fine while the only caller was the schedule solver, but the
    postseason needs the same map to seed a bracket, and pulling a CP-SAT solver
    into an API request just to ask "which conference is Denver in?" is a cost
    with no payer. The dict moved here; schedule_generator re-exports it under
    its old names, so nothing that imported it from there had to change.

    Team keys are TeamIds -- i.e. teams.slug, stored verbatim (see pg_repository:
    domain TeamId == teams.slug == "New York"). A team named here that is not in
    the database (or vice versa) is a genuine misconfiguration; the callers that
    care say so loudly rather than silently dropping the team.
Author: postseason wiring
"""
from __future__ import annotations

from typing import Dict, List

LEAGUE: Dict[str, Dict[str, List[str]]] = {
    "Eastern": {
        "Mid-Atlantic": ["Boston", "New York", "Philadelphia", "Washington"],
        "South": ["Charlotte", "Atlanta", "Miami", "Tampa Bay"],
        "Midwest": ["Toronto", "Detroit", "Cleveland", "Chicago"],
        "Country": ["Cincinnati", "Louisville", "Nashville", "Indianapolis"],
    },
    "Western": {
        "North": ["Milwaukee", "Minneapolis", "St. Louis", "Kansas City"],
        "South": ["Oklahoma City", "New Orleans", "Dallas", "Houston"],
        "Pacific": ["Phoenix", "Los Angeles", "San Diego", "San Francisco"],
        "Mountain": ["Las Vegas", "Denver", "Seattle", "Vancouver"],
    },
}

# Conference order is fixed (not sorted) so a bracket always renders East-then-West.
CONFERENCES: tuple[str, ...] = tuple(LEAGUE)

_TEAM_TO_CONF: Dict[str, str] = {}
_TEAM_TO_DIV: Dict[str, str] = {}
for _conf, _divisions in LEAGUE.items():
    for _div, _teams in _divisions.items():
        for _t in _teams:
            _TEAM_TO_CONF[_t] = _conf
            _TEAM_TO_DIV[_t] = _div


def get_conference(team: str) -> str:
    return _TEAM_TO_CONF[team]


def get_division(team: str) -> str:
    return _TEAM_TO_DIV[team]


def all_teams() -> list[str]:
    """Every configured team id, in conference/division order."""
    return list(_TEAM_TO_CONF)
