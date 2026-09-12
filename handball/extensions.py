"""
Name: extensions.py
Description: Contract extensions -- the one way a team keeps a player whose deal is
    about to run out without letting them reach the market.

    What an extension IS. A promise, not a contract. In the extension window of
    season S, a team and one of its players in the LAST year of a deal agree a new
    term and value; the player plays out season S on the old salary, and at the
    S -> S+1 rollover the new deal becomes the contract. So it cannot be written into
    contract_term/contract_value (that would re-price the season already being
    played) -- it lives in players.ext_term/ext_value/ext_signed_season (alembic
    0016), where null means "no extension" and a non-null row is also the WORK QUEUE
    the rollover drains.

    The rules, all of which the commissioner settled up front:
      - The window is exactly one point in the calendar: EXTENSION_WINDOW_AFTER_PERIOD
        periods run, and no simulation in flight. Before it, nobody knows what they
        have; after it, the deadline and the playoffs are the story.
      - Only a player at EXTENSION_ELIGIBLE_YEARS_REMAINING years remaining may be
        extended, so an extension is always a decision about a player who would
        otherwise leave -- and only once (an existing ext_term disqualifies).
      - Term is 1..MAX_CONTRACT_YEARS - years_remaining: the extension tacks onto the
        year still being played, and the TOTAL remaining may not exceed the league's
        five-year maximum.
      - Bird rights apply, as they do to any re-signing of your own player: the SOFT
        cap does not bind an extension. The HARD cap does, and it binds on the payroll
        the extension actually lands in -- NEXT season's -- which is the projection
        below, not today's payroll.
      - Binding. There is no cancel endpoint: a signed extension is a contract the
        league will enforce at the rollover.

    Shape (the house pattern, as in signing_service.py and season_readiness.py): the
    RULES are pure functions over an ExtensionContext snapshot, unit-testable with a
    hand-built roster and no database; the SQL layer reads the snapshot, checks it,
    and writes. The write locks the TEAM and then the PLAYER, the same order every
    other roster write path uses (see signing_service.sign_free_agent), and reuses
    that module's locking helpers rather than growing a second set.

    The projection is the interesting number and the reason this module exists at all.
    Next season's payroll is: every player still under contract then (years_remaining
    >= 2 today, since aging ticks one off), at their salary, PLUS every extension the
    team has signed this window, at ITS salary -- including the one being checked. A
    team cannot extend four players into a hard-cap breach one $40M promise at a time,
    because each one is checked against the sum of the others.
Author: contracts
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.pg_repository import PLAYER_SCALAR_COLS
from handball.repository import _player_from_dict
from handball.salary_cap import ContractError, validate_contract
from handball.signing_service import SigningError, lock_player, lock_team, team_row
from handball.simulation_vars import (
    EXTENSION_ELIGIBLE_YEARS_REMAINING,
    EXTENSION_WINDOW_AFTER_PERIOD,
    HARD_CAP,
    MAX_CONTRACT_VALUE,
    MAX_CONTRACT_YEARS,
    MIN_CONTRACT_VALUE,
    TRADE_DEADLINE_AFTER_PERIOD,
)


class ExtensionError(RuntimeError):
    """An extension the league's rules forbid -- the window is shut, the player is not
    eligible, the term is too long, or next season's payroll would break the hard cap.
    Carries a sentence fit to show the manager who tried it, the same convention
    SigningError follows."""


# -- the facts the rules reason over -----------------------------------------
@dataclass(frozen=True)
class ExtensionCandidate:
    """One player on the extending team, as the extension rules see them. Ratings,
    slots and injuries are all irrelevant here: an extension is a money question."""
    player_id: str                  # players.legacy_id
    name: str
    position: str
    contract_term: int
    contract_value: int
    years_remaining: int
    ext_term: int | None            # None == no extension signed
    ext_value: int | None
    ext_signed_season: int | None

    @property
    def extended(self) -> bool:
        return self.ext_term is not None


@dataclass(frozen=True)
class ExtensionContext:
    """Snapshot of one team's extension situation, read in one pass: who is on the
    roster, on what, and whether the window is even open."""
    team_id: str                    # teams.id as text
    team_name: str
    season: int                     # the extension window's season (ext_signed_season)
    window_open: bool
    roster: tuple[ExtensionCandidate, ...]

    def get(self, player_id: str) -> ExtensionCandidate | None:
        for c in self.roster:
            if c.player_id == player_id:
                return c
        return None


@dataclass(frozen=True)
class WindowState:
    """Where the league calendar sits relative to the two dates this module owns.
    Both are read off the active season's periods_run, which is the only clock the
    league has."""
    season: int | None
    periods_run: int
    run_status: str | None
    extension_window_open: bool
    trade_deadline_passed: bool

    def as_dict(self) -> dict:
        return {
            "season": self.season,
            "periods_run": self.periods_run,
            "extension_window_open": self.extension_window_open,
            "trade_deadline_passed": self.trade_deadline_passed,
            "extension_window_after_period": EXTENSION_WINDOW_AFTER_PERIOD,
            "trade_deadline_after_period": TRADE_DEADLINE_AFTER_PERIOD,
        }


# -- the rules (pure) --------------------------------------------------------
def is_eligible(c: ExtensionCandidate) -> bool:
    """Whether this player may be extended at all: in the last year of their deal, and
    not already extended. Nothing about the money -- a player can be eligible and still
    unaffordable, and the manager should be told those two things separately."""
    return c.years_remaining == EXTENSION_ELIGIBLE_YEARS_REMAINING and not c.extended


def max_extension_term(c: ExtensionCandidate) -> int:
    """The longest extension this player may sign: the league maximum less the years
    still to run, because the extension tacks onto them. Zero (or less) means no term
    is available, which the eligibility test above has already ruled out."""
    return MAX_CONTRACT_YEARS - c.years_remaining


def projected_next_payroll(
    roster: tuple[ExtensionCandidate, ...] | list[ExtensionCandidate],
    *,
    extending: str | None = None,
    value: int | None = None,
) -> int:
    """What this team's cap-counting payroll will be NEXT season, given the contracts
    and extensions on the books today -- optionally including a proposed extension for
    `extending` at `value`.

    A player counts once: their extension if they have one (it REPLACES the expiring
    deal at the rollover), otherwise their current salary if that deal still runs next
    season (years_remaining >= 2 today, since aging ticks one off first). A player in
    the last year with no extension counts for nothing -- they are leaving."""
    total = 0
    for c in roster:
        ext = value if (extending is not None and c.player_id == extending) else c.ext_value
        if ext is not None:
            total += ext
        elif c.years_remaining >= 2:
            total += c.contract_value
    return total


def max_extension_value(
    roster: tuple[ExtensionCandidate, ...] | list[ExtensionCandidate], player_id: str
) -> int:
    """The largest annual salary this team may promise THIS player -- what the UI puts
    next to the input. Bird rights mean the soft cap is not in it: the binding number
    is the hard cap against NEXT season's projected payroll, with the rest of the
    team's extensions already counted."""
    committed = projected_next_payroll(roster, extending=player_id, value=None)
    return max(MIN_CONTRACT_VALUE, min(MAX_CONTRACT_VALUE, HARD_CAP - committed))


