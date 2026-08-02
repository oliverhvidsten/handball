"""
Name: standings.py
Description: The league table -- one ranking rule, used by everything that needs to
    know who finished where: the Standings page, the playoff seeding, and (reversed)
    the draft order.

    Before this, `SeasonOrchestrator.standings()` sorted on wins-then-losses and let
    Python's stable sort leave anything still tied in ALPHABETICAL order. That was
    survivable while it only set draft order. It is not survivable now that it seeds
    a bracket people look at -- and it never agreed with the PTS column the Standings
    page was already displaying.

    The rule, in order:

      1. POINTS -- 3 for a win, 1 for a tie (simulation_vars.POINTS_PER_*).
      2. HEAD-TO-HEAD, as a mini-table: among the tied teams only, count the points
         each earned in games against the others. Applied recursively -- if it splits
         {A,B,C} into {A} and {B,C}, then B vs C is re-decided by their own
         head-to-head, not by whatever they did against A.
      3. GOAL DIFFERENTIAL, then GOALS FOR, over the whole season.
      4. team id, so the order is total and deterministic. Reaching this means two
         teams tied on points, head-to-head, goal difference AND goals scored.

    Head-to-head is always available inside a conference: the schedule has every
    intra-conference pair playing 2-4 times (rivals 4, division non-rivals 3, other
    conference opponents 2). Across conferences a pair meets once, so a cross-
    conference tie can still reach step 3 -- which is fine, since nothing that
    matters ranks the two conferences against each other.

    W-L-T comes from `teams` (the canonical record the simulator maintains and the
    rollover zeroes); goals and head-to-head come from `games`, filtered to the
    season and to REGULAR-SEASON rows -- a bracket must not seed itself off its own
    results.
Author: standings + playoff seeding
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.league_views import TeamId
from handball.simulation_vars import POINTS_PER_TIE, POINTS_PER_WIN

# a's record against b, from a's point of view.
PairRecord = tuple[int, int, int]          # (wins, losses, ties)
PairRecords = dict[tuple[TeamId, TeamId], PairRecord]


@dataclass(frozen=True)
class TeamStanding:
    """One row of the league table. `goals_for`/`goals_against` are season totals;
    they default to 0 so the offline stack -- which has records but no game log --
    can build these from a Team alone and still rank by points."""

    team_id: TeamId
    wins: int = 0
    losses: int = 0
    ties: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def points(self) -> int:
        return self.wins * POINTS_PER_WIN + self.ties * POINTS_PER_TIE

    @property
    def games_played(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def goal_diff(self) -> int:
        return self.goals_for - self.goals_against


# -- the ranking rule (pure) -------------------------------------------------
def rank_teams(
    standings: list[TeamStanding], pair_records: PairRecords | None = None
) -> list[TeamId]:
    """Best -> worst, by the rule in this module's docstring. `pair_records` is
    optional: without it (the offline stack) the head-to-head step is simply skipped
    and ties fall through to goal difference."""
    pair_records = pair_records or {}
    by_id = {s.team_id: s for s in standings}

    ordered: list[TeamId] = []
    for _, group in _grouped(list(by_id), key=lambda t: -by_id[t].points):
        ordered += _break_tie(group, by_id, pair_records)
    return ordered


def _break_tie(
    group: list[TeamId], by_id: dict[TeamId, TeamStanding], pair_records: PairRecords
) -> list[TeamId]:
    """Order teams that are level on points. Head-to-head first, re-applied within
    each subgroup it separates; whatever it cannot separate goes to goal difference,
    goals for, then team id."""
    if len(group) <= 1:
        return list(group)

    buckets = _grouped(group, key=lambda t: -_head_to_head_points(t, group, pair_records))
    if len(buckets) == 1:
        # Head-to-head said nothing (all level, or no games between them).
        return sorted(
            group,
            key=lambda t: (-by_id[t].goal_diff, -by_id[t].goals_for, str(t)),
        )

    ordered: list[TeamId] = []
    for _, bucket in buckets:
        ordered += _break_tie(bucket, by_id, pair_records)   # recurse into the subgroup
    return ordered


def _head_to_head_points(
    team: TeamId, group: list[TeamId], pair_records: PairRecords
) -> int:
    """Points `team` earned against the OTHER members of `group` -- its row in the
    mini-table."""
    total = 0
    for other in group:
        if other == team:
            continue
        wins, _losses, ties = pair_records.get((team, other), (0, 0, 0))
        total += wins * POINTS_PER_WIN + ties * POINTS_PER_TIE
    return total


def _grouped(items: list, key) -> list[tuple[object, list]]:
    """Sort by `key` and collect runs of equal key: [(key, [items...]), ...]."""
    buckets: dict[object, list] = defaultdict(list)
    for item in items:
        buckets[key(item)].append(item)
    return [(k, buckets[k]) for k in sorted(buckets)]


# -- loading from Postgres ---------------------------------------------------
@dataclass
class LeagueTable:
    """A season's standings plus the head-to-head needed to break its ties."""

    standings: list[TeamStanding] = field(default_factory=list)
    pair_records: PairRecords = field(default_factory=dict)

    def ranked(self) -> list[TeamId]:
        return rank_teams(self.standings, self.pair_records)

    def by_id(self) -> dict[TeamId, TeamStanding]:
        return {s.team_id: s for s in self.standings}


