"""
Name: season_readiness.py
Description: The league's preconditions for STARTING a season -- the "no games can be
    played until this is fixed" list. Today it holds one item (no team may open the
    season above the hard cap), but it is built as a registry so the list can grow
    without the API, the UI, or the run controls changing at all: write a new check
    function, decorate it, and it shows up everywhere readiness is reported.

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
class LeagueState:
    """Everything the readiness checks look at, read in one pass. Grow this as
    checks are added; keep it plain data so the checks stay pure."""
    season: int
    payrolls: tuple[TeamPayroll, ...] = ()
    free_agency: OpenFreeAgency | None = None


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
