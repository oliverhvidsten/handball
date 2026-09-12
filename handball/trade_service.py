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
    an unplaced incoming player, so the arrangement is momentarily illegal. The
    canonical-arrangement rules live in roster_layout (shared with free-agent
    signing); a trade applies them STRICTLY -- a swap that leaves either side unable
    to field a legal lineup is rejected. Managers can re-tweak afterwards via the
    lineup API.
Author: relational backend
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.db import get_engine
from handball.league_views import DEFAULT_RULES, RosterRules
from handball.roster_layout import RosterLayoutError, rebuild_layout
from handball.salary_cap import ContractError, assert_trade_hard_cap


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
    picks_out: Iterable[str | dict] = (),
    picks_in: Iterable[str | dict] = (),
    proposed_by: str | None = None,
    internal: bool = False,
) -> str:
    """Create a trade. Assets are described from the proposing (from_team) side:
    *_out leave from_team for to_team; *_in come back the other way. players_* are
    legacy_ids; picks_* are draft_picks ids, each either a plain id (unprotected) or
    `{"pick_id": ..., "protection_top_n": N}` -- a protection two teams agree to at
    trade time, carried on the pick until the lottery resolves it (see alembic
    0014's docstring for why the condition lives on draft_picks rather than being
    derived from the trade that created it). Returns trade id.

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
            text("select direction, player_id, draft_pick_id, protection_top_n "
                 "from trade_assets where trade_id = cast(:id as uuid)"),
            {"id": trade_id},
        ).mappings().all()

        # payrolls BEFORE the swap: the hard-cap rule for a trade is comparative (a
        # team already over the cap on rookie deals may trade DOWN but not up), so
        # both sides are needed. See salary_cap.assert_trade_hard_cap.
        payroll_before = {tid: _payroll(conn, tid) for tid in (from_id, to_id)}

        # 1. move assets. Players land unplaced (slots cleared) on the destination.
        #    A pick's protection is exactly what THIS trade agreed to carry it as --
        #    trade_assets.protection_top_n is copied onto the pick whether it's set
        #    or null, and protection_outcome resets: a freshly-traded condition
        #    hasn't been through the lottery yet (that resolution is the draft
        #    agent's job, at draw time -- not here).
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
                    text("update draft_picks set holder_team_id = :d, "
                         "protection_top_n = :prot, protection_outcome = null "
                         "where id = :p"),
                    {"d": dest, "p": a["draft_pick_id"], "prot": a["protection_top_n"]},
                )

        # 2. no team may finish a trade over the hard cap (players keep their
        #    contracts, so each side's payroll is a pure sum over its new roster).
        _assert_hard_cap(conn, from_id, payroll_before[from_id], "from_team")
        _assert_hard_cap(conn, to_id, payroll_before[to_id], "to_team")

        # 3. rebuild + validate + persist a legal lineup for both teams.
        _rearrange_team(conn, from_id, rules)
        _rearrange_team(conn, to_id, rules)

        # 4. commit.
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


def _payroll(conn, team_uuid) -> int:
    """A team's cap-counting payroll ($M/yr). Minimum ($0) contracts contribute
    nothing, so this sum IS the cap number."""
    return int(conn.execute(
        text("select coalesce(sum(contract_value), 0) from players where team_id = :t"),
        {"t": team_uuid},
    ).scalar_one())


def _assert_hard_cap(conn, team_uuid, payroll_before: int, label: str) -> None:
    """Raise TradeError unless the team's post-swap payroll obeys the hard cap. A team
    already over it (only rookie draft deals can do that) may still trade, provided
    the trade brings its payroll down -- otherwise it could never get back into
    compliance, and compliance is what lets the season start."""
    try:
        assert_trade_hard_cap(payroll_before, _payroll(conn, team_uuid), label=label)
    except ContractError as e:
        raise TradeError(str(e)) from e


def _rearrange_team(conn, team_uuid, rules: RosterRules) -> None:
    """Recompute + persist a legal lineup for one side of the swap. A roster that
    admits no legal arrangement is a TradeError (the trade is what broke it), so the
    shared layout failure is re-labelled here."""
    try:
        rebuild_layout(conn, team_uuid, rules)
    except RosterLayoutError as e:
        raise TradeError(str(e)) from e


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


def _add_pick_asset(conn, trade_id, direction: str, pick: str | dict) -> None:
    """`pick` is either a plain draft_picks id, or {"pick_id", "protection_top_n"}
    for a protected pick. Only a round-1 pick may carry one (a protection makes
    sense only on the picks whose value swings on the lottery), and only
    1-32 is a legal top-N."""
    if isinstance(pick, dict):
        pick_id = pick.get("pick_id")
        protection = pick.get("protection_top_n")
    else:
        pick_id = pick
        protection = None
    if protection is not None:
        round_row = conn.execute(
            text("select round from draft_picks where id = cast(:p as uuid)"), {"p": pick_id}
        ).first()
        if round_row is None:
            raise TradeError(f"no pick {pick_id!r}")
        if round_row[0] != 1:
            raise TradeError("only a round-1 pick may carry a protection")
        if not (1 <= protection <= 32):
            raise TradeError("a pick protection must be between 1 and 32")
    conn.execute(
        text("insert into trade_assets (trade_id, direction, draft_pick_id, protection_top_n) "
             "values (:t, :d, cast(:p as uuid), :prot)"),
        {"t": trade_id, "d": direction, "p": pick_id, "prot": protection},
    )