def load_league_table(engine: Engine, season: int) -> LeagueTable:
    """Build the season's table on a fresh connection."""
    with engine.connect() as conn:
        return read_league_table(conn, season)


def read_league_table(conn, season: int) -> LeagueTable:
    """Build the season's table on an EXISTING connection: W-L-T from `teams`, goals
    and head-to-head from that season's REGULAR-SEASON games. Takes a connection so a
    caller already inside a transaction (the postseason seeding its Final) reads the
    same snapshot it is writing against."""
    teams = conn.execute(
        text("select slug, wins, losses, ties from teams order by slug")
    ).mappings().all()
    games = conn.execute(
        text("select th.slug as home, ta.slug as away, g.home_score, g.away_score "
             "from games g "
             "join teams th on th.id = g.home_team_id "
             "join teams ta on ta.id = g.away_team_id "
             "where g.season = :s and g.is_playoff = false "
             "and g.home_score is not null and g.away_score is not null"),
        {"s": season},
    ).mappings().all()

    goals_for: dict[TeamId, int] = defaultdict(int)
    goals_against: dict[TeamId, int] = defaultdict(int)
    pair_records: PairRecords = {}

    for g in games:
        home, away, hs, as_ = g["home"], g["away"], g["home_score"], g["away_score"]
        goals_for[home] += hs
        goals_against[home] += as_
        goals_for[away] += as_
        goals_against[away] += hs
        if hs > as_:
            _tally(pair_records, home, away, "w")
        elif as_ > hs:
            _tally(pair_records, away, home, "w")
        else:
            _tally(pair_records, home, away, "t")

    standings = [
        TeamStanding(
            team_id=t["slug"], wins=t["wins"], losses=t["losses"], ties=t["ties"],
            goals_for=goals_for[t["slug"]], goals_against=goals_against[t["slug"]],
        )
        for t in teams
    ]
    return LeagueTable(standings=standings, pair_records=pair_records)


def _tally(pair_records: PairRecords, winner: TeamId, loser: TeamId, outcome: str) -> None:
    """Record one result into both teams' side of the pair. For a tie, `winner` and
    `loser` are just the two participants."""
    w = pair_records.get((winner, loser), (0, 0, 0))
    l = pair_records.get((loser, winner), (0, 0, 0))
    if outcome == "t":
        pair_records[(winner, loser)] = (w[0], w[1], w[2] + 1)
        pair_records[(loser, winner)] = (l[0], l[1], l[2] + 1)
    else:
        pair_records[(winner, loser)] = (w[0] + 1, w[1], w[2])
        pair_records[(loser, winner)] = (l[0], l[1] + 1, l[2])


def ranked_team_ids(engine: Engine, season: int) -> list[TeamId]:
    """Best -> worst for `season`. The one ranking: playoff seeding reads it, and the
    draft order is its reverse."""
    return load_league_table(engine, season).ranked()
