"""
Name: signing_service.py
Description: Free-agent signing -- picking a player out of the unsigned pool, the
    second way (alongside trade_service) a player joins a team.

    A free-agent signing is ALWAYS the same deal: FREE_AGENT_CONTRACT_YEARS at
    FREE_AGENT_CONTRACT_SALARY (1 year, $0M -- a league-minimum contract). Nothing is
    negotiated here, so the endpoint takes no term or value. The pool is how a team
    fills an empty roster spot, not a market: real money and real term are settled
    with your OWN expiring players, in the offseason re-signing process, which is a
    separate mechanism (see TODO.md).

    That makes the money side nearly trivial -- a $0 contract never counts against the
    cap, because payroll is just the sum of contract_value -- but the signing still
    goes through the full check (salary_cap.check_signing) rather than skipping it: a
    team already ABOVE the hard cap on rookie deals may not add players, only shed
    them, the same rule trades follow.

    Offseason and in-season signings are the same operation: a free agent is a
    non-retired player with no team, whenever that came about, and the rules do not
    change with the calendar. What does differ is availability -- in-season a team is
    normally at max_roster and has no spot to sign into (a player under contract
    cannot be dropped), so in practice signings happen in the offseason or to backfill
    a roster thinned by retirement. The API additionally refuses to sign while a period
    is simulating; see api/main.py.

    Shape (mirrors season_readiness.py): the RULES are pure functions over a
    SigningContext snapshot -- every eligibility decision is unit-testable with a
    hand-built context and no database -- and the SQL layer does nothing but read
    that snapshot, then write. The write is one transaction: lock the rows (two teams
    may be after the same player; the loser sees "already signed"), check, put the
    contract on the player via domain.Player.update_contract (the one validated
    contract write path), move them onto the roster, and re-derive the lineup.

    The pure layer is deliberately more general than this one fixed deal: it takes an
    arbitrary term/value and knows about Bird rights (players.rights_team_id -- the
    team a contract expired off, stamped by offseason._process_free_agency, see
    alembic 0010), because the re-signing process will price real contracts against
    exactly these rules.
Author: relational backend
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.league_views import DEFAULT_RULES, RosterRules
from handball.pg_repository import PLAYER_SCALAR_COLS
from handball.repository import _player_from_dict
from handball.roster_layout import try_rebuild_layout
from handball.salary_cap import (
    ContractError,
    cap_situation,
    check_signing,
    max_offer,
)
from handball.simulation_vars import (
    FIRST_LUXURY_TAX_THRESHOLD,
    HARD_CAP,
    MAX_CONTRACT_VALUE,
    MAX_CONTRACT_YEARS,
    MIN_CONTRACT_VALUE,
    SALARY_CAP,
    SECOND_LUXURY_TAX_THRESHOLD,
)


# The deal every free-agent signing gets: a one-year league-minimum contract. Mirrors
# postseason.ROOKIE_CONTRACT_YEARS/SALARY, the other fixed-terms write path.
FREE_AGENT_CONTRACT_YEARS = 1
FREE_AGENT_CONTRACT_SALARY = MIN_CONTRACT_VALUE      # $0M/yr -- never counts vs the cap


class SigningError(RuntimeError):
    """A signing the league's rules forbid -- an ineligible player, a full roster, or
    no cap room. Carries a sentence fit to show the manager who tried it. (Cap
    violations arrive as salary_cap.ContractError and are re-raised as this, so a
    caller has exactly one exception type to handle.)"""


# -- the facts the rules reason over -----------------------------------------
@dataclass(frozen=True)
class SigningContext:
    """Snapshot of one (team, player) signing question, read in one pass. Plain data
    so every rule below stays pure and DB-free."""
    team_id: str                    # teams.id as text -- the would-be signer
    team_name: str
    payroll: int                    # cap-counting payroll ($M/yr) BEFORE the signing
    roster_size: int                # non-retired players currently on the team
    player_id: str                  # players.legacy_id
    player_name: str
    retired: bool
    current_team_id: str | None     # None == a free agent
    rights_team_id: str | None      # who holds Bird rights (None == nobody)


def has_bird_rights(ctx: SigningContext) -> bool:
    """Whether this is a RE-signing: the player's last contract expired off this very
    team, so the soft cap doesn't bind (only the hard cap does)."""
    return ctx.rights_team_id is not None and ctx.rights_team_id == ctx.team_id


