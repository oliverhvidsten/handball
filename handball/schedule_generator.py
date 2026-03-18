from __future__ import annotations

import argparse
import csv
import json
import os
import random
from collections import Counter
from typing import Dict, List, Tuple, Optional, Literal

from ortools.sat.python import cp_model


MatchupType = Literal["division_rival", "division_non_rival", "conference", "inter_conference"]


league: Dict[str, Dict[str, List[str]]] = {
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


# Path where persistent rival assignments are stored.
RIVALS_JSON_PATH = os.path.join(
    os.path.dirname(__file__),
    "datafiles",
    "rivals.json",
)


def assign_rivals(league_def: Dict[str, Dict[str, List[str]]], seed: Optional[int] = None) -> Dict[str, str]:
    """
    Randomly assign symmetric rivalry pairs within each 4‑team division.

    Returns a mapping from team -> single rival.
    """
    rng = random.Random(seed)
    rivals: Dict[str, str] = {}
    for conference, divisions in league_def.items():
        for division, teams in divisions.items():
            if len(teams) != 4:
                raise ValueError(f"Division {conference}/{division} must have exactly 4 teams.")
            shuffled = teams[:]
            rng.shuffle(shuffled)
            # Pair 0-1 and 2-3
            a, b, c, d = shuffled
            rivals[a] = b
            rivals[b] = a
            rivals[c] = d
            rivals[d] = c
    return rivals


def load_or_create_rivals(
    league_def: Dict[str, Dict[str, List[str]]],
    seed: Optional[int] = None,
    path: str = RIVALS_JSON_PATH,
) -> Dict[str, str]:
    """
    Load rival assignments from JSON if present; otherwise assign once and persist.

    Once written, the JSON file is treated as the single source of truth, and
    subsequent calls ignore the seed and simply reload the stored mapping.
    """
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        # Basic sanity: ensure every team has a rival in same division
        for team in _all_teams():
            if team not in data:
                raise ValueError(f"Rivals JSON missing team {team}")
        return data

    # No existing file: assign rivals using the provided seed and persist.
    rivals = assign_rivals(league_def, seed=seed)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(rivals, f, indent=2)
    return rivals


# Precompute lookup maps for helpers
_TEAM_TO_CONF: Dict[str, str] = {}
_TEAM_TO_DIV: Dict[str, str] = {}
for conf_name, divisions in league.items():
    for div_name, teams in divisions.items():
        for t in teams:
            _TEAM_TO_CONF[t] = conf_name
            _TEAM_TO_DIV[t] = div_name


def get_conference(team: str) -> str:
    return _TEAM_TO_CONF[team]


def get_division(team: str) -> str:
    return _TEAM_TO_DIV[team]


def get_rival(team: str, rivals: Dict[str, str]) -> str:
    return rivals[team]


def get_division_non_rivals(team: str, rivals: Dict[str, str]) -> List[str]:
    conf = get_conference(team)
    div = get_division(team)
    division_teams = league[conf][div]
    rival = get_rival(team, rivals)
    return [t for t in division_teams if t not in (team, rival)]


def get_conference_teams(team: str, rivals: Dict[str, str]) -> List[str]:
    """Non‑division, same‑conference teams (12 opponents)."""
    conf = get_conference(team)
    div = get_division(team)
    teams: List[str] = []
    for d_name, d_teams in league[conf].items():
        if d_name == div:
            continue
        teams.extend(d_teams)
    if len(teams) != 12:
        raise AssertionError(f"Expected 12 non‑division conference teams for {team}, got {len(teams)}")
    return teams


def get_opposite_conference_teams(team: str) -> List[str]:
    """All teams in the opposite conference (16 opponents)."""
    conf = get_conference(team)
    other_conf = "Western" if conf == "Eastern" else "Eastern"
    teams: List[str] = []
    for d_teams in league[other_conf].values():
        teams.extend(d_teams)
    if len(teams) != 16:
        raise AssertionError(f"Expected 16 opposite‑conference teams for {team}, got {len(teams)}")
    return teams


def _all_teams() -> List[str]:
    teams: List[str] = []
    for divisions in league.values():
        for d_teams in divisions.values():
            teams.extend(d_teams)
    return teams


def _matchup_type(a: str, b: str, rivals: Dict[str, str]) -> MatchupType:
    if get_conference(a) != get_conference(b):
        return "inter_conference"
    if get_division(a) == get_division(b):
        if get_rival(a, rivals) == b:
            return "division_rival"
        return "division_non_rival"
    return "conference"


def _games_for_pair(a: str, b: str, rivals: Dict[str, str]) -> int:
    mtype = _matchup_type(a, b, rivals)
    if mtype == "division_rival":
        return 4
    if mtype == "division_non_rival":
        return 3
    if mtype == "conference":
        return 2
    if mtype == "inter_conference":
        return 1
    raise ValueError(f"Unknown matchup type {mtype}")


def generate_pair_counts(rivals: Dict[str, str]) -> Dict[frozenset, Tuple[int, MatchupType]]:
    """
    Build symmetric game counts for every unordered pair of teams.
    Returns mapping: frozenset({team_a, team_b}) -> (games, matchup_type_from_a_perspective)
    """
    teams = _all_teams()
    pair_counts: Dict[frozenset, Tuple[int, MatchupType]] = {}
    for i, a in enumerate(teams):
        for j in range(i + 1, len(teams)):
            b = teams[j]
            games = _games_for_pair(a, b, rivals)
            mtype = _matchup_type(a, b, rivals)
            pair_counts[frozenset({a, b})] = (games, mtype)
    return pair_counts


def build_weeks(
    pair_counts: Dict[frozenset, Tuple[int, MatchupType]],
    seed: Optional[int] = None,
    num_weeks: int = 55,
    games_per_team: int = 50,
) -> List[List[Tuple[str, str, MatchupType]]]:
    """
    Use a CP-SAT solver (OR-Tools) to build a week-by-week schedule satisfying:

    - Each team plays at most one game per week.
    - Each team plays exactly `games_per_team` games in total.
    - All pair-wise game counts from `pair_counts` (4/3/2/1) are satisfied.

    With 32 teams, 50 games each, and at most one game per week, we require
    at least 50 weeks. We fix `num_weeks` to 55, yielding exactly 5 bye weeks
    per team.
    """
    teams = _all_teams()
    num_teams = len(teams)
    if num_teams != 32:
        raise ValueError("Expected exactly 32 teams for this scheduler.")

    team_index: Dict[str, int] = {t: i for i, t in enumerate(teams)}

    # Map pairs to indices and preserve types/counts
    pair_list: List[Tuple[int, int]] = []
    pair_games: List[int] = []
    pair_types: List[MatchupType] = []
    for pair, (games, mtype) in pair_counts.items():
        a, b = tuple(pair)
        i, j = team_index[a], team_index[b]
        if i > j:
            i, j = j, i
        pair_list.append((i, j))
        pair_games.append(games)
        pair_types.append(mtype)

    num_pairs = len(pair_list)

    model = cp_model.CpModel()

    # Decision variables: x[p, w] = 1 if pair p plays in week w
    x: Dict[Tuple[int, int], cp_model.IntVar] = {}
    for p in range(num_pairs):
        for w in range(num_weeks):
            x[(p, w)] = model.NewBoolVar(f"x_p{p}_w{w}")

    # plays[i,w] = 1 if team i plays in week w
    plays: Dict[Tuple[int, int], cp_model.IntVar] = {}
    for i in range(num_teams):
        for w in range(num_weeks):
            plays[(i, w)] = model.NewBoolVar(f"plays_t{i}_w{w}")

    # 1) Required games per pair
    for p in range(num_pairs):
        model.Add(sum(x[(p, w)] for w in range(num_weeks)) == pair_games[p])

    # Precompute for each team which pairs it is in
    team_pairs: Dict[int, List[int]] = {i: [] for i in range(num_teams)}
    for p, (i, j) in enumerate(pair_list):
        team_pairs[i].append(p)
        team_pairs[j].append(p)

    # 2) At most one game per team per week and definition of plays[i,w]
    for i in range(num_teams):
        for w in range(num_weeks):
            incident = [x[(p, w)] for p in team_pairs[i]]
            if incident:
                model.Add(sum(incident) <= 1)
                # plays[i,w] == sum(incident) (both sides are 0/1)
                model.Add(sum(incident) == plays[(i, w)])
            else:
                model.Add(plays[(i, w)] == 0)

    # 3) Exactly games_per_team games per team
    for i in range(num_teams):
        model.Add(
            sum(plays[(i, w)] for w in range(num_weeks)) == games_per_team
        )

    # Optional: hint the solver with a seed for reproducibility
    solver = cp_model.CpSolver()
    if seed is not None:
        solver.parameters.random_seed = seed
    solver.parameters.max_time_in_seconds = 60.0

    result = solver.Solve(model)
    if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError("CP-SAT solver failed to find a valid schedule.")

    # Extract weeks
    weeks: List[List[Tuple[str, str, MatchupType]]] = []
    for w in range(num_weeks):
        week_games: List[Tuple[str, str, MatchupType]] = []
        for p, (i, j) in enumerate(pair_list):
            if solver.BooleanValue(x[(p, w)]):
                team1 = teams[i]
                team2 = teams[j]
                mtype = pair_types[p]
                week_games.append((team1, team2, mtype))
        if week_games:
            weeks.append(week_games)

    return weeks


def generate_team_schedule(
    team: str,
    pair_counts: Dict[frozenset, Tuple[int, MatchupType]],
) -> List[Tuple[str, int, MatchupType]]:
    """
    Return (opponent, num_games, matchup_type) tuples for a single team,
    derived from the global symmetric pair_counts.
    """
    entries: List[Tuple[str, int, MatchupType]] = []
    for pair, (games, mtype) in pair_counts.items():
        if team in pair:
            opponent = next(t for t in pair if t != team)
            entries.append((opponent, games, mtype))
    # Sort for determinism
    entries.sort(key=lambda x: x[0])
    return entries


def generate_full_schedule(
    rivals: Dict[str, str],
) -> Dict[str, List[Tuple[str, int, MatchupType]]]:
    pair_counts = generate_pair_counts(rivals)
    schedule: Dict[str, List[Tuple[str, int, MatchupType]]] = {}
    for team in _all_teams():
        schedule[team] = generate_team_schedule(team, pair_counts)
    return schedule


def validate_schedule(
    schedule: Dict[str, List[Tuple[str, int, MatchupType]]],
    rivals: Dict[str, str],
) -> None:
    """
    Validate that the schedule satisfies all specified constraints.
    Raises AssertionError on failure, prints success message otherwise.
    """
    errors: List[str] = []
    teams = _all_teams()

    # Validate per-team composition
    for team in teams:
        entries = schedule.get(team)
        if entries is None:
            errors.append(f"No schedule found for team {team}")
            continue

        total_games = sum(g for _, g, _ in entries)
        if total_games != 50:
            errors.append(f"{team}: expected 50 games, got {total_games}")

        # Count games by opponent
        opp_counter: Counter = Counter()
        for opp, games, _ in entries:
            opp_counter[opp] += games

        # Rival
        rival = get_rival(team, rivals)
        if opp_counter[rival] != 4:
            errors.append(f"{team}: expected 4 games vs rival {rival}, got {opp_counter[rival]}")

        # Division non-rivals
        non_rivals = get_division_non_rivals(team, rivals)
        for nr in non_rivals:
            if opp_counter[nr] != 3:
                errors.append(f"{team}: expected 3 games vs division non‑rival {nr}, got {opp_counter[nr]}")

        # Same-conference, non-division opponents
        conf_teams = get_conference_teams(team, rivals)
        for ct in conf_teams:
            if opp_counter[ct] != 2:
                errors.append(f"{team}: expected 2 games vs conference opponent {ct}, got {opp_counter[ct]}")

        # Opposite-conference opponents
        other_conf = get_opposite_conference_teams(team)
        for oc in other_conf:
            if opp_counter[oc] != 1:
                errors.append(f"{team}: expected 1 game vs opposite‑conference opponent {oc}, got {opp_counter[oc]}")

    # Symmetry: if A plays B N times, B plays A N times
    for team, entries in schedule.items():
        for opp, games, _ in entries:
            opp_entries = schedule.get(opp, [])
            opp_counter: Counter = Counter({t: g for t, g, _ in opp_entries})
            if opp_counter[team] != games:
                errors.append(
                    f"Symmetry violation: {team} vs {opp} has {games} games, "
                    f"but {opp} vs {team} has {opp_counter[team]}"
                )

    # Rival relationships: exactly one rival in same division
    for team in teams:
        rival = rivals.get(team)
        if rival is None:
            errors.append(f"{team}: has no rival assigned")
            continue
        if get_division(team) != get_division(rival):
            errors.append(f"{team}: rival {rival} is not in the same division")
        if rivals.get(rival) != team:
            errors.append(f"{team}: rivalry with {rival} is not symmetric")

    if errors:
        raise AssertionError("Schedule validation failed:\n" + "\n".join(errors))
    print("✅ All validations passed")


class ScheduleGenerator:
    """
    Container for league schedule and rivalries, designed to be JSON serializable.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        # Seed is only used the first time rivals are created. Once the rivals
        # JSON exists, it becomes the single source of truth.
        self.seed = seed
        self.rivals: Dict[str, str] = load_or_create_rivals(league, seed=seed)

        # Build pair-wise symmetric counts and then a week-by-week schedule
        # where each team plays at most one game per week.
        pair_counts = generate_pair_counts(self.rivals)
        self.weeks: List[List[Dict[str, object]]] = []
        for week in build_weeks(pair_counts, seed=seed):
            self.weeks.append(
                [
                    {"team1": a, "team2": b, "matchup_type": mtype}
                    for (a, b, mtype) in week
                ]
            )

        # Derive per-team aggregated schedule (opponent + game count) from weeks,
        # stored in a JSON-friendly structure.
        per_team: Dict[str, Counter] = {t: Counter() for t in _all_teams()}
        for week in self.weeks:
            for g in week:
                a = str(g["team1"])
                b = str(g["team2"])
                mtype = str(g["matchup_type"])
                per_team[a][(b, mtype)] += 1
                per_team[b][(a, mtype)] += 1

        self.schedule: Dict[str, List[Dict[str, object]]] = {}
        for team, counter in per_team.items():
            entries: List[Dict[str, object]] = []
            for (opp, mtype), games in sorted(counter.items()):
                entries.append(
                    {"opponent": opp, "games": games, "matchup_type": mtype}
                )
            self.schedule[team] = entries

    def evaluate_week(self, n: int) -> List[Dict[str, object]]:
        """
        Return all games scheduled in "week n".

        Weeks are constructed so that each team appears in at most one game
        per week. A week is thus a list of matchups, each represented as:

            {"team1": ..., "team2": ..., "matchup_type": ...}
        """
        if n <= 0:
            raise ValueError("Week index n must be >= 1")

        if n > len(self.weeks):
            return []

        # Return a shallow copy to avoid callers mutating internal state
        return list(self.weeks[n - 1])

    def to_json_serializable(self) -> Dict[str, object]:
        return {
            "seed": self.seed,
            "rivals": self.rivals,
            "schedule": self.schedule,
        }

    def __str__(self) -> str:
        """
        Human-readable representation of the full schedule, grouped by
        "week" index: all teams' 1st games, then all 2nd games, etc.
        """
        lines: List[str] = []
        week_index = 1

        while week_index <= len(self.weeks):
            week_games = self.evaluate_week(week_index)
            if not week_games:
                break

            lines.append(f"=== Week {week_index} ===")
            # Sort for deterministic output: by team1 name, then team2
            week_games_sorted = sorted(
                week_games, key=lambda g: (g["team1"], g["team2"])
            )
            for g in week_games_sorted:
                lines.append(
                    f"{g['team1']} vs {g['team2']} ({g['matchup_type']})"
                )
            lines.append("")  # blank line between weeks

            week_index += 1

        return "\n".join(lines) if lines else "No scheduled games."


def print_rivalry_summary(rivals: Dict[str, str]) -> None:
    print("=== Rivalry Pairs ===\n")
    for conf_name, divisions in league.items():
        print(f"{conf_name} Conference")
        for div_name, teams in divisions.items():
            # Build pairs for this division
            seen = set()
            pairs: List[Tuple[str, str]] = []
            for t in teams:
                if t in seen:
                    continue
                r = rivals[t]
                pairs.append((t, r))
                seen.add(t)
                seen.add(r)
            pair_str = "  |  ".join(f"{a} ↔ {b}" for a, b in pairs)
            print(f"{div_name}:  {pair_str}")
        print()


def export_schedule_to_csv(
    schedule: Dict[str, List[Dict[str, object]]],
    path: str,
) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Team", "Opponent", "Games", "Matchup Type"])
        for team, entries in schedule.items():
            for entry in entries:
                writer.writerow(
                    [
                        team,
                        entry["opponent"],
                        entry["games"],
                        entry["matchup_type"],
                    ]
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate full league schedule.")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for rival assignment (optional).",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Optional path to export schedule as CSV.",
    )
    args = parser.parse_args()

    generator = ScheduleGenerator(seed=args.seed)

    # Print rivalry summary
    print_rivalry_summary(generator.rivals)

    # Reconstruct tuple-based schedule for validation
    tuple_schedule: Dict[str, List[Tuple[str, int, MatchupType]]] = {}
    for team, entries in generator.schedule.items():
        tuple_schedule[team] = [
            (str(e["opponent"]), int(e["games"]), str(e["matchup_type"]))  # type: ignore[arg-type]
            for e in entries
        ]

    # Validate
    validate_schedule(tuple_schedule, generator.rivals)

    # Optionally export CSV
    if args.csv:
        export_schedule_to_csv(generator.schedule, args.csv)
        print(f"Schedule exported to {args.csv}")

    # Demonstrate JSON serializability (not printed by default)
    _ = json.dumps(generator.to_json_serializable())


if __name__ == "__main__":
    main()

