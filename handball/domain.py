"""
Name: domain.py
Description: The pure domain model the rest of the redesign migrates toward: full
    Player/Team objects with NO sheet/JSON awareness, plus the two operations
    that consume a manager's TeamArrangement -- validate() and
    Team.apply_arrangement().

    This is the target shape; the current players.py/teams.py converge here. It
    is deliberately offline and dependency-free so it runs and tests anywhere.

    Position keys are the canonical singular Player.position values
    ("Forward", "Midfielder", "Defense", "Goalie") everywhere -- normalizing the
    current plural/singular mix ("Forwards" vs "Defense") in trade_handler.
Author: design sketch
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from handball.league_views import (
    COACH_ROLES,
    CoachId,
    CoachTenure,
    DEFAULT_RULES,
    PlayerId,
    PlayerPublicView,
    RosterRules,
    TeamArrangement,
    TeamId,
    TeamPublicView,
)
# InjuryReport is already pure (no I/O); reuse it rather than duplicate. It is
# the one nested object the repository serializes specially.
from handball.players import InjuryReport
from handball.simulation_vars import (
    INJURY_DECLINE_MULTIPLIER,
    INJURY_GROWTH_PENALTY,
    MAX_DECLINE_RATE,
    NEW_PLAYER_MEAN,
    NEW_PLAYER_STD,
    STAT_CAP,
)


def _new_season_log() -> dict:
    return {"shots_taken": [], "goals": [], "performances": [], "saves": [], "goals_allowed": []}


@dataclass
class Player:
    """Full domain player: the complete authoritative state, with a stable `id`.

    The physical/derived fields carry sensible defaults so a Player can be
    constructed from just (id, name, position) -- the real model required all of
    them only because create_new_player always supplied them.

    `public_view()` is the single enforcement point of the hidden/public
    boundary; every field except the few it names is invisible to managers.
    """
    id: PlayerId
    name: str
    position: str

    # biographical
    age: int = 25
    years_in_league: int = 0
    height: int = 72
    weight: int = 180

    # visible stats
    offense: float = 5.0
    defense: float = 5.0
    goalie_skill: float = 0.1

    # hidden static ceilings + trajectory
    max_offense: float = 7.0
    max_defense: float = 7.0
    max_goalie_skill: float = 0.1
    variance: float = 0.5
    peak_age: int = 27
    decline_age: int = 30
    decline_rate: float = 0.15

    # injury
    is_injured: bool = False
    injury_risk: float = 0.001
    injury_log: InjuryReport = field(
        default_factory=lambda: InjuryReport(active_injury=False, injuries=[])
    )

    # contract
    contract_term: int = 0
    contract_value: int = 0          # in millions
    years_remaining: int = 0
    amount_paid: int = 0
    rookie_contract: bool = True
    restricted_free_agent: bool = True

    # accumulating state
    awards_won: list = field(default_factory=list)
    current_season_log: dict = field(default_factory=_new_season_log)

    # -- construction ------------------------------------------------------
    @classmethod
    def create_new_player(cls, id: PlayerId, name: str, position: str) -> "Player":
        """Create a fresh draft-class player with a stable `id`. Overall skill is
        sampled from N(NEW_PLAYER_MEAN, NEW_PLAYER_STD) and split into
        offense/defense by position (ported from the legacy players.Player)."""
        stats: dict = {}
        # Biographical
        stats["name"] = name
        stats["age"] = int(random.uniform(18, 23))
        stats["years_in_league"] = 0
        stats["height"] = int(round(random.normalvariate(71, 2)))
        stats["weight"] = int(round(random.normalvariate(175, 15)))

        # Overall score: sampled, floored at 0, capped at STAT_CAP.
        overall = min(STAT_CAP, max(0, random.normalvariate(NEW_PLAYER_MEAN, NEW_PLAYER_STD)))

        def split_overall(overall, is_midfielder):
            """Split the overall score into offense/defense scores."""
            std = 0.5 if is_midfielder else 1
            first_score = min(10, max(0, random.uniform(overall, std)))
            # Clamp the complement to [0, 10]; for a low overall the raw
            # complement (2*overall - first_score) can go negative.
            second_score = min(10, max(0, (2 * overall) - first_score))
            scores = [first_score, second_score]
            if is_midfielder:
                random.shuffle(scores)
            else:
                scores.sort(reverse=True)
            return scores[0], scores[1]

        # Position
        stats["position"] = position
        match position:
            case "Goalie":
                offense, defense = 0.1, 0.1
                goalie_skill = overall
            case "Defense":
                defense, offense = split_overall(overall, is_midfielder=False)
                goalie_skill = 0.1
            case "Forward":
                offense, defense = split_overall(overall, is_midfielder=False)
                goalie_skill = 0.1
            case "Midfielder":
                offense, defense = split_overall(overall, is_midfielder=True)
                goalie_skill = 0.1

        # Hard cap visible skill at 10; max scores may exceed 10 (affect growth).
        stats["offense"] = min(10.0, offense)
        stats["defense"] = min(10.0, defense)
        stats["goalie_skill"] = min(10.0, goalie_skill)

        stats["max_offense"] = max(offense, offense + random.normalvariate(2, 0.75))
        stats["max_defense"] = max(defense, defense + random.normalvariate(2, 0.75))
        stats["max_goalie_skill"] = min(10.0, max(goalie_skill, goalie_skill + random.normalvariate(2, 0.75)))

        if position == "Goalie":
            stats["max_offense"] = 0.1
            stats["max_defense"] = 0.1
        else:
            stats["max_goalie_skill"] = 0.1

        stats["variance"] = max(0, random.normalvariate(0.5, 0.5))
        stats["decline_rate"] = max(0.01, random.normalvariate(0.15, 0.1))
        stats["peak_age"] = int(random.normalvariate(25, sigma=1))
        stats["decline_age"] = stats["peak_age"] + int(random.uniform(1, 4))

        # Injury
        stats["is_injured"] = False
        stats["injury_risk"] = max(0.0005, random.normalvariate(0.001, 0.001))
        stats["injury_log"] = InjuryReport(active_injury=False, injuries=[])

        # Contract
        stats["contract_term"] = 0
        stats["contract_value"] = 0
        stats["years_remaining"] = 0
        stats["amount_paid"] = 0
        stats["rookie_contract"] = True
        stats["restricted_free_agent"] = True

        stats["current_season_log"] = _new_season_log()
        stats["awards_won"] = []

        player = cls(id=id, **stats)
        # Boost above-18 players a bit (linear branch -> no RNG consumed).
        player.update_stats(player.age - 18, rate_scale=0.25)
        return player

    # -- public projection -------------------------------------------------
    def public_view(self) -> PlayerPublicView:
        contract = f"{self.contract_term}/${self.contract_value}" + (
            " (R)" if self.rookie_contract else ""
        )
        return PlayerPublicView(
            id=self.id,
            name=self.name,
            position=self.position,
            age=self.age,
            contract=contract,
            injured=self.is_injured,
            offense=round(self.offense, 2),
            defense=round(self.defense, 2),
            goalie_skill=round(self.goalie_skill, 2),
        )

    # -- season stat rollups ----------------------------------------------
    @property
    def total_season_goals(self) -> int:
        return sum(self.current_season_log["goals"])

    @property
    def total_season_saves(self) -> int:
        return sum(self.current_season_log["saves"])

    @property
    def total_season_goals_allowed(self) -> int:
        return sum(self.current_season_log["goals_allowed"])

    @property
    def save_percentage(self) -> float:
        faced = self.total_season_saves + self.total_season_goals_allowed
        return self.total_season_saves / faced if faced else 0.0

    # -- progression / lifecycle (ported from players.Player) -------------
    def update_stats(self, years: int, rate_scale: float) -> None:
        """Progress stats: linear toward ceilings before peak, noisy at plateau,
        decay after decline_age."""
        if self.age < self.peak_age:
            span = self.peak_age - self.age
            self.offense = min(10.0, self.offense + (self.max_offense - self.offense) / span * years * rate_scale)
            self.defense = min(10.0, self.defense + (self.max_defense - self.defense) / span * years * rate_scale)
            self.goalie_skill = min(10.0, self.goalie_skill + (self.max_goalie_skill - self.goalie_skill) / span * years * rate_scale)
        elif self.age < self.decline_age - 1:
            if self.position != "Goalie":
                self.offense = min(10.0, self.offense + random.normalvariate(0.0, 0.25) * rate_scale)
                self.defense = min(10.0, self.defense + random.normalvariate(0.0, 0.25) * rate_scale)
            else:
                self.goalie_skill = min(10.0, self.goalie_skill + random.normalvariate(0.0, 0.25) * rate_scale)
        else:
            if self.position == "Goalie":
                self.goalie_skill *= (1 - self.decline_rate)
            else:
                self.offense = min(10.0, self.offense * (1 - self.decline_rate))
                self.defense = min(10.0, self.defense * (1 - self.decline_rate))

    def advance_year(self) -> None:
        """Roll the player into next season: progress, age, reset season log."""
        self.update_stats(years=1, rate_scale=1)
        self.age += 1
        self.years_in_league += 1
        self.years_remaining -= 1
        self.current_season_log = _new_season_log()

    def injure(self, year: int, injury_type: str):
        """Mark injured and log it; returns injury duration in games (or False
        if already injured)."""
        self.is_injured = True
        return self.injury_log.add(year, injury_type)

    def tick_injury(self) -> None:
        """Advance the active injury one game and sync is_injured."""
        self.injury_log.tick()
        self.is_injured = self.injury_log.active_injury

    def apply_injury_impact(self) -> None:
        """Degrade trajectory after a major injury (slow growth pre-peak, faster
        decline post-peak)."""
        if self.age < self.peak_age:
            self.max_offense = max(self.offense, self.max_offense * INJURY_GROWTH_PENALTY)
            self.max_defense = max(self.defense, self.max_defense * INJURY_GROWTH_PENALTY)
            self.max_goalie_skill = max(self.goalie_skill, self.max_goalie_skill * INJURY_GROWTH_PENALTY)
        else:
            self.decline_rate = min(MAX_DECLINE_RATE, self.decline_rate * INJURY_DECLINE_MULTIPLIER)

    def update_contract(self, contract_term: int, contract_salary: int, rookie: bool) -> None:
        self.contract_term = contract_term
        self.contract_value = contract_salary
        if not rookie:
            self.rookie_contract = False
            self.restricted_free_agent = False


@dataclass
class Coach:
    """A coach as a first-class, league-level entity (careers span teams, so a
    Coach is NOT a child of Team). `tenures` is the full career history as a list
    of CoachTenure stints; at most one is open (end_season is None) at a time --
    the coach's current post. Coaches do not affect gameplay; they are tracked.

    `id` is a stable legacy_id slug (e.g. "jane-doe"), mirroring Player.id.
    `pool_role` is the role list the coach was drafted from (the only role marker
    for an unassigned free agent, who has no open tenure). `age` may be None when
    unknown.
    """
    id: CoachId
    name: str
    age: int | None = None
    pool_role: str | None = None
    tenures: list[CoachTenure] = field(default_factory=list)

    def current_tenure(self) -> CoachTenure | None:
        """The open stint (end_season is None), or None if the coach holds no
        current post. Invariant: at most one open tenure (enforced in the DB by a
        partial unique index)."""
        for t in self.tenures:
            if t.end_season is None:
                return t
        return None

    def assign(self, team_id: TeamId, role: str, season: int) -> None:
        """Move the coach into (team_id, role) effective `season`.

        - If the open tenure already IS (team_id, role), this is a no-op -- which
          is what makes re-seeding the same post idempotent (the short-circuit
          MUST come first).
        - Otherwise any open tenure is closed at max(start_season, season - 1) and
          a new open tenure is appended. The max() guards against end < start on a
          same-season replacement (satisfying the DB CHECK end_season >= start)."""
        if role not in COACH_ROLES:
            raise ValueError(f"unknown coach role {role!r}; expected one of {COACH_ROLES}")
        cur = self.current_tenure()
        if cur is not None and cur.team_id == team_id and cur.role == role:
            return
        if cur is not None:
            end = max(cur.start_season, season - 1)
            self.tenures = [t for t in self.tenures if t is not cur]
            self.tenures.append(CoachTenure(cur.team_id, cur.role, cur.start_season, end))
        self.tenures.append(CoachTenure(team_id, role, season, None))

    def leave(self, season: int) -> None:
        """Close the coach's open tenure (if any) without opening a new one, e.g.
        the coach has left the league. End season is max(start, season - 1)."""
        cur = self.current_tenure()
        if cur is None:
            return
        end = max(cur.start_season, season - 1)
        self.tenures = [t for t in self.tenures if t is not cur]
        self.tenures.append(CoachTenure(cur.team_id, cur.role, cur.start_season, end))


class ArrangementError(ValueError):
    """Raised by validate() with the full list of problems (not just the first),
    so a manager sees everything wrong with their lineup at once."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class Team:
    id: TeamId
    name: str
    coaches: list[str]
    starters: dict[str, list[Player]]   # values ALWAYS lists (goalie -> [p]);
    bench: dict[str, list[Player]]      # kills the list|single union + isinstance
    reserves: list[Player]
    record: tuple[int, int, int] = (0, 0, 0)

    # -- reads -------------------------------------------------------------
    def roster(self) -> list[Player]:
        out: list[Player] = []
        for group in (self.starters, self.bench):
            for plist in group.values():
                out.extend(plist)
        out.extend(self.reserves)
        return out

    def get(self, pid: PlayerId) -> Player | None:
        for p in self.roster():
            if p.id == pid:
                return p
        return None

    def arrangement(self) -> TeamArrangement:
        """Current layout. After a repo.load() this is the baseline a sheet read
        is diffed against."""
        return TeamArrangement(
            starters={pos: tuple(p.id for p in plist) for pos, plist in self.starters.items()},
            bench={pos: tuple(p.id for p in plist) for pos, plist in self.bench.items()},
            reserves=tuple(p.id for p in self.reserves),
        )

    @property
    def total_salaries(self) -> int:
        return sum(p.contract_value for p in self.roster())

    def public_view(self) -> TeamPublicView:
        def proj(group: dict[str, list[Player]]) -> dict[str, list[PlayerPublicView]]:
            return {pos: [p.public_view() for p in plist] for pos, plist in group.items()}

        return TeamPublicView(
            id=self.id,
            name=self.name,
            coaches=list(self.coaches),
            starters=proj(self.starters),
            bench=proj(self.bench),
            reserves=[p.public_view() for p in self.reserves],
            record=self.record,
            total_salaries=self.total_salaries,
        )

    def record_result(self, outcome: str) -> None:
        """Apply a game outcome ("W" | "L" | "T") to the team's W-L-T record."""
        w, l, t = self.record
        if outcome == "W":
            self.record = (w + 1, l, t)
        elif outcome == "L":
            self.record = (w, l + 1, t)
        elif outcome == "T":
            self.record = (w, l, t + 1)
        else:
            raise ValueError(f"unknown outcome {outcome!r}")

    # -- per-game stat writes (consumed by the game simulator) -------------
    # The scorer index order is fixed: starters Forward/Midfielder/Defense,
    # then bench Forward/Midfielder/Defense, then the two goalies. numpy scalars
    # are cast to plain int/float so results stay JSON-serializable.
    def update_performances(self, performances) -> None:
        """Append each player's game performance to their season log; reserves
        (who do not play) get a 0."""
        s, b = self.starters, self.bench
        for i, perf in enumerate(performances):
            if i < 3:
                tgt = s["Forward"][i]
            elif i < 6:
                tgt = s["Midfielder"][i - 3]
            elif i < 9:
                tgt = s["Defense"][i - 6]
            elif i < 11:
                tgt = b["Forward"][i - 9]
            elif i < 13:
                tgt = b["Midfielder"][i - 11]
            elif i < 15:
                tgt = b["Defense"][i - 13]
            elif i == 15:
                tgt = s["Goalie"][0]
            else:
                tgt = b["Goalie"][0]
            tgt.current_season_log["performances"].append(float(perf))
        for reserve in self.reserves:
            reserve.current_season_log["performances"].append(0)

    def update_offensive_stats(self, goals_scored, shots_taken) -> None:
        """Record goals/shots for eligible scorers (starter Forward/Midfielder,
        bench Forward/Midfielder); everyone else gets an explicit 0 so every
        player has exactly one entry per game."""
        s, b = self.starters, self.bench
        for i, (goals, shots) in enumerate(zip(goals_scored, shots_taken)):
            if i < 3:
                tgt = s["Forward"][i]
            elif i < 6:
                tgt = s["Midfielder"][i - 3]
            elif i < 8:
                tgt = b["Forward"][i - 6]
            else:
                tgt = b["Midfielder"][i - 8]
            tgt.current_season_log["goals"].append(int(goals))
            tgt.current_season_log["shots_taken"].append(int(shots))

        non_scorers = (
            list(s["Defense"]) + list(b["Defense"])
            + [s["Goalie"][0], b["Goalie"][0]] + list(self.reserves)
        )
        for p in non_scorers:
            p.current_season_log["goals"].append(0)
            p.current_season_log["shots_taken"].append(0)

    def update_goalie_stats(self, saves: int, goals_allowed: int) -> None:
        """Both goalies play (halftime swap): split 60% starter / 40% bench."""
        saves, goals_allowed = int(saves), int(goals_allowed)
        starter_saves = int(round(saves * 0.6))
        starter_ga = int(round(goals_allowed * 0.6))
        self.starters["Goalie"][0].current_season_log["saves"].append(starter_saves)
        self.starters["Goalie"][0].current_season_log["goals_allowed"].append(starter_ga)
        self.bench["Goalie"][0].current_season_log["saves"].append(saves - starter_saves)
        self.bench["Goalie"][0].current_season_log["goals_allowed"].append(goals_allowed - starter_ga)

    # -- the one write path for roster layout ------------------------------
    def apply_arrangement(self, arr: TeamArrangement, rules: RosterRules = DEFAULT_RULES) -> None:
        """Reorder/retier existing players to match `arr`. Validates first and
        raises ArrangementError on any violation, leaving the team untouched.

        This is the SINGLE place lineup state changes -- manager edits, injury
        next-man-up substitutions, and post-trade placement all funnel through
        it, so the rules are enforced uniformly and the hand-indexed mutation
        scattered across the current code (and its copy-paste bugs) goes away.
        """
        validate(arr, self, rules)
        by_id = {p.id: p for p in self.roster()}
        self.starters = {pos: [by_id[i] for i in ids] for pos, ids in arr.starters.items()}
        self.bench = {pos: [by_id[i] for i in ids] for pos, ids in arr.bench.items()}
        self.reserves = [by_id[i] for i in arr.reserves]


