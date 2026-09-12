"""
Name: contract_admin.py
Description: The commissioner's BULK contract path -- (re)assigning term and value
    across many players at once, for the rosters that predate contracts being
    modelled at all.

    Why this exists. Players imported before the contract model arrived carry a
    `years_remaining` that was never set to anything meaningful, and every offseason
    since has ticked it down one more (domain.Player.advance_year). The counter is
    the ONLY thing offseason._process_free_agency looks at: after aging, every
    non-retired rostered player at `years_remaining <= 0` has their team cleared and
    becomes a free agent. A league whose counters were never initialised therefore
    empties every roster on the next rollover -- not as a bug in the rollover, which
    is doing exactly what it should, but because it is reading a number nobody ever
    wrote. Repairing that per player through the API is the same operation 600+
    times; this is the one that does it in a transaction.

    Shape (the same pure-rules / SQL split the rest of the package uses):
      - ContractRow is the snapshot of one player's contract facts.
      - plan_bulk_contracts() is PURE: rows + instructions -> BulkPlan. It decides
        every change, refuses the ones the rules forbid, and -- the part that makes
        it a tool rather than an UPDATE -- reports what the league looks like
        afterwards, including how many players the NEXT rollover would release.
      - apply_plan() writes it, funnelling every change through
        domain.Player.update_contract so the bulk path validates exactly as the
        single-player path does (salary_cap.validate_contract).

    Two instructions, which compose:
      - The RESTART strategy is the mass repair: a player whose counter has run out
        (`years_remaining <= 0`) has it restarted at their existing contract_term.
        Term and value are untouched, so no team's payroll moves and no cap question
        arises -- it reads the deal already on the books and gives it a start date.
      - OVERRIDES are explicit per-player (term, value) assignments, for the deals
        the commissioner wants to actually decide. These do move payroll, so they
        are held to the cap rule below.

    What this deliberately does NOT do: fix a team that is over the hard cap. Six
    teams being over is a real state with a real remedy (trade salary away, which
    salary_cap.assert_trade_hard_cap explicitly permits while over) and it is the
    team's call which players to move. A commissioner tool that quietly rewrote
    salaries to make the blocker disappear would be deciding that for them. The cap
    rule here is only that a plan may not make it WORSE -- the same rule, and the
    same function, that governs a trade.
Author: contract administration
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.domain import Player
from handball.salary_cap import ContractError, assert_trade_hard_cap, validate_contract

# The strategy name for the mass repair. Kept a constant because it crosses the API
# boundary as a string.
RESTART = "restart_expired"
STRATEGIES = (RESTART, "none")


class BulkContractError(ValueError):
    """A bulk plan the rules forbid. Carries every problem, not the first, so the
    commissioner can fix the whole request in one pass -- the same convention
    ArrangementError and SeasonNotReady already use."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems) or "bulk contract change rejected")


# -- the facts ---------------------------------------------------------------
@dataclass(frozen=True)
class ContractRow:
    """One player's contract, as the planner sees it. `team_id`/`team_name` are None
    for a free agent -- they are carried because payroll is a per-team question and
    a free agent contributes to nobody's."""
    player_id: str                  # players.legacy_id, the id every API speaks
    name: str
    team_id: str | None
    team_name: str | None
    contract_term: int
    contract_value: int
    years_remaining: int
    rookie_contract: bool
    restricted_free_agent: bool
    # A signed extension (players.ext_term, alembic 0016). An extended player's deal
    # runs out at the next rollover like anyone else's, but they are NOT released by
    # it -- offseason._apply_extensions puts them on the new contract first. Carried
    # here so the counts below stay honest about who actually leaves.
    extended: bool = False


@dataclass(frozen=True)
class Override:
    """An explicit "put this player on this deal" instruction."""
    player_id: str
    term: int
    value: int


