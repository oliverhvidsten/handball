"""
Name: season_readiness.py
Description: The league's preconditions for STARTING a season -- the "no games can be
    played until this is fixed" list. Today: no team above the hard cap, the draft
    finished, free agency settled, and every team able to field a legal lineup. It is
    built as a registry so the list can grow without the API, the UI, or the run
    controls changing at all:
    write a new check function, decorate it, and it shows up everywhere readiness is
    reported.

    Shape:
      - LeagueState is a snapshot of the facts checks reason over, read from the DB
        once by load_league_state(). When a new check needs facts the snapshot
        doesn't carry, add a field there and populate it in that one function.
      - A check is a PURE function LeagueState -> list[Blocker], registered with
        @readiness_check(name, description). Pure means every check is unit-testable
        with a hand-built LeagueState and no database.
      - blockers(state) runs the whole registry; a season is ready iff it comes back
        empty. assert_season_can_start() is the enforcement point (the API calls it
        before the first period of a season) and raises SeasonNotReady, whose
        .blockers carries every reason at once -- the commissioner should see the
        full list, not the first problem.

    Why the hard cap lives here rather than as a hard error at write time: rookie
    draft contracts are exempt from the cap (a team must be able to sign its picks),
    so sitting over the hard cap during the offseason is a LEGAL state, not a bug to
    reject. It just may not survive into a season -- the team has to trade or shed
    salary first. See handball/salary_cap.py for the rules themselves.
Author: season readiness
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.league_views import DEFAULT_RULES, RosterRules
from handball.salary_cap import HARD_CAP, hard_cap_overage


# -- the facts checks reason over --------------------------------------------
@dataclass(frozen=True)
class TeamPayroll:
    """One team's cap-counting payroll ($M/yr). Minimum ($0) deals contribute
    nothing, so a plain sum of contract_value IS the cap number."""
    team_id: str            # teams.id, as text
    slug: str
    name: str
    payroll: int


@dataclass(frozen=True)
class OpenFreeAgency:
    """An offseason market that is still running (handball/free_agency.py). Present
    only while a period is open; None means free agency is settled."""
    season: int
    round_number: int
    round_status: str
    live_auctions: int


@dataclass(frozen=True)
class UnfinishedDraft:
    """A draft for this season that has not finished (handball/draft.py). Present
    only while one is outstanding; None means either the draft is complete or this
    league has no draft for the season at all -- leagues that predate the draft, and
    every offline fixture, must not be blocked by a phase they never had."""
    season: int
    status: str


@dataclass(frozen=True)
class TeamRoster:
    """One team's roster SHAPE -- the counts the arrangement rules actually read.

    `by_position` counts non-retired players per position; `unplaced` is how many of
    them sit on the roster with no slot (slot_group is null). That is enough to decide
    everything canonical_team() decides, without loading a single Player: it fills
    each position's starters then bench from the players at that position, so a
    position is fillable iff it has starter_cap + bench_cap bodies, and everyone left
    over lands in reserves."""
    team_id: str
    slug: str
    name: str
    by_position: dict[str, int] = field(default_factory=dict)
    unplaced: int = 0

    def shortfalls(self, rules: RosterRules) -> list[tuple[str, int, int]]:
        """(position, have, need) for every position that cannot be filled, in the
        rules' position order."""
        out = []
        for pos in rules.positions:
            have = self.by_position.get(pos, 0)
            need = rules.starter_caps[pos] + rules.bench_caps[pos]
            if have < need:
                out.append((pos, have, need))
        return out

    def reserves(self, rules: RosterRules) -> int:
        """How many players would land in reserves -- the surplus at each position
        summed. A position that is short contributes nothing, so this matches what
        canonical_team() would produce."""
        return sum(
            max(0, self.by_position.get(pos, 0)
                   - rules.starter_caps[pos] - rules.bench_caps[pos])
            for pos in rules.positions
        )


@dataclass(frozen=True)
class LeagueState:
    """Everything the readiness checks look at, read in one pass. Grow this as
    checks are added; keep it plain data so the checks stay pure."""
    season: int
    payrolls: tuple[TeamPayroll, ...] = ()
    free_agency: OpenFreeAgency | None = None
    rosters: tuple[TeamRoster, ...] = ()
    draft: UnfinishedDraft | None = None


