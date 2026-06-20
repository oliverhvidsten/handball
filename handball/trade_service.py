"""
Name: trade_service.py
Description: Player/pick trades between two teams -- the write path the website
    drives. A trade is a small state machine persisted in the `trades` table:

        proposed --accept--> accepted --approve--> committed
            |  \\--reject--> rejected
            \\----cancel--> cancelled        (from proposed or accepted)

    approve_trade() is the commissioner action AND the transactional commit: in
    ONE Postgres transaction it moves the players/picks, rebuilds a valid lineup
    for both teams, validates, and flips status to committed. Anything illegal
    (e.g. a team left unable to field a legal roster) raises and rolls the whole
    trade back -- the swap is all-or-nothing.

    Why rebuild the lineup: a swap leaves a hole where the outgoing player sat and
    an unplaced incoming player, so the arrangement is momentarily illegal.
    domain.validate is arrangement-level, so we first compute a canonical
    arrangement (best-by-position into starters -> bench -> reserves), then
    validate it. Managers can re-tweak afterwards via the lineup API.
Author: relational backend
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.db import get_engine
from handball.domain import ArrangementError, Player, Team, validate
from handball.league_views import DEFAULT_RULES, RosterRules
from handball.pg_repository import _iter_slots


class TradeError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Proposal + lifecycle
# --------------------------------------------------------------------------
def propose_trade(
    engine: Engine,
    from_team: str,
    to_team: str,
    *,
    players_out: Iterable[str] = (),
    players_in: Iterable[str] = (),
    picks_out: Iterable[str] = (),
    picks_in: Iterable[str] = (),
    proposed_by: str | None = None,
    internal: bool = False,
) -> str:
    """Create a trade. Assets are described from the proposing (from_team) side:
    *_out leave from_team for to_team; *_in come back the other way. players_* are
    legacy_ids; picks_* are draft_picks ids. Returns trade id.

    `internal` (both teams owned by the proposer): the trade skips counterparty
    acceptance and is created already 'accepted', but -- like every trade -- only
    commits once the commissioner approves it."""
    with engine.begin() as conn:
        from_id = _team_uuid(conn, from_team)
        to_id = _team_uuid(conn, to_team)
        if from_id == to_id:
            raise TradeError("cannot trade a team with itself")

        status = "accepted" if internal else "proposed"
        trade_id = conn.execute(
            text("insert into trades (from_team_id, to_team_id, status, internal, proposed_by) "
                 "values (:f, :t, :s, :internal, cast(:by as uuid)) returning id"),
            {"f": from_id, "t": to_id, "s": status, "internal": internal, "by": proposed_by},
        ).scalar_one()

        # 'to_to' => asset ends up on to_team; 'to_from' => ends up on from_team.
        for lid in players_out:
            _add_player_asset(conn, trade_id, "to_to", lid)
        for lid in players_in:
            _add_player_asset(conn, trade_id, "to_from", lid)
        for pid in picks_out:
            _add_pick_asset(conn, trade_id, "to_to", pid)
        for pid in picks_in:
            _add_pick_asset(conn, trade_id, "to_from", pid)

        if not conn.execute(
            text("select 1 from trade_assets where trade_id = :t limit 1"), {"t": trade_id}
        ).first():
            raise TradeError("a trade must move at least one asset")
        return str(trade_id)


def accept_trade(engine: Engine, trade_id: str) -> None:
    _transition(engine, trade_id, frm=("proposed",), to="accepted")


def reject_trade(engine: Engine, trade_id: str) -> None:
    _transition(engine, trade_id, frm=("proposed",), to="rejected", resolve=True)


def cancel_trade(engine: Engine, trade_id: str) -> None:
    _transition(engine, trade_id, frm=("proposed", "accepted"), to="cancelled", resolve=True)


def approve_trade(engine: Engine, trade_id: str, rules: RosterRules = DEFAULT_RULES) -> None:
    """Commissioner approval + transactional commit. accepted -> committed.
    Raises TradeError / ArrangementError (rolling back) if the swap would leave
    either team with an illegal roster."""
    with engine.begin() as conn:
        row = conn.execute(
            text("select status, from_team_id, to_team_id from trades where id = cast(:id as uuid)"),
            {"id": trade_id},
        ).mappings().first()
        if row is None:
            raise TradeError(f"no trade {trade_id!r}")
        if row["status"] != "accepted":
            raise TradeError(f"can only approve an accepted trade (is {row['status']!r})")

        from_id, to_id = row["from_team_id"], row["to_team_id"]
        assets = conn.execute(
            text("select direction, player_id, draft_pick_id from trade_assets "
                 "where trade_id = cast(:id as uuid)"),
            {"id": trade_id},
        ).mappings().all()

        # 1. move assets. Players land unplaced (slots cleared) on the destination.
        for a in assets:
            dest = to_id if a["direction"] == "to_to" else from_id
            if a["player_id"] is not None:
                conn.execute(
                    text("update players set team_id = :d, slot_group = null, "
                         "slot_position = null, slot_order = null where id = :p"),
                    {"d": dest, "p": a["player_id"]},
                )
            else:
                conn.execute(
                    text("update draft_picks set holder_team_id = :d where id = :p"),
                    {"d": dest, "p": a["draft_pick_id"]},
                )

        # 2. rebuild + validate + persist a legal lineup for both teams.
        _rearrange_team(conn, from_id, rules)
        _rearrange_team(conn, to_id, rules)

        # 3. commit.
        conn.execute(
            text("update trades set status = 'committed', resolved_at = now() "
                 "where id = cast(:id as uuid)"),
            {"id": trade_id},
        )


def get_trade_status(engine: Engine, trade_id: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text("select status from trades where id = cast(:id as uuid)"), {"id": trade_id}
        ).first()
    if row is None:
        raise TradeError(f"no trade {trade_id!r}")
    return row[0]


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------
def _transition(engine, trade_id, *, frm: tuple[str, ...], to: str, resolve: bool = False):
    with engine.begin() as conn:
        row = conn.execute(
            text("select status from trades where id = cast(:id as uuid) for update"),
            {"id": trade_id},
        ).first()
        if row is None:
            raise TradeError(f"no trade {trade_id!r}")
        if row[0] not in frm:
            raise TradeError(f"cannot move trade from {row[0]!r} to {to!r}")
        resolved = ", resolved_at = now()" if resolve else ""
        conn.execute(
            text(f"update trades set status = :to{resolved} where id = cast(:id as uuid)"),
            {"to": to, "id": trade_id},
        )


def _rearrange_team(conn, team_uuid, rules: RosterRules) -> None:
    rows = conn.execute(
        text("select legacy_id, name, position, is_injured, offense, defense, goalie_skill "
             "from players where team_id = :t"),
        {"t": team_uuid},
    ).mappings().all()
    players = [
        Player(id=r["legacy_id"], name=r["name"], position=r["position"],
               is_injured=r["is_injured"], offense=r["offense"], defense=r["defense"],
               goalie_skill=r["goalie_skill"])
        for r in rows
    ]
    team = _canonical_team(players, rules)
    validate(team.arrangement(), team, rules)        # safety net; rolls back on failure
    for slot_group, slot_position, slot_order, player in _iter_slots(team):
        conn.execute(
            text("update players set slot_group = cast(:g as roster_group), "
                 "slot_position = cast(:p as player_position), slot_order = :o "
                 "where legacy_id = :lid"),
            {"g": slot_group, "p": slot_position, "o": slot_order, "lid": player.id},
        )


def _canonical_team(players: list[Player], rules: RosterRules) -> Team:
    """Deterministic legal arrangement: per position, the strongest healthy
    players fill starters, then bench; the remainder go to reserves. Raises
    TradeError if a position can't be filled or reserves overflow."""
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
            raise TradeError(f"resulting roster cannot field {pos}: have {len(plist)}, need {sc + bc}")
        starters[pos] = plist[:sc]
        bench[pos] = plist[sc:sc + bc]
        reserves.extend(plist[sc + bc:])
    if len(reserves) > rules.reserve_max:
        raise TradeError(f"resulting roster has {len(reserves)} reserves, max {rules.reserve_max}")

    return Team(id="<trade-eval>", name="<trade-eval>", coaches=[],
                starters=starters, bench=bench, reserves=reserves)


def _team_uuid(conn, slug: str):
    row = conn.execute(text("select id from teams where slug = :s"), {"s": slug}).first()
    if row is None:
        raise TradeError(f"no team {slug!r}")
    return row[0]


def _add_player_asset(conn, trade_id, direction: str, legacy_id: str) -> None:
    pid = conn.execute(
        text("select id from players where legacy_id = :lid"), {"lid": legacy_id}
    ).first()
    if pid is None:
        raise TradeError(f"no player {legacy_id!r}")
    conn.execute(
        text("insert into trade_assets (trade_id, direction, player_id) "
             "values (:t, :d, :p)"),
        {"t": trade_id, "d": direction, "p": pid[0]},
    )


def _add_pick_asset(conn, trade_id, direction: str, pick_id: str) -> None:
    conn.execute(
        text("insert into trade_assets (trade_id, direction, draft_pick_id) "
             "values (:t, :d, cast(:p as uuid))"),
        {"t": trade_id, "d": direction, "p": pick_id},
    )