# -- the plan ----------------------------------------------------------------
@dataclass(frozen=True)
class ContractChange:
    """One player's before -> after. `reason` says which instruction produced it, so
    a 600-row plan is still readable."""
    player_id: str
    name: str
    team_name: str | None
    reason: str
    term_before: int
    term_after: int
    value_before: int
    value_after: int
    years_before: int
    years_after: int

    def as_dict(self) -> dict:
        return {
            "player_id": self.player_id, "name": self.name,
            "team": self.team_name, "reason": self.reason,
            "term": {"before": self.term_before, "after": self.term_after},
            "value": {"before": self.value_before, "after": self.value_after},
            "years_remaining": {"before": self.years_before, "after": self.years_after},
        }


@dataclass(frozen=True)
class TeamPayrollChange:
    team_name: str
    before: int
    after: int

    def as_dict(self) -> dict:
        return {"team": self.team_name, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class BulkPlan:
    """Everything the commissioner should see before committing, and everything
    apply_plan() needs to write it.

    `expiring_next_rollover` is the number this whole tool exists for: how many
    rostered players would be released by the next /season/advance if the plan is
    applied. Aging decrements the counter and THEN releases everyone at or below
    zero, so it counts `years_remaining <= 1` -- minus anyone holding a signed
    extension, who is put on a new contract by the same rollover."""
    changes: tuple[ContractChange, ...] = ()
    payrolls: tuple[TeamPayrollChange, ...] = ()
    expiry_cohorts: dict[int, int] = field(default_factory=dict)
    expiring_next_rollover: int = 0
    expiring_before: int = 0
    unchanged: int = 0

    def as_dict(self) -> dict:
        return {
            "changes": [c.as_dict() for c in self.changes],
            "changed": len(self.changes),
            "unchanged": self.unchanged,
            "payrolls": [p.as_dict() for p in self.payrolls if p.before != p.after],
            # str keys: this crosses a JSON boundary, where int keys don't survive.
            "expiry_cohorts": {str(k): v for k, v in sorted(self.expiry_cohorts.items())},
            "expiring_next_rollover": self.expiring_next_rollover,
            "expiring_before": self.expiring_before,
        }


# -- planning (pure) ---------------------------------------------------------
def _expiring(years_remaining: int, extended: bool = False) -> bool:
    """Whether the next rollover releases a player on this counter: aging ticks it
    down one, then `<= 0` is released -- UNLESS the player has signed an extension,
    which the rollover applies before free agency ever sees them."""
    return years_remaining <= 1 and not extended


def plan_bulk_contracts(
    rows: list[ContractRow],
    *,
    strategy: str = RESTART,
    overrides: list[Override] | None = None,
) -> BulkPlan:
    """Decide every contract change, or raise BulkContractError with ALL the reasons
    it cannot be done. Pure: no database, no clock, no randomness.

    Order matters where the two instructions overlap: an override WINS over the
    restart strategy for the same player, because it is the more specific
    instruction. A player named in overrides is therefore never also restarted.
    """
    overrides = overrides or []
    if strategy not in STRATEGIES:
        raise BulkContractError([f"unknown strategy {strategy!r}; "
                                 f"expected one of {', '.join(STRATEGIES)}"])

    by_id = {r.player_id: r for r in rows}
    problems: list[str] = []

    # -- the overrides have to name real players, once each ------------------
    # Collected, not raised: the contract limits are checked further down, and a
    # request with one of each kind of mistake should come back with both rather
    # than making the commissioner discover them one round-trip at a time.
    seen: set[str] = set()
    for o in overrides:
        if o.player_id in seen:
            problems.append(f"{o.player_id}: named twice in overrides")
        seen.add(o.player_id)
        if o.player_id not in by_id:
            problems.append(f"{o.player_id}: no such player")

    # -- build the changes ---------------------------------------------------
    changes: list[ContractChange] = []
    overridden = {o.player_id: o for o in overrides}
    for row in rows:
        o = overridden.get(row.player_id)
        if o is not None:
            term, value, reason = o.term, o.value, "override"
        elif strategy == RESTART and row.years_remaining <= 0:
            # The repair: the deal on the books, started now. A term of 0 is what an
            # unmodelled import looks like and cannot be restarted into a legal
            # contract (the minimum is 1 year) -- name it rather than silently
            # inventing a length.
            if row.contract_term < 1:
                problems.append(
                    f"{row.player_id} ({row.name}): contract_term is "
                    f"{row.contract_term}, so there is no deal to restart -- give this "
                    f"player an explicit override")
                continue
            term, value, reason = row.contract_term, row.contract_value, "restart"
        else:
            continue

        if (term, value, term) == (row.contract_term, row.contract_value,
                                   row.years_remaining):
            continue                        # already exactly this deal; not a change

        # Every change is validated, whatever produced it. A restart re-uses the
        # term and value already in the database, and THIS TOOL EXISTS FOR ROWS
        # THAT PREDATE THE RULES -- so those are exactly the values that might not
        # satisfy them. Checking here rather than only in update_contract keeps the
        # plan honest: apply_bulk must not be able to fail on a plan that passed.
        try:
            validate_contract(term, value)
        except ContractError as e:
            problems.append(f"{row.player_id} ({row.name}): {e}"
                            + (" -- give this player an explicit override"
                               if reason == "restart" else ""))
            continue
        changes.append(ContractChange(
            player_id=row.player_id, name=row.name, team_name=row.team_name,
            reason=reason,
            term_before=row.contract_term, term_after=term,
            value_before=row.contract_value, value_after=value,
            years_before=row.years_remaining, years_after=term,
        ))
    if problems:
        raise BulkContractError(problems)

    # -- the cap rule, per team ----------------------------------------------
    # Only overrides can move payroll (a restart re-uses the value already there),
    # so for the mass repair every before == after and nothing below can fire.
    new_value = {c.player_id: c.value_after for c in changes}
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    names: dict[str, str] = {}
    for row in rows:
        if row.team_id is None:
            continue                        # a free agent is on nobody's payroll
        names[row.team_id] = row.team_name or row.team_id
        before[row.team_id] += row.contract_value
        after[row.team_id] += new_value.get(row.player_id, row.contract_value)
    for team_id in sorted(before, key=lambda t: names[t]):
        if before[team_id] == after[team_id]:
            # Payroll untouched. Six teams are legitimately over the hard cap today
            # (rookie deals are exempt from it), and assert_trade_hard_cap demands a
            # strict REDUCTION from a team already over -- right for a trade, wrong
            # here, where "unchanged" is not "made worse". A repair that moves no
            # money must not be blocked by a state it isn't touching.
            continue
        try:
            assert_trade_hard_cap(before[team_id], after[team_id], label=names[team_id])
        except ContractError as e:
            problems.append(str(e))
    if problems:
        raise BulkContractError(problems)

    # -- what the league looks like afterwards -------------------------------
    years_after = {c.player_id: c.years_after for c in changes}
    cohorts: Counter[int] = Counter()
    expiring = expiring_before = 0
    for row in rows:
        if row.team_id is None:
            continue                        # only a rostered player can be released
        y = years_after.get(row.player_id, row.years_remaining)
        cohorts[y] += 1
        expiring += _expiring(y, row.extended)
        expiring_before += _expiring(row.years_remaining, row.extended)

    return BulkPlan(
        changes=tuple(changes),
        payrolls=tuple(TeamPayrollChange(names[t], before[t], after[t])
                       for t in sorted(before, key=lambda t: names[t])),
        expiry_cohorts=dict(cohorts),
        expiring_next_rollover=expiring,
        expiring_before=expiring_before,
        unchanged=len(rows) - len(changes),
    )


# -- the database side -------------------------------------------------------
_ROW_SQL = (
    "select p.legacy_id, p.name, t.id::text as team_id, t.name as team_name, "
    "p.contract_term, p.contract_value, p.years_remaining, p.rookie_contract, "
    "p.restricted_free_agent, (p.ext_term is not null) as extended "
    "from players p left join teams t on t.id = p.team_id "
    "where p.retired = false order by t.name nulls last, p.name"
)


def load_contracts(conn) -> list[ContractRow]:
    """Every non-retired player's contract, rostered or not. Free agents are included
    because a commissioner may well be repairing an unsigned player's asking terms;
    they simply contribute to no team's payroll."""
    return [
        ContractRow(
            player_id=r["legacy_id"], name=r["name"],
            team_id=r["team_id"], team_name=r["team_name"],
            contract_term=int(r["contract_term"]),
            contract_value=int(r["contract_value"]),
            years_remaining=int(r["years_remaining"]),
            rookie_contract=bool(r["rookie_contract"]),
            restricted_free_agent=bool(r["restricted_free_agent"]),
            extended=bool(r["extended"]),
        )
        for r in conn.execute(text(_ROW_SQL)).mappings().all()
    ]


def plan(engine: Engine, *, strategy: str = RESTART,
         overrides: list[Override] | None = None) -> BulkPlan:
    """Dry run: what a bulk change would do, without writing anything."""
    with engine.connect() as conn:
        rows = load_contracts(conn)
    return plan_bulk_contracts(rows, strategy=strategy, overrides=overrides)


def apply_bulk(engine: Engine, *, strategy: str = RESTART,
               overrides: list[Override] | None = None) -> BulkPlan:
    """Plan and write, in ONE transaction: the rows are re-read inside it, so the
    plan that is applied is the plan for the state actually being written.

    Every change goes through domain.Player.update_contract, which restarts
    years_remaining at the full term and validates term/value -- the bulk path is
    the single-player path, run in a loop, and cannot create a deal the single-player
    path would reject.

    The UPDATE touches the three contract columns and nothing else. Notably it does
    NOT write rookie_contract or restricted_free_agent: update_contract clears both
    when told a deal is not a rookie one, and a REPAIR must not change a player's
    rookie or restricted status as a side effect of fixing their counter."""
    with engine.begin() as conn:
        rows = load_contracts(conn)
        computed = plan_bulk_contracts(rows, strategy=strategy, overrides=overrides)
        by_id = {r.player_id: r for r in rows}
        updates = []
        for change in computed.changes:
            row = by_id[change.player_id]
            player = Player(
                id=row.player_id, name=row.name, position="Forward",   # unused here
                contract_term=row.contract_term, contract_value=row.contract_value,
                years_remaining=row.years_remaining,
                rookie_contract=row.rookie_contract,
                restricted_free_agent=row.restricted_free_agent,
            )
            player.update_contract(change.term_after, change.value_after,
                                   rookie=row.rookie_contract)
            updates.append({
                "lid": row.player_id,
                "term": player.contract_term,
                "value": player.contract_value,
                "years": player.years_remaining,
            })
        if updates:
            conn.execute(
                text("update players set contract_term = :term, contract_value = :value, "
                     "years_remaining = :years, updated_at = now() "
                     "where legacy_id = :lid"),
                updates,
            )
    return computed


def audit(engine: Engine) -> dict:
    """Read-only answer to "what is the state of the league's contracts, and what
    would the next rollover do?" -- the thing to look at before deciding whether a
    bulk change is needed at all. Reported as the no-op plan (strategy "none"), so
    the numbers are computed by exactly the code that plans a real change.

    The restart preview is best-effort: a league holding a player with no term to
    restart makes that plan un-runnable, and an AUDIT is exactly where the
    commissioner should find that out -- so the reasons are reported rather than
    raised."""
    current = plan(engine, strategy="none")
    report = {
        "rostered": sum(current.expiry_cohorts.values()),
        "expiring_next_rollover": current.expiring_next_rollover,
        "expiry_cohorts": {str(k): v for k, v in sorted(current.expiry_cohorts.items())},
    }
    try:
        proposed = plan(engine, strategy=RESTART)
    except BulkContractError as e:
        return {**report, "restart_runnable": False, "restart_problems": e.problems}
    return {
        **report,
        "restart_runnable": True,
        "restart_would_change": len(proposed.changes),
        "restart_expiring_next_rollover": proposed.expiring_next_rollover,
    }