def validate(arr: TeamArrangement, team: Team, rules: RosterRules = DEFAULT_RULES) -> None:
    """Raise ArrangementError unless `arr` is a legal layout of `team`'s exact
    roster. Collects ALL problems before raising."""
    problems: list[str] = []
    by_id = {p.id: p for p in team.roster()}

    # 1. Membership: arrangement covers exactly the current roster, no dupes,
    #    no phantom/foreign ids.
    arr_ids = arr.all_ids()
    arr_set, roster_set = set(arr_ids), set(by_id)
    if len(arr_ids) != len(arr_set):
        dupes = sorted({i for i in arr_ids if arr_ids.count(i) > 1})
        problems.append(f"player(s) placed in multiple slots: {dupes}")
    for missing in sorted(roster_set - arr_set):
        problems.append(f"rostered player {missing!r} ({by_id[missing].name}) is not placed anywhere")
    for foreign in sorted(arr_set - roster_set):
        problems.append(f"unknown player {foreign!r} is not on this team")

    # 2. Tier shape: position groups present and counts == caps.
    def check_group(label: str, group: dict[str, tuple[PlayerId, ...]], caps: dict[str, int]) -> None:
        if set(group) != set(caps):
            problems.append(f"{label} positions {sorted(group)} != expected {sorted(caps)}")
            return
        for pos, cap in caps.items():
            n = len(group[pos])
            if n != cap:
                problems.append(f"{label} {pos}: {n} player(s), expected {cap}")

    check_group("starter", arr.starters, rules.starter_caps)
    check_group("bench", arr.bench, rules.bench_caps)
    if len(arr.reserves) > rules.reserve_max:
        problems.append(f"reserves: {len(arr.reserves)}, max {rules.reserve_max}")

    # Position integrity is intentionally NOT enforced: any player may fill any
    # slot regardless of their card position (stats are intrinsic to the Player
    # and unaffected by where they are slotted). Injured players are likewise
    # allowed in any slot -- an injured player simply contributes nothing in the
    # game simulator, and the web lineup editor warns before starting one.

    if problems:
        raise ArrangementError(problems)
