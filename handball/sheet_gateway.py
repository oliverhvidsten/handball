"""
Name: sheet_gateway.py
Description: The ONLY module that knows the Google Sheet exists. Everything else
    in the simulation talks to the SheetGateway interface in terms of domain
    value types (TeamArrangement in, TeamPublicView out).

    Two channels, never a bidirectional mirror:
      - publish(view)          -> OUTBOX: render the public projection to cells
      - read_arrangement(team) -> INBOX:  parse the manager-editable region

    FakeSheetGateway is an in-memory twin with zero Google dependencies, so the
    whole league can run and be tested offline. GoogleSheetGateway wraps the
    existing SheetHandler and is the single home for all hardcoded ids/ranges/
    auth -- it is imported lazily so importing this module never requires
    google libs or gs_key.json.
Author: design sketch
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from handball.league_views import (
    PlayerId,
    PlayerPublicView,
    TeamArrangement,
    TeamId,
    TeamPublicView,
)


class ArrangementParseError(ValueError):
    """Raised when the sheet's editable region can't be resolved to known
    players (e.g. a manager typed a name that isn't on the roster)."""


@runtime_checkable
class SheetGateway(Protocol):
    def read_arrangement(self, team_id: TeamId) -> TeamArrangement:
        """Parse the editable region (names in slots) into a TeamArrangement
        (ids in slots)."""
        ...

    def publish(self, view: TeamPublicView) -> None:
        """Render the public projection to the sheet. Idempotent; establishes
        the baseline the next read is diffed against."""
        ...

    def publish_free_agents(self, views: list[PlayerPublicView]) -> None:
        ...


# ---------------------------------------------------------------------------
# Offline twin -- no google deps, importable and runnable anywhere.
# ---------------------------------------------------------------------------
class FakeSheetGateway:
    """In-memory SheetGateway for tests and offline runs.

    Models the mailbox invariant explicitly:
      - publish() stores the projection AND clears any pending manager edit
        (after we publish, the sheet shows canonical state).
      - read_arrangement() returns a pending edit if one exists, otherwise the
        baseline derived from the last published view.

    Tests drive the "manager rearranges their lineup between cycles" case via
    simulate_manager_edit().
    """

    def __init__(self) -> None:
        self._published: dict[TeamId, TeamPublicView] = {}
        self._edits: dict[TeamId, TeamArrangement] = {}
        self._free_agents: list[PlayerPublicView] = []
        self.publish_calls = 0  # test affordance

    def publish(self, view: TeamPublicView) -> None:
        self._published[view.id] = view
        self._edits.pop(view.id, None)  # republish resets the baseline
        self.publish_calls += 1

    def read_arrangement(self, team_id: TeamId) -> TeamArrangement:
        if team_id in self._edits:
            return self._edits[team_id]
        if team_id in self._published:
            return TeamArrangement.from_public_view(self._published[team_id])
        raise KeyError(f"team {team_id!r} has never been published")

    def publish_free_agents(self, views: list[PlayerPublicView]) -> None:
        self._free_agents = list(views)

    # -- test affordances -------------------------------------------------
    def last_published(self, team_id: TeamId) -> TeamPublicView:
        return self._published[team_id]

    def simulate_manager_edit(self, team_id: TeamId, arrangement: TeamArrangement) -> None:
        """Stand in for a human dragging names around the sheet between cycles."""
        if team_id not in self._published:
            raise KeyError(f"cannot edit unpublished team {team_id!r}")
        self._edits[team_id] = arrangement


# ---------------------------------------------------------------------------
# Real gateway -- wraps the existing SheetHandler. All sheet-layout knowledge
# lives here; nothing else imports SheetHandler or constants.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SheetLayout:
    """Row/column map into the TEAM_RANGE block (A3:F32 -> 0-indexed grid).
    Mirrors constants.py (STARTERS_ROW=5, BENCH_ROW=17, RESERVES_ROW=26,
    NAME_COL=1, OFF_COL=2, DEF_COL=3, record at F3)."""
    name_col: int = 1
    off_col: int = 2
    def_col: int = 3
    record_row: int = 0
    record_col: int = 5
    coach_rows: tuple[int, int, int] = (0, 1, 2)  # HC, OC, DC rows
    coach_col: int = 2   # coach NAMES sit at col 2 (col 0 holds the label)
    starter_rows: dict[str, tuple[int, ...]] = field(default_factory=lambda: {
        "Forward": (5, 6, 7), "Midfielder": (8, 9, 10),
        "Defense": (11, 12, 13), "Goalie": (14,),
    })
    bench_rows: dict[str, tuple[int, ...]] = field(default_factory=lambda: {
        "Forward": (17, 18), "Midfielder": (19, 20),
        "Defense": (21, 22), "Goalie": (23,),
    })
    reserve_rows: tuple[int, ...] = (26, 27, 28, 29)


class GoogleSheetGateway:
    """Production SheetGateway. Resolves sheet names -> ids using the roster it
    last published (the mailbox invariant guarantees the on-sheet baseline
    equals the last published view, so a name found there is unambiguous within
    its position group).

    NOTE: name resolution assumes names are unique within a position group on a
    team -- true in practice; stable ids exist precisely so the rest of the
    system never relies on names.
    """

    def __init__(self, handler, layout: SheetLayout | None = None):
        # `handler` is a single SheetHandler for the whole workbook; teams are
        # tabs addressed by name via get/update_full_team_values(team_id).
        # Use from_sheet_id() in production; inject a fake handler in tests.
        self.handler = handler
        self.layout = layout or SheetLayout()
        # team_id -> {name: id} from the last publish, for read-time resolution.
        self._name_index: dict[TeamId, dict[str, PlayerId]] = {}

    @classmethod
    def from_sheet_id(cls, sheet_id: str, layout: SheetLayout | None = None) -> "GoogleSheetGateway":
        # Lazy import so this module loads with no google libs unless used live.
        from handball.sheets_handler import SheetHandler

        return cls(SheetHandler(sheet_id), layout=layout)

    @staticmethod
    def _cell(grid: list[list], row: int, col: int) -> str:
        if row < len(grid) and col < len(grid[row]):
            return str(grid[row][col]).strip()
        return ""

    def read_arrangement(self, team_id: TeamId) -> TeamArrangement:
        grid = self.handler.get_full_team_values(team_id)
        name_to_id = self._name_index.get(team_id, {})

        def resolve(name: str) -> PlayerId:
            try:
                return name_to_id[name]
            except KeyError:
                raise ArrangementParseError(
                    f"{team_id}: sheet name {name!r} is not a known rostered "
                    f"player. Manager may have edited a non-arrangement field."
                )

        def ids_for(rows: tuple[int, ...]) -> tuple[PlayerId, ...]:
            out = []
            for r in rows:
                name = self._cell(grid, r, self.layout.name_col)
                if name:
                    out.append(resolve(name))
            return tuple(out)

        L = self.layout
        return TeamArrangement(
            starters={pos: ids_for(rows) for pos, rows in L.starter_rows.items()},
            bench={pos: ids_for(rows) for pos, rows in L.bench_rows.items()},
            reserves=ids_for(L.reserve_rows),
        )

    def publish(self, view: TeamPublicView) -> None:
        L = self.layout
        grid = self.handler.get_full_team_values(view.id)  # read-modify-write, transient

        def ensure(row: int, col: int) -> None:
            while len(grid) <= row:
                grid.append([])
            while len(grid[row]) <= col:
                grid[row].append("")

        def write_player(row: int, p: PlayerPublicView) -> None:
            ensure(row, max(L.name_col, L.off_col, L.def_col))
            grid[row][L.name_col] = p.name
            if p.position == "Goalie":
                grid[row][L.off_col] = round(p.goalie_skill, 2)
            else:
                grid[row][L.off_col] = round(p.offense, 2)
                grid[row][L.def_col] = round(p.defense, 2)

        for pos, rows in L.starter_rows.items():
            for row, p in zip(rows, view.starters.get(pos, [])):
                write_player(row, p)
        for pos, rows in L.bench_rows.items():
            for row, p in zip(rows, view.bench.get(pos, [])):
                write_player(row, p)
        for row, p in zip(L.reserve_rows, view.reserves):
            write_player(row, p)

        for r, coach in zip(L.coach_rows, view.coaches):
            ensure(r, L.coach_col)
            grid[r][L.coach_col] = coach

        ensure(L.record_row, L.record_col)
        grid[L.record_row][L.record_col] = "-".join(str(x) for x in view.record)

        self.handler.update_full_team_values(view.id, grid)

        # Refresh the name->id index so the next read can resolve names.
        self._name_index[view.id] = {p.name: p.id for p in view.all_players()}

    def publish_free_agents(self, views: list[PlayerPublicView]) -> None:
        raise NotImplementedError("wire to SheetHandler.write_free_agents in step 1")
