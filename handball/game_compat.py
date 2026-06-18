"""
Name: game_compat.py
Description: A thin compatibility view that lets the existing GameSimulator run on
    the new domain.Team WITHOUT modifying game_simulator.py (so the legacy path
    keeps working during migration).

    GameSimulator reads the legacy Subroster attribute API
    (team.starters.forwards / .midfielders / .defense / .goalie), uses
    team.team_name, calls win()/lose()/tie(), and writes per-game stats via
    update_offensive_stats()/update_goalie_stats(). TeamView exposes exactly that
    surface over a domain.Team, sharing the SAME Player objects -- so every stat
    the simulator records lands on the real model, and records flow into
    domain.Team.record_result().

    The stat-distribution logic mirrors handball.teams.Team exactly (same scorer
    index order, same 60/40 goalie split), with numpy scalars cast to plain ints
    so the results stay JSON-serializable through the repository.
Author: design sketch
"""
from __future__ import annotations

from handball.domain import Player, Team


class _SubrosterView:
    """Maps the domain dict layout to the legacy attribute API. Returns the live
    Player lists, so mutations are shared with the underlying Team."""

    def __init__(self, group: dict[str, list[Player]]):
        self._g = group

    @property
    def forwards(self) -> list[Player]:
        return self._g["Forward"]

    @property
    def midfielders(self) -> list[Player]:
        return self._g["Midfielder"]

    @property
    def defense(self) -> list[Player]:
        return self._g["Defense"]

    @property
    def goalie(self) -> Player:
        return self._g["Goalie"][0]


class TeamView:
    """Legacy Team/Subroster facade over a domain.Team."""

    def __init__(self, team: Team):
        self._team = team
        self.starters = _SubrosterView(team.starters)
        self.bench = _SubrosterView(team.bench)
        self.reserves = team.reserves

    @property
    def team_name(self) -> str:
        # GameSimulator uses team_name as the identifier; the domain id is the
        # stable key, so the game summary keys map straight to ids.
        return self._team.id

    # -- records ----------------------------------------------------------
    def win(self) -> None:
        self._team.record_result("W")

    def lose(self) -> None:
        self._team.record_result("L")

    def tie(self) -> None:
        self._team.record_result("T")

    # -- per-game stat writes (mirror handball.teams.Team) -----------------
    def update_performances(self, performances) -> None:
        s, b = self.starters, self.bench
        for i, perf in enumerate(performances):
            if i < 3:
                tgt = s.forwards[i]
            elif i < 6:
                tgt = s.midfielders[i - 3]
            elif i < 9:
                tgt = s.defense[i - 6]
            elif i < 11:
                tgt = b.forwards[i - 9]
            elif i < 13:
                tgt = b.midfielders[i - 11]
            elif i < 15:
                tgt = b.defense[i - 13]
            elif i == 15:
                tgt = s.goalie
            else:
                tgt = b.goalie
            tgt.current_season_log["performances"].append(float(perf))
        for reserve in self.reserves:
            reserve.current_season_log["performances"].append(0)

    def update_offensive_stats(self, goals_scored, shots_taken) -> None:
        s, b = self.starters, self.bench
        for i, (goals, shots) in enumerate(zip(goals_scored, shots_taken)):
            if i < 3:
                tgt = s.forwards[i]
            elif i < 6:
                tgt = s.midfielders[i - 3]
            elif i < 8:
                tgt = b.forwards[i - 6]
            else:
                tgt = b.midfielders[i - 8]
            tgt.current_season_log["goals"].append(int(goals))
            tgt.current_season_log["shots_taken"].append(int(shots))

        # Non-scorers get explicit zeros so every player has one entry per game.
        non_scorers = list(s.defense) + list(b.defense) + [s.goalie, b.goalie] + list(self.reserves)
        for p in non_scorers:
            p.current_season_log["goals"].append(0)
            p.current_season_log["shots_taken"].append(0)

    def update_goalie_stats(self, saves: int, goals_allowed: int) -> None:
        saves, goals_allowed = int(saves), int(goals_allowed)
        starter_saves = int(round(saves * 0.6))
        starter_ga = int(round(goals_allowed * 0.6))
        self.starters.goalie.current_season_log["saves"].append(starter_saves)
        self.starters.goalie.current_season_log["goals_allowed"].append(starter_ga)
        self.bench.goalie.current_season_log["saves"].append(saves - starter_saves)
        self.bench.goalie.current_season_log["goals_allowed"].append(goals_allowed - starter_ga)