def check_extension(ctx: ExtensionContext, player_id: str, term: int, value: int) -> None:
    """Raise ExtensionError unless this team may extend this player on these terms.
    Order matters, as in check_signing_allowed: the calendar, then who the player is,
    then the term, then the money -- so the manager hears the disqualifying reason
    rather than a cap number for a deal that was never available."""
    if not ctx.window_open:
        raise ExtensionError(
            f"the extension window is closed -- extensions are signed once "
            f"{EXTENSION_WINDOW_AFTER_PERIOD} period(s) of the season have run, and "
            f"not while a period is simulating")
    c = ctx.get(player_id)
    if c is None:
        raise ExtensionError(f"{player_id} is not on {ctx.team_name}'s roster")
    if c.extended:
        raise ExtensionError(
            f"{c.name} has already signed an extension "
            f"({c.ext_term}yr/${c.ext_value}M from next season)")
    if c.years_remaining != EXTENSION_ELIGIBLE_YEARS_REMAINING:
        raise ExtensionError(
            f"{c.name} has {c.years_remaining} years left; only a player in the last "
            f"year of a contract ({EXTENSION_ELIGIBLE_YEARS_REMAINING} remaining) may "
            f"be extended")
    ceiling = max_extension_term(c)
    if not (1 <= term <= ceiling):
        raise ExtensionError(
            f"extension term {term} out of range 1..{ceiling} years -- {c.name} has "
            f"{c.years_remaining} year(s) still to run and no contract may total more "
            f"than {MAX_CONTRACT_YEARS}")
    try:
        validate_contract(term, value)
    except ContractError as e:
        raise ExtensionError(str(e)) from e
    projected = projected_next_payroll(ctx.roster, extending=player_id, value=value)
    if projected > HARD_CAP:
        raise ExtensionError(
            f"extending {c.name} at ${value}M would put {ctx.team_name}'s projected "
            f"next-season payroll at ${projected}M, over the ${HARD_CAP}M hard cap "
            f"(extensions already signed count against it)")