# -- findings ----------------------------------------------------------------
@dataclass(frozen=True)
class Blocker:
    """One reason the season cannot start. `subject` is the thing that has to change
    (usually a team name) and `message` is the sentence shown to the commissioner --
    it should say what to do, not just what's wrong. `detail` carries the machine
    -readable numbers behind it for any UI that wants to render them."""
    check: str
    subject: str
    message: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "subject": self.subject,
            "message": self.message,
            "detail": dict(self.detail),
        }


class SeasonNotReady(RuntimeError):
    """Raised by assert_season_can_start(). Carries EVERY blocker, so the caller can
    show the commissioner the whole list in one go."""

    def __init__(self, blockers: list[Blocker]) -> None:
        self.blockers = blockers
        super().__init__("; ".join(b.message for b in blockers) or "season not ready")

    @property
    def problems(self) -> list[str]:
        """The messages alone -- matches the ArrangementError.problems convention the
        API and website already use for multi-reason failures."""
        return [b.message for b in self.blockers]


# -- the registry ------------------------------------------------------------
CheckFn = Callable[[LeagueState], list[Blocker]]


@dataclass(frozen=True)
class ReadinessCheck:
    name: str            # stable slug, also the Blocker.check tag
    description: str     # what passing looks like, in one line (shown in the UI)
    fn: CheckFn


_CHECKS: list[ReadinessCheck] = []


def readiness_check(name: str, description: str) -> Callable[[CheckFn], CheckFn]:
    """Register a season-start check. Order of registration is the order blockers are
    reported in, so declare the checks in the order a commissioner should act on
    them. Names must be unique -- they identify the check to the UI."""
    def decorate(fn: CheckFn) -> CheckFn:
        if any(c.name == name for c in _CHECKS):
            raise ValueError(f"duplicate readiness check {name!r}")
        _CHECKS.append(ReadinessCheck(name=name, description=description, fn=fn))
        return fn
    return decorate


def checks() -> tuple[ReadinessCheck, ...]:
    """Every registered check, in registration order."""
    return tuple(_CHECKS)


def blockers(state: LeagueState) -> list[Blocker]:
    """Run every check against the snapshot. Empty == the season may start."""
    found: list[Blocker] = []
    for check in _CHECKS:
        found.extend(check.fn(state))
    return found


def assert_ready(state: LeagueState) -> None:
    """Pure form of the gate: raise SeasonNotReady if anything blocks the season."""
    found = blockers(state)
    if found:
        raise SeasonNotReady(found)


# -- checks ------------------------------------------------------------------
@readiness_check(
    "hard_cap",
    f"No team's payroll is above the ${HARD_CAP}M hard cap.",
)
def _hard_cap_compliance(state: LeagueState) -> list[Blocker]:
    """Rookie draft contracts can legally push a team past the hard cap, but no team
    may OPEN a season there. The fix is the team's: trade salary away (a payroll
    -reducing trade is allowed even while over) or release players."""
    out: list[Blocker] = []
    for team in sorted(state.payrolls, key=lambda t: -hard_cap_overage(t.payroll)):
        over = hard_cap_overage(team.payroll)
        if over <= 0:
            break                      # sorted worst-first: the rest are compliant
        out.append(Blocker(
            check="hard_cap",
            subject=team.name,
            message=(f"{team.name} is ${over}M over the ${HARD_CAP}M hard cap "
                     f"(payroll ${team.payroll}M) and must shed salary before the "
                     f"season can start."),
            detail={"team_id": team.team_id, "slug": team.slug,
                    "payroll": team.payroll, "hard_cap": HARD_CAP, "over_by": over},
        ))
    return out


@readiness_check(
    "draft_complete",
    "The draft is finished -- every pick has been made.",
)
def _draft_finished(state: LeagueState) -> list[Blocker]:
    """A season cannot start with picks still on the board. Draftees are rostered
    players on rookie contracts, so an unfinished draft means teams opening the year
    short of the bodies they are owed -- and free agency is gated on the draft too
    (a manager who has not picked yet cannot know what holes they are signing to
    fill). The fix is the commissioner's: draw the lottery, open the room, and let
    the clock finish what the managers do not.

    Declared ahead of the free-agency check because that is the order the offseason
    runs in, and the registry reports blockers in registration order -- the
    commissioner should be told to finish the draft before being told to close a
    market that cannot open yet."""
    draft = state.draft
    if draft is None:
        return []
    where = {
        "pending": "the lottery has not been drawn",
        "lottery_drawn": "the room has not opened",
        "open": "it is still on the clock",
    }.get(draft.status, f"it is {draft.status!r}")
    return [Blocker(
        check="draft_complete",
        subject="The draft",
        message=(f"The {draft.season} draft is not finished ({where}); finish it "
                 f"before the season can start."),
        detail={"season": draft.season, "status": draft.status},
    )]


