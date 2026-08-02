"""
Name: calibrate_sim.py
Description: Prints the calibration table for the game simulator, run against the real
             league in handball/datafiles_v2. Use this after touching any constant in
             simulation_vars.py or any probability in game_simulator.py.
Author: Oliver Hvidsten

Usage:
    python scripts/calibrate_sim.py
    python scripts/calibrate_sim.py --games 800 --seed 3

What good looks like (the bands tests/test_game_simulator.py::TestCalibration enforces):

    goals / team / game     ~12, median 12, and >=80% of team-scores at or below 15
    shot attempts           ~38          shooting %      ~30%
    passing turnovers       ~16          GK save %       ~50%
    possessions / team      ~54
    best vs worst           ~90%         best vs 2nd     ~55%
    a team vs itself        ~50%         (no home advantage is modelled)
"""
import argparse
import copy
import itertools
import random
from pathlib import Path

import numpy as np

from handball.game_simulator import GameSimulator
from handball.repository import JsonTeamRepository
from handball.simulation_vars import (
    BENCH_MINUTES,
    GOALIE_MULTIPLIER,
    GOAL_CONVERSION_REF,
    GOAL_SHOOTER_WEIGHT,
    GOAL_TEAM_WEIGHT,
    MAIN_STAT,
    MIDDIE_STATS,
    SECONDARY_STAT,
    STARTER_MINUTES,
)

DATAFILES = Path(__file__).resolve().parent.parent / "handball" / "datafiles_v2"


def team_rating(team):
    """
    The minutes- and slot-weighted offense/defense/goalie totals, without the per-game
    RNG. This mirrors GameSimulator.init_stats -- if the weighting there ever changes,
    change it here too.
    """
    minutes = np.array([STARTER_MINUTES] * 9 + [BENCH_MINUTES] * 6) / 60
    groups = [
        (team.starters["Forward"], MAIN_STAT, SECONDARY_STAT),
        (team.starters["Midfielder"], MIDDIE_STATS, MIDDIE_STATS),
        (team.starters["Defense"], SECONDARY_STAT, MAIN_STAT),
        (team.bench["Forward"], MAIN_STAT, SECONDARY_STAT),
        (team.bench["Midfielder"], MIDDIE_STATS, MIDDIE_STATS),
        (team.bench["Defense"], SECONDARY_STAT, MAIN_STAT),
    ]
    offense, defense = [], []
    for players, off_weight, def_weight in groups:
        for p in players:
            offense.append(p.offense * off_weight)
            defense.append(p.defense * def_weight)
    # Both goalies play, so weight them the way the game actually splits the net.
    goalie = (
        team.starters["Goalie"][0].goalie_skill * 0.75
        + team.bench["Goalie"][0].goalie_skill * 0.25
    ) * GOALIE_MULTIPLIER
    return (
        float(np.sum(np.array(offense) * minutes)),
        float(np.sum(np.array(defense) * minutes)),
        goalie,
    )


def play(home, away):
    """Play one game on throwaway copies, so the source teams are never mutated."""
    sim = GameSimulator(copy.deepcopy(home), copy.deepcopy(away))
    sim.simulate_game()
    return sim


def win_rate(home, away, games):
    wins = 0
    for _ in range(games):
        sim = play(home, away)
        wins += sim.home_score > sim.away_score
    return wins / games


def box_score(home, away, games):
    rows = []
    for _ in range(games):
        sim = play(home, away)
        st = sim.stat_tracker
        rows.append((
            sim.home_score,
            st.home_shots.sum(),
            st.home_turnovers,
            st.away_goalie_saves,
            st.away_goalie_goals_allowed,
        ))
    goals, shots, turnovers, saves, allowed = np.array(rows, dtype=float).mean(axis=0)
    on_goal = saves + allowed
    print("\n=== Box score, per team per game (evenly matched pairing) ===")
    print(f"  goals              {goals:6.1f}")
    print(f"  shot attempts      {shots:6.1f}     shooting %  {goals / shots:6.1%}")
    print(f"  shots on goal      {on_goal:6.1f}     on-goal %   {on_goal / shots:6.1%}")
    print(f"  GK saves faced     {saves:6.1f}     save %      {saves / on_goal:6.1%}")
    print(f"  passing turnovers  {turnovers:6.1f}")
    print(f"  possessions        {shots + turnovers:6.1f}")


def score_distribution(teams, reps):
    scores = []
    for _ in range(reps):
        for home, away in itertools.permutations(teams, 2):
            sim = play(home, away)
            scores += [sim.home_score, sim.away_score]
    s = np.array(scores)
    print(f"\n=== Score distribution ({len(s)} team-scores across every matchup) ===")
    pct = [np.percentile(s, q) for q in (10, 25, 50, 75, 90, 99)]
    print(f"  mean {s.mean():.2f}   median {np.median(s):.0f}   max {s.max()}")
    print("  p10 {:.0f}   p25 {:.0f}   p50 {:.0f}   p75 {:.0f}   p90 {:.0f}   p99 {:.0f}".format(*pct))
    print(f"  P(team score <= 15) = {(s <= 15).mean():.1%}     P(<= 20) = {(s <= 20).mean():.1%}")


