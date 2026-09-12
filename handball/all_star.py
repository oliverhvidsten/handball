"""
Name: all_star.py
Description: The All-Star exhibition: turning the positional vote into two squads
    and playing them against each other once, at the break between periods 3 and 4.

    WHAT THE VOTE DECIDES. handball/voting_rules.select_all_stars turns each
    conference's per-position vote counts into a 17-man squad -- the top 3/3/3/1 by
    votes START, the rest come off the bench. That is a commissioner decision, not an
    implementation detail, so this module builds the domain Team from the VOTE order
    rather than letting roster_layout.canonical_team re-sort it by ability. What
    canonical_team is used for is the thing it is actually for: proving the 17 admit
    a legal lineup at all, before any of them reach the simulator. ALL_STAR_BALLOT is
    sized so the two agree on shape -- 5/5/5/2 names is exactly starters + bench with
    no reserves -- and domain.validate is the safety net behind both.

    WHAT IT DOES NOT TOUCH IS THE POINT. The game is written to all_star_games and
    NOWHERE else. It never reaches `games` or `player_game_lines`, because those feed
    the standings, the leaderboards and the award race, and an exhibition that
    counted toward any of them would be a bug in every one of them -- a manager's MVP
    case should not improve because the fans voted their forward into a meaningless
    game. The same reasoning the postseason uses for team records applies here to
    everything: Players are loaded fresh through the repository, mutated freely by
    the simulator, and never saved. The price of staying out of `games` is that the
    box score has no table to be joined from, so it rides on the row as jsonb.

    HOSTING alternates by season parity. It confers no advantage today (the simulator
    opens with a fair coin flip and settles overtime with another), so this is purely
    so the fixture reads differently year to year -- and it is already there to hang a
    home advantage on if one is ever wanted.

    THE GATE. Period 4 is the second half of the season, and the break is before it.
    assert_played is what /periods/run calls to refuse period 4 until the exhibition
    has actually been played, in the same spirit as season_readiness' checks: the
    league does not skip its own showcase because nobody clicked the button.
Author: voting
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.domain import Player, Team, validate
from handball.league_structure import CONFERENCES
from handball.league_views import DEFAULT_RULES
from handball.roster_layout import canonical_team
from handball.simulation_vars import ALL_STAR_BALLOT
from handball.voting import (
    ALL_STAR_KIND,
    VotingError,
    _all_star_candidates,
    _ballots_for,
    _require_open,
    _sync_status,
    conference_teams,
)
from handball.voting_rules import AllStarSelection, count_all_star_votes, select_all_stars


class AllStarError(Exception):
    """The exhibition cannot be played: the vote is not open, it has already been
    played, or a conference cannot field a legal squad."""


def home_conference(season: int) -> str:
    """Which conference hosts. Alternates by season parity."""
    return CONFERENCES[season % len(CONFERENCES)]


def _selections(conn, season: int) -> dict[str, AllStarSelection]:
    """Each conference's squad, from its ballots."""
    pools = _all_star_candidates(conn, season)
    out: dict[str, AllStarSelection] = {}
    for conference in CONFERENCES:
        ballots = _ballots_for(conn, season, ALL_STAR_KIND, conference)
        counts = count_all_star_votes(ballots)
        by_pos = pools[conference]
        names = {c["id"]: c["name"] for lst in by_pos.values() for c in lst}
        # Votes for a player who has since left the conference (a trade between the
        # ballot and the break) are dropped: they cannot play for this squad.
        eligible_ids = set(names)
        counts = {
            pos: {p: n for p, n in c.items() if p in eligible_ids}
            for pos, c in counts.items()
        }
        out[conference] = select_all_stars(
            counts,
            eligible_by_position={pos: [c["id"] for c in lst] for pos, lst in by_pos.items()},
            names=names,
        )
    return out