@readiness_check(
    "free_agency_open",
    "Free agency is settled -- no offer round or auction is still running.",
)
def _free_agency_settled(state: LeagueState) -> list[Blocker]:
    """A season cannot start in the middle of its own free agency: unsigned players
    would open the year on nobody's roster, and a live board would be resolving
    contracts into a season already under way. The fix is the commissioner's -- finish
    the boards and close the period."""
    fa = state.free_agency
    if fa is None:
        return []
    if fa.live_auctions:
        detail = (f"round {fa.round_number} has {fa.live_auctions} auction(s) still "
                  f"live")
    elif fa.round_status == "offers":
        detail = f"round {fa.round_number} is still taking offers"
    else:
        detail = f"round {fa.round_number} is finished but the period is still open"
    return [Blocker(
        check="free_agency_open",
        subject="Free agency",
        message=(f"Free agency for {fa.season} is still open ({detail}); resolve it and "
                 f"close the period before the season can start."),
        detail={"season": fa.season, "round_number": fa.round_number,
                "round_status": fa.round_status, "live_auctions": fa.live_auctions},
    )]


@readiness_check(
    "roster_legality",
    "Every team can field a legal lineup, with no player left out of it.",
)
def _rosters_can_field_a_lineup(state: LeagueState) -> list[Blocker]:
    """A team that cannot be arranged into a legal lineup cannot play its games.

    Two ways to get here, and they are reported as one problem per team because the
    fix is the same conversation with that manager:
      - SHORT A POSITION (or too many bodies for reserves). Retirements and expiries
        both take players off a roster without asking whether what remains is legal,
        so this is the normal end-of-offseason state, not an exotic one. The fix is
        the manager's: sign a free agent, or trade.
      - EVERYTHING IS THERE BUT NOT PLACED. A signing rebuilds the lineup best-effort
        (roster_layout.try_rebuild_layout), so a player signed into an incomplete
        roster stays unplaced, and stays that way after the roster is completed by
        some later write that doesn't rebuild. They are on the team and would not
        play. Re-saving the lineup places them.

    Reported only when the roster is otherwise arrangeable: while a position is
    short, unplaced players are a SYMPTOM (there is no legal lineup to be in), and
    listing both would send the manager after the wrong thing first.
    """
    rules = DEFAULT_RULES
    out: list[Blocker] = []
    for team in state.rosters:
        short = team.shortfalls(rules)
        reserves, over_reserves = team.reserves(rules), 0
        if reserves > rules.reserve_max:
            over_reserves = reserves - rules.reserve_max

        reasons: list[str] = []
        for pos, have, need in short:
            reasons.append(f"{need - have} short at {pos} (has {have}, needs {need})")
        if over_reserves:
            reasons.append(f"{reserves} players for {rules.reserve_max} reserve spots "
                           f"({over_reserves} too many)")
        if reasons:
            message = (f"{team.name} cannot field a legal lineup: {'; '.join(reasons)}. "
                       f"Sign, trade or release players before the season can start.")
        elif team.unplaced:
            message = (f"{team.name} has {team.unplaced} player(s) on its roster but not "
                       f"in its lineup; they would not play. Save the team's lineup to "
                       f"place them before the season can start.")
        else:
            continue

        out.append(Blocker(
            check="roster_legality",
            subject=team.name,
            message=message,
            detail={
                "team_id": team.team_id, "slug": team.slug,
                "shortfalls": [{"position": p, "have": h, "needs": n} for p, h, n in short],
                "reserves": reserves, "reserve_max": rules.reserve_max,
                "unplaced": team.unplaced,
                "roster_size": sum(team.by_position.values()),
            },
        ))
    return out