def predictability(ranked, games):
    print(f"\n=== Predictability ({games} games per pairing) ===")
    pairings = [
        ("best vs worst", ranked[0], ranked[-1]),
        ("best vs median", ranked[0], ranked[len(ranked) // 2]),
        ("best vs 2nd best", ranked[0], ranked[1]),
        ("median vs itself", ranked[len(ranked) // 2], ranked[len(ranked) // 2]),
    ]
    for label, home, away in pairings:
        print(f"  {label:18s} P(home win) = {win_rate(home, away, games):6.1%}")
    print("  (a team against itself measures symmetry -- no home advantage is modelled,")
    print("   so it should sit at ~50%)")


def season(teams, games_per_team, seed):
    rng = random.Random(seed)
    schedule = list(itertools.permutations(teams, 2)) * 2
    rng.shuffle(schedule)
    wins = {t.id: 0 for t in teams}
    played = {t.id: 0 for t in teams}
    for home, away in schedule[: len(teams) * games_per_team // 2]:
        sim = play(home, away)
        played[home.id] += 1
        played[away.id] += 1
        wins[(home if sim.home_score > sim.away_score else away).id] += 1

    ratings = np.array([sum(team_rating(t)) for t in teams])
    win_pct = np.array([wins[t.id] / max(played[t.id], 1) for t in teams])
    order = np.argsort(-win_pct)
    best_rated = int(np.argmax(ratings))
    print(f"\n=== Simulated {games_per_team}-game season ===")
    print(f"  win% range [{win_pct.min():.1%}, {win_pct.max():.1%}]   sd {win_pct.std():.3f}")
    print(f"  corr(team rating, win%) = {np.corrcoef(ratings, win_pct)[0, 1]:.3f}")
    print(f"  best-rated roster ({teams[best_rated].id}) finished "
          f"{int((win_pct > win_pct[best_rated]).sum()) + 1} of {len(teams)}")
    print("  top 3:   " + ", ".join(f"{teams[i].id} {win_pct[i]:.0%}" for i in order[:3]))
    print("  bottom 3:" + ", ".join(f" {teams[i].id} {win_pct[i]:.0%}" for i in order[-3:]))


def conversion_reference(teams):
    """
    Report the league-average quality ratio that GOAL_CONVERSION_REF is meant to centre.
    If this drifts away from the constant, scoring will drift with it -- re-centre it.
    """
    values = []
    for team in teams:
        offense, defense, goalie = team_rating(team)
        for opponent in teams:
            if opponent.id == team.id:
                continue
            _, opp_defense, opp_goalie = team_rating(opponent)
            shooters = list(team.starters["Forward"]) + list(team.starters["Midfielder"])
            mean_shooter = float(np.mean([p.offense for p in shooters]))
            values.append(
                (GOAL_TEAM_WEIGHT * offense + GOAL_SHOOTER_WEIGHT * mean_shooter)
                / (offense + opp_defense + opp_goalie)
            )
    observed = float(np.mean(values))
    print("\n=== Goal-conversion reference ===")
    print(f"  observed league mean quality ratio = {observed:.3f}")
    print(f"  GOAL_CONVERSION_REF                = {GOAL_CONVERSION_REF:.3f}")
    if abs(observed - GOAL_CONVERSION_REF) > 0.02:
        print("  ** drifted -- re-centre GOAL_CONVERSION_REF on the observed value **")


def inflation_neutrality(home, away, games):
    """
    Ratings inflate as the league develops. Both remapped probabilities take a ratio as
    input, so scoring should be flat here; if it is not, something has broken scale
    invariance and scores will creep upward season over season.
    """
    print("\n=== League-wide inflation neutrality ===")
    for bump in (0.0, 1.0, 2.0):
        a, b = copy.deepcopy(home), copy.deepcopy(away)
        for team in (a, b):
            for p in team.roster():
                p.offense = min(10.0, p.offense + bump)
                p.defense = min(10.0, p.defense + bump)
                if p.position == "Goalie":
                    p.goalie_skill = min(10.0, p.goalie_skill + bump)
        scores = []
        for _ in range(games):
            sim = play(a, b)
            scores += [sim.home_score, sim.away_score]
        print(f"  +{bump:.0f} to every rating: mean goals/team {np.mean(scores):5.2f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=400,
                        help="games per head-to-head pairing (default 400)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    np.random.seed(args.seed)
    random.seed(args.seed)

    teams = JsonTeamRepository(DATAFILES).load_all()
    ranked = sorted(teams, key=lambda t: -sum(team_rating(t)))
    print(f"Loaded {len(teams)} teams from {DATAFILES}")

    mid = len(ranked) // 2
    box_score(ranked[mid], ranked[mid], args.games)
    score_distribution(teams, reps=1)
    predictability(ranked, args.games)
    season(teams, games_per_team=50, seed=args.seed)
    conversion_reference(teams)
    inflation_neutrality(ranked[mid], ranked[mid + 1], args.games // 2)


if __name__ == "__main__":
    main()