def check_signing_allowed(
    ctx: SigningContext, term: int, value: int, rules: RosterRules = DEFAULT_RULES
) -> None:
    """Raise SigningError unless this team may sign this player to this deal. Order
    matters: eligibility ("is this player even available?") before roster room before
    money, so the manager is told the disqualifying reason rather than a cap number
    for a player they could never have signed."""
    if ctx.retired:
        raise SigningError(f"{ctx.player_name} is retired and cannot be signed")
    if ctx.current_team_id is not None:
        if ctx.current_team_id == ctx.team_id:
            raise SigningError(
                f"{ctx.player_name} is already under contract with {ctx.team_name}")
        raise SigningError(
            f"{ctx.player_name} is under contract with another team and is not a free "
            f"agent; acquire them by trade")
    if ctx.roster_size >= rules.max_roster:
        raise SigningError(
            f"{ctx.team_name}'s roster is full ({ctx.roster_size}/{rules.max_roster}); "
            f"a player under contract cannot be dropped to make room")
    try:
        check_signing(ctx.payroll, term, value, own_player=has_bird_rights(ctx))
    except ContractError as e:
        raise SigningError(str(e)) from e


def offer_ceiling(ctx: SigningContext) -> int:
    """The most this team may offer THIS player per year -- the cap ceiling for the
    right kind of signing (Bird rights or outside). What the UI puts on the slider."""
    return max_offer(ctx.payroll, own_player=has_bird_rights(ctx))