# -- the database side -------------------------------------------------------
def load_league_state(engine: Engine, season: int) -> LeagueState:
    """Read the snapshot the checks run against. One query per fact group; add here
    (and to LeagueState) when a new check needs something. Retired players are
    excluded defensively -- retirement clears team_id, so they shouldn't be attached
    to a roster anyway."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("select t.id::text as team_id, t.slug, t.name, "
                 "coalesce(sum(p.contract_value) filter (where p.retired = false), 0) "
                 "  as payroll "
                 "from teams t left join players p on p.team_id = t.id "
                 "group by t.id, t.slug, t.name order by t.name")
        ).mappings().all()
        # The open free-agency period, if any, with its latest round and how many of
        # that round's boards are unresolved. Read here rather than through
        # free_agency.py so this module keeps its one dependency (salary_cap).
        fa = conn.execute(
            text("select f.season, r.round_number, r.status as round_status, "
                 "(select count(*) from fa_auctions a where a.round_id = r.id "
                 "  and a.status in ('collecting','matching','bidding','awaiting_award')) "
                 "  as live_auctions "
                 "from fa_periods f left join fa_rounds r on r.period_id = f.id "
                 "where f.status = 'open' order by r.round_number desc nulls last limit 1")
        ).mappings().first()
        # The draft for this season, when one exists and has not finished. Read
        # here rather than through draft.py so this module keeps its one dependency
        # (salary_cap); the status vocabulary is alembic 0014's CHECK constraint.
        draft_row = conn.execute(
            text("select season, status from draft_state "
                 "where season = :s and status <> 'complete'"),
            {"s": season},
        ).mappings().first()
        # Roster shape, one row per (team, position). The retired filter is in the
        # JOIN, not a WHERE, so a team whose players have all retired still comes
        # back -- as an empty roster, which is exactly the blocker we want to report.
        # A team with no players at all yields a single row with position NULL.
        roster_rows = conn.execute(
            text("select t.id::text as team_id, t.slug, t.name, "
                 "p.position::text as position, count(p.id) as n, "
                 "count(p.id) filter (where p.slot_group is null) as unplaced "
                 "from teams t "
                 "left join players p on p.team_id = t.id and p.retired = false "
                 "group by t.id, t.slug, t.name, p.position "
                 "order by t.name, p.position")
        ).mappings().all()
    # Fold the per-position rows up into one TeamRoster each, preserving the query's
    # name order (dicts iterate in insertion order).
    counts: dict[str, dict[str, int]] = {}
    unplaced: dict[str, int] = {}
    ident: dict[str, tuple[str, str]] = {}
    for r in roster_rows:
        tid = r["team_id"]
        ident.setdefault(tid, (r["slug"], r["name"]))
        counts.setdefault(tid, {})
        unplaced.setdefault(tid, 0)
        if r["position"] is None:
            continue                    # the no-players-at-all row; team still counts
        counts[tid][r["position"]] = int(r["n"])
        unplaced[tid] += int(r["unplaced"])
    return LeagueState(
        season=season,
        payrolls=tuple(
            TeamPayroll(team_id=r["team_id"], slug=r["slug"], name=r["name"],
                        payroll=int(r["payroll"]))
            for r in rows
        ),
        free_agency=OpenFreeAgency(
            season=int(fa["season"]),
            round_number=int(fa["round_number"] or 0),
            round_status=fa["round_status"] or "none",
            live_auctions=int(fa["live_auctions"] or 0),
        ) if fa else None,
        draft=UnfinishedDraft(
            season=int(draft_row["season"]), status=draft_row["status"],
        ) if draft_row else None,
        rosters=tuple(
            TeamRoster(team_id=tid, slug=ident[tid][0], name=ident[tid][1],
                       by_position=counts[tid], unplaced=unplaced[tid])
            for tid in ident
        ),
    )


def season_blockers(engine: Engine, season: int) -> list[Blocker]:
    """Everything standing between this league and the start of `season`."""
    return blockers(load_league_state(engine, season))


def assert_season_can_start(engine: Engine, season: int) -> None:
    """Enforcement point: raise SeasonNotReady unless the league is clear to play."""
    assert_ready(load_league_state(engine, season))


def readiness_report(engine: Engine, season: int) -> dict:
    """Serializable readiness summary for the API / Commissioner page. `checks` is
    included so the page can list what is being verified even when all of it passes."""
    found = season_blockers(engine, season)
    return {
        "season": season,
        "ready": not found,
        "blockers": [b.as_dict() for b in found],
        "checks": [{"name": c.name, "description": c.description} for c in checks()],
    }