def _players_by_uuid(engine: Engine, conference: str) -> dict[str, Player]:
    """Every player in the conference as a full domain Player, keyed by players.id.

    Loaded through PostgresTeamRepository rather than assembled here, so an All-Star
    carries exactly the same ratings, variance and injury state the simulator sees
    in a real game. Nothing is ever saved back."""
    from handball.pg_repository import PostgresTeamRepository

    repo = PostgresTeamRepository(engine)
    slugs = conference_teams(conference)
    by_legacy: dict[str, Player] = {}
    for slug in slugs:
        try:
            team = repo.load(slug)
        except KeyError:
            continue          # a configured team with no row yet; nothing to select
        for p in team.roster():
            by_legacy[p.id] = p
    with engine.connect() as conn:
        rows = conn.execute(
            text("select p.id, p.legacy_id from players p join teams t on t.id = p.team_id "
                 "where t.slug = any(:slugs)"),
            {"slugs": slugs},
        ).all()
    return {str(pid): by_legacy[legacy] for pid, legacy in rows if legacy in by_legacy}


def _build_team(conference: str, selection: AllStarSelection,
                players: dict[str, Player]) -> Team:
    """The squad as a domain Team, in VOTE order.

    canonical_team is run first purely as the legality check: it raises if the 17
    cannot be arranged at all, which is the one failure that must not reach the
    simulator. The Team actually returned keeps the voters' starters."""
    missing = [pid for pid in selection.all_ids() if pid not in players]
    if missing:
        raise AllStarError(
            f"{conference}: {len(missing)} selected player(s) are no longer rostered "
            "in this conference"
        )
    chosen = [players[pid] for pid in selection.all_ids()]
    canonical_team(chosen, DEFAULT_RULES)          # legality, not arrangement

    team = Team(
        id=conference,
        name=f"{conference} All-Stars",
        coaches=[],
        starters={pos: [players[pid] for pid in ids]
                  for pos, ids in selection.starters.items()},
        bench={pos: [players[pid] for pid in ids] for pos, ids in selection.bench.items()},
        reserves=[],
    )
    validate(team.arrangement(), team, DEFAULT_RULES)   # safety net
    return team


def _roster_doc(selection: AllStarSelection, players: dict[str, Player],
                conference: str) -> dict:
    """What gets stored as home_roster / away_roster: WHO was selected and how, with
    the vote behind each name. Separate from the box score, which is what they did."""
    def side(group: dict[str, list[str]], slot: str) -> list[dict]:
        out = []
        for pos in ALL_STAR_BALLOT:
            for pid in group.get(pos, []):
                p = players[pid]
                out.append({"player_id": pid, "legacy_id": p.id, "name": p.name,
                            "position": pos, "slot": slot,
                            "votes": selection.votes.get(pid, 0)})
        return out

    return {"conference": conference,
            "players": side(selection.starters, "starter") + side(selection.bench, "bench")}


def _box_score(team: Team, lines: dict, roster: dict) -> list[dict]:
    """Per-player lines for one side, in roster (starters-then-bench) order."""
    slot_of = {r["legacy_id"]: r["slot"] for r in roster["players"]}
    votes_of = {r["legacy_id"]: r["votes"] for r in roster["players"]}
    out = []
    for r in roster["players"]:
        p = team.get(r["legacy_id"])
        if p is None:
            continue
        line = lines.get(p.id, {})
        out.append({
            "legacy_id": p.id, "name": p.name, "position": p.position,
            "slot": slot_of.get(p.id), "votes": votes_of.get(p.id, 0),
            "goals": int(line.get("goals", 0)), "shots": int(line.get("shots", 0)),
            "saves": int(line.get("saves", 0)),
            "goals_allowed": int(line.get("goals_allowed", 0)),
            "performance": float(line.get("performance") or 0.0),
        })
    return out


def played(engine: Engine, season: int) -> bool:
    with engine.connect() as conn:
        n = conn.execute(
            text("select count(*) from all_star_games where season = :s"), {"s": season}
        ).scalar_one()
    return n > 0