# -- the database side -------------------------------------------------------
def sign_free_agent(
    engine: Engine,
    team_slug: str,
    legacy_id: str,
    *,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """Sign a free agent out of the pool, in ONE transaction. The contract is fixed --
    FREE_AGENT_CONTRACT_YEARS at FREE_AGENT_CONTRACT_SALARY, the same deal for every
    signing -- so there is nothing to pass but who and where. Raises SigningError
    (rolling back) if the rules forbid it.

    The team and player rows are locked FOR UPDATE before the context is read (see the
    lock-order comment below), so concurrent signings serialize: a second team after
    the same free agent finds them rostered and gets "under contract with another
    team". Bird rights are consumed by the signing (rights_team_id is cleared) -- they
    describe the team a contract expired off, and this contract has not expired yet.

    The lineup is rebuilt best-effort: a signing into a roster that is still short a
    position (mid-offseason, after retirements) must not be rejected for that, so the
    new player stays unplaced until the roster is whole. `placed` in the return says
    which happened."""
    from handball.free_agency import open_period_row       # lazy: free_agency imports us

    term, value = FREE_AGENT_CONTRACT_YEARS, FREE_AGENT_CONTRACT_SALARY
    with engine.begin() as conn:
        # While the offseason market is open, the pool is closed. Otherwise a manager
        # could scoop a player out from under a live auction on a $0 deal, which
        # defeats the whole mechanism; inside a period the equivalent move is a $0
        # offer, which anyone can beat.
        if open_period_row(conn) is not None:
            raise SigningError(
                "free agency is open -- make an offer through the auction instead of "
                "signing at the minimum")
        team = team_row(conn, team_slug)
        # Lock the TEAM first, then the player -- always in that order, so concurrent
        # signings can't deadlock. The team lock serializes this team's signings
        # against each other (two deals checked against the same payroll would each
        # look affordable and jointly blow the cap); the player lock serializes
        # different teams bidding on the same free agent.
        lock_team(conn, team["id"])
        prow = lock_player(conn, legacy_id)
        ctx = signing_context(conn, team, prow)
        check_signing_allowed(ctx, term, value, rules)
        signed = apply_signing(conn, ctx.team_id, prow, term, value, rules=rules)

        return {
            "player_id": legacy_id,
            "player_name": ctx.player_name,
            "team": team_slug,
            "team_name": ctx.team_name,
            "term": signed["term"],
            "value": signed["value"],
            "bird_rights": has_bird_rights(ctx),
            "payroll": ctx.payroll + signed["value"],
            "roster_size": ctx.roster_size + 1,
            "placed": signed["placed"],
        }


def apply_signing(
    conn,
    team_id: str,
    prow,
    term: int,
    value: int,
    *,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """Put `term`/`value` on an already-locked player row and move them onto the team,
    inside the CALLER's transaction. Returns {"term", "value", "placed"}.

    This is the write half of a signing, shared by the two paths that produce one: the
    fixed-terms pool deal above, and the offseason auction (handball/free_agency.py),
    where the numbers were negotiated. The only difference between them IS the numbers,
    so this is the one place a free agent becomes a rostered player.

    The caller owns everything before this point and must have: locked the team and the
    player (in that order), and checked the deal with check_signing_allowed. `prow` must
    be a row carrying every PLAYER_SCALAR_COLS column -- i.e. one from lock_player --
    because the contract goes on through the domain model rather than raw SQL:
    update_contract validates term/value, restarts years_remaining at the full term,
    and clears the rookie/restricted flags for a non-rookie deal.

    Bird rights are consumed (rights_team_id cleared): they describe the team a contract
    expired off, and this contract has not expired yet. The lineup is rebuilt
    best-effort -- a signing into a roster still short a position (mid-offseason, after
    retirements) must not fail for that, so the new player stays unplaced until the
    roster is whole."""
    player = _player_from_dict(
        {"id": prow["legacy_id"], **{c: prow[c] for c in PLAYER_SCALAR_COLS}}
    )
    player.update_contract(term, value, rookie=False)
    conn.execute(
        text("update players set team_id = cast(:team as uuid), rights_team_id = null, "
             "slot_group = null, slot_position = null, slot_order = null, "
             "contract_term = :term, contract_value = :value, "
             "years_remaining = :years, rookie_contract = :rookie, "
             "restricted_free_agent = :restricted, updated_at = now() "
             "where id = cast(:uuid as uuid)"),
        {"team": team_id, "term": player.contract_term,
         "value": player.contract_value, "years": player.years_remaining,
         "rookie": player.rookie_contract,
         "restricted": player.restricted_free_agent, "uuid": str(prow["id"])},
    )
    return {
        "term": player.contract_term,
        "value": player.contract_value,
        "placed": try_rebuild_layout(conn, team_id, rules),
    }


def load_signing_context(engine: Engine, team_slug: str, legacy_id: str) -> SigningContext:
    """Read-only form of the snapshot the write path checks -- for callers that want
    to evaluate a signing (e.g. "what can I offer this player?") without making it."""
    with engine.connect() as conn:
        team = team_row(conn, team_slug)
        prow = conn.execute(
            text("select id, legacy_id, name, team_id::text as team_id, "
                 "rights_team_id::text as rights_team_id, retired from players "
                 "where legacy_id = :lid"),
            {"lid": legacy_id},
        ).mappings().first()
        if prow is None:
            raise SigningError(f"no player {legacy_id!r}")
        return signing_context(conn, team, prow)


def team_cap_report(engine: Engine, team_slug: str, rules: RosterRules = DEFAULT_RULES) -> dict:
    """A team's cap standing plus what it may actually DO with it: roster room and the
    two offer ceilings (own free agent vs outside). Serves the signing UI and the team
    page -- one place the numbers are shaped, so the website never re-implements a cap
    rule. League constants ride along so the UI can label the thresholds without
    hardcoding them.

    `projected_next_payroll` is the same view one season forward: what the team is
    already committed to in season+1 (contracts that run past this one, plus every
    extension signed in this window). It is the number an extension is checked
    against, so the page that shows a cap must show it too, or a manager will be
    surprised by a refusal. `extension_window_open` says whether they can act on it
    today."""
    from handball.extensions import (            # lazy: extensions imports us
        projected_next_payroll, roster_snapshot, window_state,
    )

    with engine.connect() as conn:
        team = team_row(conn, team_slug)
        payroll, roster_size = payroll_and_roster(conn, team["id"])
        projected = projected_next_payroll(roster_snapshot(conn, team["id"]))
        window_open = window_state(conn).extension_window_open
    situation = cap_situation(payroll)
    return {
        "team": team_slug,
        "team_name": team["name"],
        "payroll": situation.payroll,
        "cap_room": situation.cap_room,
        "over_cap": situation.over_cap,
        "over_first_threshold": situation.over_first_threshold,
        "over_second_threshold": situation.over_second_threshold,
        "mid_level_exception": situation.mid_level_exception,
        "hard_cap_room": situation.hard_cap_room,
        "projected_next_payroll": projected,
        "extension_window_open": window_open,
        "roster_size": roster_size,
        "max_roster": rules.max_roster,
        "roster_spots": max(0, rules.max_roster - roster_size),
        "max_outside_offer": max_offer(payroll, own_player=False),
        "max_own_offer": max_offer(payroll, own_player=True),
        "limits": {
            "salary_cap": SALARY_CAP,
            "first_luxury_threshold": FIRST_LUXURY_TAX_THRESHOLD,
            "second_luxury_threshold": SECOND_LUXURY_TAX_THRESHOLD,
            "hard_cap": HARD_CAP,
            "max_contract_years": MAX_CONTRACT_YEARS,
            "max_contract_value": MAX_CONTRACT_VALUE,
            "min_contract_value": MIN_CONTRACT_VALUE,
        },
    }


# -- building blocks, shared with handball/free_agency.py ---------------------
# These take a live `conn` and do one small thing each, so the auction can compose the
# same reads, locks and checks inside its own (much longer) transactions instead of
# re-implementing them. The lock ORDER they imply -- team, then player -- is part of
# the contract; see the comment in sign_free_agent.
def team_row(conn, slug: str):
    row = conn.execute(
        text("select id::text as id, slug, name from teams where slug = :s"), {"s": slug}
    ).mappings().first()
    if row is None:
        raise SigningError(f"no team {slug!r}")
    return row


def lock_team(conn, team_id: str) -> None:
    """Hold the team row for the rest of the transaction, so this team's payroll can't
    move under a signing that is being checked against it."""
    conn.execute(
        text("select 1 from teams where id = cast(:t as uuid) for update"), {"t": team_id}
    ).first()


def lock_player(conn, legacy_id: str):
    """Read the player row and hold it for the rest of the transaction. Takes every
    scalar column because the contract write rebuilds the domain Player from it."""
    cols = ", ".join(PLAYER_SCALAR_COLS)
    row = conn.execute(
        text(f"select id, legacy_id, team_id::text as team_id, "
             f"rights_team_id::text as rights_team_id, retired, {cols} "
             "from players where legacy_id = :lid for update"),
        {"lid": legacy_id},
    ).mappings().first()
    if row is None:
        raise SigningError(f"no player {legacy_id!r}")
    return row


def signing_context(conn, team, prow) -> SigningContext:
    payroll, roster_size = payroll_and_roster(conn, team["id"])
    return SigningContext(
        team_id=team["id"],
        team_name=team["name"],
        payroll=payroll,
        roster_size=roster_size,
        player_id=prow["legacy_id"],
        player_name=prow["name"],
        retired=prow["retired"],
        current_team_id=prow["team_id"],
        rights_team_id=prow["rights_team_id"],
    )


def payroll_and_roster(conn, team_id: str) -> tuple[int, int]:
    """The team's cap-counting payroll ($M/yr) and headcount. Minimum ($0) deals
    contribute nothing to the sum, so it IS the cap number; retired players are
    excluded defensively (retirement clears team_id anyway)."""
    row = conn.execute(
        text("select coalesce(sum(contract_value), 0) as payroll, count(*) as n "
             "from players where team_id = cast(:t as uuid) and retired = false"),
        {"t": team_id},
    ).mappings().one()
    return int(row["payroll"]), int(row["n"])