def describe(c: ExtensionCandidate, roster: tuple[ExtensionCandidate, ...]) -> dict:
    """One eligible player as the API serves them: the deal they are on, and the
    ceiling on the deal they could sign."""
    return {
        "player_id": c.player_id,
        "name": c.name,
        "position": c.position,
        "contract_term": c.contract_term,
        "contract_value": c.contract_value,
        "years_remaining": c.years_remaining,
        "max_term": max_extension_term(c),
        "max_value": max_extension_value(roster, c.player_id),
    }


# -- the database side -------------------------------------------------------
_WINDOW_SQL = ("select season, periods_run, run_status from season_state "
               "order by season desc limit 1")


def window_state(conn) -> WindowState:
    """Read the calendar off the active season's run cursor, inside the caller's
    transaction. No season_state row at all (a league that has never run a period)
    means the window is shut and the deadline is a long way off."""
    row = conn.execute(text(_WINDOW_SQL)).mappings().first()
    if row is None:
        return WindowState(season=None, periods_run=0, run_status=None,
                           extension_window_open=False, trade_deadline_passed=False)
    periods_run = int(row["periods_run"])
    return WindowState(
        season=int(row["season"]),
        periods_run=periods_run,
        run_status=row["run_status"],
        # A period in flight is a season being half-played; a contract signed
        # underneath it is exactly the kind of write the run would not notice.
        extension_window_open=(periods_run == EXTENSION_WINDOW_AFTER_PERIOD
                               and row["run_status"] != "running"),
        trade_deadline_passed=periods_run >= TRADE_DEADLINE_AFTER_PERIOD,
    )


def windows(engine: Engine) -> dict:
    """The two dates, for the UI: is the extension window open, has the trade deadline
    passed. One read, so a page can gate a form without asking two endpoints."""
    with engine.connect() as conn:
        return window_state(conn).as_dict()


def trade_deadline_passed(engine: Engine) -> bool:
    """Whether trades are shut for the season. Lives here rather than in
    trade_service because it is the same calendar question as the extension window,
    read off the same row -- trade_service imports this one function."""
    with engine.connect() as conn:
        return window_state(conn).trade_deadline_passed


_ROSTER_SQL = (
    "select legacy_id, name, position::text as position, contract_term, contract_value, "
    "years_remaining, ext_term, ext_value, ext_signed_season "
    "from players where team_id = cast(:t as uuid) and retired = false "
    "order by name"
)


def roster_snapshot(conn, team_id: str) -> tuple[ExtensionCandidate, ...]:
    """Every non-retired player on the team, with their contract and any extension --
    the whole snapshot the rules need, in one read. The WHOLE roster, not just the
    eligible players: the projection is a team-level number."""
    return tuple(
        ExtensionCandidate(
            player_id=r["legacy_id"], name=r["name"], position=r["position"],
            contract_term=int(r["contract_term"]), contract_value=int(r["contract_value"]),
            years_remaining=int(r["years_remaining"]),
            ext_term=None if r["ext_term"] is None else int(r["ext_term"]),
            ext_value=None if r["ext_value"] is None else int(r["ext_value"]),
            ext_signed_season=(None if r["ext_signed_season"] is None
                               else int(r["ext_signed_season"])),
        )
        for r in conn.execute(text(_ROSTER_SQL), {"t": team_id}).mappings().all()
    )


def extension_context(conn, team) -> ExtensionContext:
    state = window_state(conn)
    return ExtensionContext(
        team_id=team["id"],
        team_name=team["name"],
        season=state.season or 0,
        window_open=state.extension_window_open,
        roster=roster_snapshot(conn, team["id"]),
    )


def _team(conn, team_slug: str):
    """signing_service.team_row, with its error re-raised as ours so a caller of this
    module has exactly one exception type to handle."""
    try:
        return team_row(conn, team_slug)
    except SigningError as e:
        raise ExtensionError(str(e)) from e


