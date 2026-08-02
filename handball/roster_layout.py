"""
Name: roster_layout.py
Description: Re-deriving a team's on-field layout after its ROSTER changes -- the
    step every roster write path needs and none of them should own privately. A
    trade leaves a hole where the outgoing player sat plus an unplaced incoming
    player; a free-agent signing adds an unplaced player. Either way the
    arrangement is momentarily illegal, and domain.validate is arrangement-level,
    so the fix is to compute a canonical arrangement and validate THAT.

    canonical_team() is the whole rule: per position, the strongest healthy players
    fill starters, then bench; the remainder go to reserves. Deterministic, pure,
    and testable without a database. Managers re-tweak afterwards via the lineup API.

    Two ways to apply it, because the two callers differ on what an unarrangeable
    roster means:
      - rebuild_layout() is strict. A trade may not leave either side unable to
        field a legal lineup, so an unarrangeable result raises (rolling the trade
        back).
      - try_rebuild_layout() is best-effort, returning False instead of raising. A
        signing happens INTO rosters that are legitimately incomplete -- mid
        -offseason a team can be short a position after retirements and expiries,
        and signing is how it fills back up, so "can't be arranged yet" must not
        reject the signing. The new player simply stays unplaced until the roster is
        whole and the manager (or the next successful rebuild) slots them in.
Author: relational backend
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import text

from handball.domain import ArrangementError, Player, Team, validate
from handball.league_views import DEFAULT_RULES, RosterRules
from handball.pg_repository import _iter_slots


class RosterLayoutError(ValueError):
    """A roster that cannot be arranged into a legal lineup (a position can't be
    filled to its caps, or reserves overflow). Callers translate this into their own
    error type -- TradeError, SigningError -- since what it MEANS depends on the
    write path."""


def canonical_team(players: list[Player], rules: RosterRules = DEFAULT_RULES) -> Team:
    """Deterministic legal arrangement of `players`: per position, the strongest
    healthy players fill starters, then bench; the remainder go to reserves. Raises
    RosterLayoutError if a position can't be filled or reserves overflow."""
    by_pos: dict[str, list[Player]] = defaultdict(list)
    for p in players:
        by_pos[p.position].append(p)
    for plist in by_pos.values():
        plist.sort(key=lambda p: (p.is_injured, -(p.offense + p.defense + p.goalie_skill)))

    starters: dict[str, list[Player]] = {}
    bench: dict[str, list[Player]] = {}
    reserves: list[Player] = []
    for pos in rules.positions:
        plist = by_pos.get(pos, [])
        sc, bc = rules.starter_caps[pos], rules.bench_caps[pos]
        if len(plist) < sc + bc:
            raise RosterLayoutError(
                f"resulting roster cannot field {pos}: have {len(plist)}, need {sc + bc}")
        starters[pos] = plist[:sc]
        bench[pos] = plist[sc:sc + bc]
        reserves.extend(plist[sc + bc:])
    if len(reserves) > rules.reserve_max:
        raise RosterLayoutError(
            f"resulting roster has {len(reserves)} reserves, max {rules.reserve_max}")

    return Team(id="<layout>", name="<layout>", coaches=[],
                starters=starters, bench=bench, reserves=reserves)


def team_players(conn, team_uuid) -> list[Player]:
    """The team's roster as domain Players, with just the columns the arrangement
    rules read (skill, position, health) -- not a full repository load."""
    rows = conn.execute(
        text("select legacy_id, name, position, is_injured, offense, defense, goalie_skill "
             "from players where team_id = cast(:t as uuid)"),
        {"t": str(team_uuid)},          # accepts a UUID object or its text form
    ).mappings().all()
    return [
        Player(id=r["legacy_id"], name=r["name"], position=r["position"],
               is_injured=r["is_injured"], offense=r["offense"], defense=r["defense"],
               goalie_skill=r["goalie_skill"])
        for r in rows
    ]


def rebuild_layout(conn, team_uuid, rules: RosterRules = DEFAULT_RULES) -> None:
    """Recompute and persist a legal lineup for the team, in the caller's
    transaction. Raises RosterLayoutError (or ArrangementError from the validate
    safety net) if the roster admits no legal arrangement, so the caller's
    transaction rolls back."""
    team = canonical_team(team_players(conn, team_uuid), rules)
    validate(team.arrangement(), team, rules)      # safety net; rolls back on failure
    persist_layout(conn, team, team_uuid)


def try_rebuild_layout(conn, team_uuid, rules: RosterRules = DEFAULT_RULES) -> bool:
    """rebuild_layout, but for a roster that is ALLOWED to be incomplete: returns
    True if a legal lineup was computed and persisted, False if the roster can't be
    arranged yet (nothing written, nothing raised)."""
    try:
        rebuild_layout(conn, team_uuid, rules)
    except (RosterLayoutError, ArrangementError):
        return False
    return True


def persist_layout(conn, team: Team, team_uuid) -> None:
    """Write the team's slot columns from an already-validated arrangement.

    Clear the team's slots first, exactly as PostgresTeamRepository.save does: the
    slots are written one row at a time, and `players` has a unique index on
    (team_id, slot_group, slot_position, slot_order), so ANY rearrangement that
    moves a player into a slot its current occupant hasn't vacated yet trips the
    index mid-write. NULL slots are mutually distinct, so clearing is always safe,
    and it also leaves a player who is on the roster but not in the arrangement
    (a fresh signing on an incomplete roster) correctly unplaced."""
    conn.execute(
        text("update players set slot_group = null, slot_position = null, "
             "slot_order = null where team_id = :tid"),
        {"tid": team_uuid},
    )
    for slot_group, slot_position, slot_order, player in _iter_slots(team):
        conn.execute(
            text("update players set slot_group = cast(:g as roster_group), "
                 "slot_position = cast(:p as player_position), slot_order = :o "
                 "where legacy_id = :lid"),
            {"g": slot_group, "p": slot_position, "o": slot_order, "lid": player.id},
        )