def assert_played(engine: Engine, season: int) -> None:
    """Raise unless the season's All-Star game has been played. Called by
    /periods/run before period 4 -- the break falls before the second half."""
    if not played(engine, season):
        raise AllStarError(
            "play the All-Star game before running the second half of the season"
        )


def play(engine: Engine, season: int, *, game_engine=None) -> dict:
    """Select both squads from the vote, play ONE exhibition, and store it.

    The simulation runs OUTSIDE a transaction and the row is written after it: a
    game is real compute, and holding a write transaction open across it buys
    nothing (there is exactly one All-Star game per season, guarded by a unique
    index, so there is no race to lose)."""
    from handball.orchestration import GameSimulatorAdapter

    with engine.begin() as conn:
        _sync_status(conn, season)
        # Asked BEFORE the phase check: playing the game is what closes the vote, so
        # a second attempt would otherwise be reported as "the vote is already
        # counted", which is true and unhelpful.
        if conn.execute(
            text("select count(*) from all_star_games where season = :s"), {"s": season}
        ).scalar_one():
            raise AllStarError(f"the {season} All-Star game has already been played")
        _require_open(conn, season, ALL_STAR_KIND)
        selections = _selections(conn, season)

    home_conf = home_conference(season)
    away_conf = next(c for c in CONFERENCES if c != home_conf)

    squads: dict[str, tuple[Team, dict, dict]] = {}
    for conference in (home_conf, away_conf):
        players = _players_by_uuid(engine, conference)
        team = _build_team(conference, selections[conference], players)
        roster = _roster_doc(selections[conference], players, conference)
        squads[conference] = (team, players, roster)

    home_team, _, home_roster = squads[home_conf]
    away_team, _, away_roster = squads[away_conf]

    engine_impl = game_engine or GameSimulatorAdapter(allow_tie=False)
    result = engine_impl.play(home_team, away_team)

    box = {
        "home": {"conference": home_conf, "score": result.home_score,
                 "players": _box_score(home_team, result.player_lines, home_roster)},
        "away": {"conference": away_conf, "score": result.away_score,
                 "players": _box_score(away_team, result.player_lines, away_roster)},
    }

    with engine.begin() as conn:
        conn.execute(
            text("insert into all_star_games (season, home_conference, away_conference, "
                 "home_score, away_score, went_to_overtime, home_roster, away_roster, "
                 "box_score) values (:s, :hc, :ac, :hs, :as_, :ot, cast(:hr as jsonb), "
                 "cast(:ar as jsonb), cast(:box as jsonb))"),
            {"s": season, "hc": home_conf, "ac": away_conf,
             "hs": result.home_score, "as_": result.away_score,
             "ot": bool(result.went_to_overtime),
             "hr": json.dumps(home_roster), "ar": json.dumps(away_roster),
             "box": json.dumps(box)},
        )
        conn.execute(
            text("update voting_status set status = 'tallied', closed_at = now() "
                 "where season = :s and kind = :k"),
            {"s": season, "k": ALL_STAR_KIND},
        )
    return game(engine, season) or {}


def game(engine: Engine, season: int) -> dict | None:
    """The stored exhibition for a season, or None. What the Awards page renders."""
    with engine.connect() as conn:
        row = conn.execute(
            text("select season, played_at, home_conference, away_conference, home_score, "
                 "away_score, went_to_overtime, scoring_log, home_roster, away_roster, "
                 "box_score from all_star_games where season = :s"),
            {"s": season},
        ).mappings().first()
    if row is None:
        return None
    d = dict(row)
    d["played_at"] = d["played_at"].isoformat() if d["played_at"] else None
    return d


def seasons(engine: Engine) -> list[int]:
    with engine.connect() as conn:
        return [
            int(r[0]) for r in conn.execute(
                text("select season from all_star_games order by season desc")
            ).all()
        ]


__all__ = [
    "AllStarError", "VotingError", "assert_played", "game", "home_conference",
    "play", "played", "seasons",
]