def eligible_players(engine: Engine, team_slug: str) -> dict:
    """Who this team may extend and for how much, plus the extensions it has already
    signed and what they do to next season's payroll. Read-only: the answer the Roster
    page needs before anyone fills in a form."""
    with engine.connect() as conn:
        team = _team(conn, team_slug)
        ctx = extension_context(conn, team)
    return {
        "team": team_slug,
        "team_name": ctx.team_name,
        "season": ctx.season,
        "extension_window_open": ctx.window_open,
        "projected_next_payroll": projected_next_payroll(ctx.roster),
        "hard_cap": HARD_CAP,
        "max_contract_years": MAX_CONTRACT_YEARS,
        "max_contract_value": MAX_CONTRACT_VALUE,
        "players": [describe(c, ctx.roster) for c in ctx.roster if is_eligible(c)],
        "extended": [
            {"player_id": c.player_id, "name": c.name, "position": c.position,
             "ext_term": c.ext_term, "ext_value": c.ext_value,
             "ext_signed_season": c.ext_signed_season}
            for c in ctx.roster if c.extended
        ],
    }


def offer_extension(
    engine: Engine, team_slug: str, player_legacy_id: str, term: int, value: int
) -> dict:
    """Sign an extension, in ONE transaction. Raises ExtensionError (rolling back) if
    the rules forbid it.

    The team row is locked first and the player second -- the lock order every roster
    write path in this package uses, so an extension cannot deadlock against a signing
    or a trade. The team lock is what makes the projection safe: two extensions checked
    concurrently against the same projected payroll would each look affordable and
    jointly break the hard cap.

    Binding on signature. Nothing here can be undone by the manager; the rollover
    (offseason.advance_season -> _apply_extensions) turns it into the contract."""
    with engine.begin() as conn:
        team = _team(conn, team_slug)
        lock_team(conn, team["id"])
        try:
            prow = lock_player(conn, player_legacy_id)
        except SigningError as e:
            raise ExtensionError(str(e)) from e
        ctx = extension_context(conn, team)
        check_extension(ctx, player_legacy_id, term, value)
        conn.execute(
            text("update players set ext_term = :term, ext_value = :value, "
                 "ext_signed_season = :season, updated_at = now() "
                 "where id = cast(:uuid as uuid)"),
            {"term": term, "value": value, "season": ctx.season, "uuid": str(prow["id"])},
        )
        c = ctx.get(player_legacy_id)
        return {
            "player_id": player_legacy_id,
            "player_name": c.name if c else player_legacy_id,
            "team": team_slug,
            "team_name": ctx.team_name,
            "term": term,
            "value": value,
            "signed_season": ctx.season,
            "starts_season": ctx.season + 1,
            "projected_next_payroll": projected_next_payroll(
                ctx.roster, extending=player_legacy_id, value=value),
        }


# -- the rollover ------------------------------------------------------------
_APPLIED_COLS = ("contract_term", "contract_value", "years_remaining",
                 "rookie_contract", "restricted_free_agent")


def _apply_extensions(conn) -> int:
    """Turn every signed extension into the contract, inside the rollover's
    transaction. Returns how many were applied.

    Runs AFTER aging (which ticks years_remaining to 0 on the deal being replaced) and
    BEFORE free agency (which would otherwise release exactly these players) -- that
    ordering IS the feature: an extended player never reaches the market.

    The write goes through domain.Player.update_contract, the one validated contract
    write path, so the new deal restarts years_remaining at the full term and clears
    the rookie/restricted flags the way any non-rookie contract does. A player
    extended off a rookie deal is no longer a restricted free agent, because they are
    not a free agent at all. The three ext_* columns are cleared in the same statement:
    they are the work queue, and this drains it."""
    cols = ", ".join(PLAYER_SCALAR_COLS)
    rows = conn.execute(
        text(f"select id, legacy_id, ext_term, ext_value, {cols} from players "
             "where retired = false and ext_term is not null and years_remaining <= 0")
    ).mappings().all()
    updates = []
    for r in rows:
        player = _player_from_dict(
            {"id": r["legacy_id"], **{c: r[c] for c in PLAYER_SCALAR_COLS}}
        )
        player.update_contract(int(r["ext_term"]), int(r["ext_value"]), rookie=False)
        updates.append({"uuid": str(r["id"]),
                        **{c: getattr(player, c) for c in _APPLIED_COLS}})
    if updates:
        set_sql = ", ".join(f"{c} = :{c}" for c in _APPLIED_COLS)
        conn.execute(
            text(f"update players set {set_sql}, ext_term = null, ext_value = null, "
                 "ext_signed_season = null, updated_at = now() "
                 "where id = cast(:uuid as uuid)"),
            updates,
        )
    return len(updates)
