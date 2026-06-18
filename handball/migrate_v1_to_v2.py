"""
Name: migrate_v1_to_v2.py
Description: One-time migration of legacy per-team JSON (v1) to the self-contained
    v2 schema used by repository.py.

    v1 reality: each file is `{player_name: {full player fields}}` -- keyed by
    NAME, with NO roster arrangement and NO team metadata. The starters/bench/
    reserves split and the team name/coaches/record all live only on the Google
    Sheet. So migration is a JOIN of three inputs:

        legacy JSON  (hidden player stats, by name)
      + sheet arrangement  (which player sits in which slot)
      + sheet metadata     (team name, coaches, record)
      -> v2 file (players by stable id, arrangement embedded, fully self-contained)

    The arrangement/metadata source is injected (ArrangementSource), so the
    migration logic is testable fully offline; SheetArrangementSource is the
    production binding (lazy google import).

    Field-agnostic: player dicts are carried through VERBATIM (plus an `id`), so
    no hidden stat is dropped even while domain.Player is still minimal.
Author: design sketch
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from handball.domain import ArrangementError, Player, Team, validate
from handball.league_views import DEFAULT_RULES, RosterRules, TeamArrangement
from handball.repository import SCHEMA_VERSION, JsonTeamRepository


# ---------------------------------------------------------------------------
# Inputs the migration needs that a v1 JSON file does NOT contain.
# ---------------------------------------------------------------------------
class ArrangementSource(Protocol):
    def arrangement(self, team_id: str) -> dict:
        """{"starters": {pos: [name, ...]}, "bench": {...}, "reserves": [name, ...]}"""
        ...

    def metadata(self, team_id: str) -> dict:
        """{"name": str, "coaches": [HC, OC, DC], "record": [w, l, t]}"""
        ...


@dataclass
class DictArrangementSource:
    """Offline source for tests / dry runs from pre-captured data."""
    arrangements: dict[str, dict]
    metadatas: dict[str, dict]

    def arrangement(self, team_id: str) -> dict:
        return self.arrangements[team_id]

    def metadata(self, team_id: str) -> dict:
        return self.metadatas[team_id]


class SheetArrangementSource:
    """Production source: read slot names + metadata off the live sheet. Lazy
    google import so this module loads offline."""

    def __init__(self, handler, layout=None):
        # `handler` is one SheetHandler for the whole workbook; team_id is a tab.
        from handball.sheet_gateway import SheetLayout

        self.handler = handler
        self.layout = layout or SheetLayout()

    @classmethod
    def from_sheet_id(cls, sheet_id: str, layout=None) -> "SheetArrangementSource":
        from handball.sheets_handler import SheetHandler

        return cls(SheetHandler(sheet_id), layout=layout)

    def _grid(self, team_id: str) -> list[list]:
        return self.handler.get_full_team_values(team_id)

    @staticmethod
    def _cell(grid, r, c) -> str:
        return str(grid[r][c]).strip() if r < len(grid) and c < len(grid[r]) else ""

    def arrangement(self, team_id: str) -> dict:
        g, L = self._grid(team_id), self.layout
        names = lambda rows: [self._cell(g, r, L.name_col) for r in rows]
        return {
            "starters": {pos: names(rows) for pos, rows in L.starter_rows.items()},
            "bench": {pos: names(rows) for pos, rows in L.bench_rows.items()},
            "reserves": names(L.reserve_rows),
        }

    def metadata(self, team_id: str) -> dict:
        g, L = self._grid(team_id), self.layout
        rec_raw = self._cell(g, L.record_row, L.record_col)
        record = [int(x) for x in rec_raw.split("-")] if rec_raw else [0, 0, 0]
        # Coach names live at coach_col; unfilled cells hold the "<insert name>"
        # template placeholder -- normalize those to empty.
        coaches = [self._cell(g, r, L.coach_col) for r in L.coach_rows]
        coaches = ["" if c == "<insert name>" else c for c in coaches]
        return {"name": team_id, "coaches": coaches, "record": record}


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------
@dataclass
class MigrationIssue:
    team_id: str
    severity: str   # "error" | "warning"
    message: str


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _legacy_filename(team_id: str) -> str:
    """v1 files were written as f"{team_name.lower()}.json" -- spaces and
    periods preserved (e.g. "new york.json", "st. louis.json"). Distinct from
    JsonTeamRepository._filename, which normalizes those for v2 output."""
    return team_id.lower() + ".json"


def _assign_ids(team_id: str, names: list[str]) -> dict[str, str]:
    """Deterministic, human-readable, collision-safe ids (sorted for stability
    across runs)."""
    out, used = {}, set()
    for name in sorted(names):
        base = f"{_slug(team_id)}-{_slug(name)}"
        pid, n = base, 2
        while pid in used:
            pid, n = f"{base}-{n}", n + 1
        used.add(pid)
        out[name] = pid
    return out


def migrate_team(
    team_id: str,
    legacy_players: dict,
    source: ArrangementSource,
    rules: RosterRules = DEFAULT_RULES,
) -> tuple[dict | None, list[MigrationIssue]]:
    """Join legacy stats + sheet arrangement/metadata into a v2 dict. Returns
    (v2_dict_or_None, issues). Returns None when there's an error-level issue."""
    issues: list[MigrationIssue] = []
    err = lambda m: issues.append(MigrationIssue(team_id, "error", m))
    warn = lambda m: issues.append(MigrationIssue(team_id, "warning", m))

    name_to_id = _assign_ids(team_id, list(legacy_players))
    arr_names = source.arrangement(team_id)
    meta = source.metadata(team_id)

    placed: set[str] = set()

    def to_ids(names: list[str]) -> list[str]:
        ids = []
        for nm in names:
            if not nm:
                continue  # vacant slot on the sheet
            if nm not in name_to_id:
                err(f"sheet lists {nm!r} but no such player in legacy JSON")
                continue
            if nm in placed:
                err(f"{nm!r} appears in more than one slot on the sheet")
                continue
            placed.add(nm)
            ids.append(name_to_id[nm])
        return ids

    starters = {pos: to_ids(arr_names["starters"].get(pos, [])) for pos in rules.starter_caps}
    bench = {pos: to_ids(arr_names["bench"].get(pos, [])) for pos in rules.bench_caps}
    reserves = to_ids(arr_names["reserves"])

    for orphan in sorted(set(legacy_players) - placed):
        err(f"legacy player {orphan!r} is not placed in any slot on the sheet")

    # Carry every player field through verbatim, just adding the stable id.
    players = {name_to_id[n]: {**pd, "id": name_to_id[n]} for n, pd in legacy_players.items()}

    v2 = {
        "schema_version": SCHEMA_VERSION,
        "id": team_id,
        "name": meta.get("name", team_id),
        "coaches": list(meta.get("coaches", [])),
        "record": list(meta.get("record", [0, 0, 0])),
        "arrangement": {"starters": starters, "bench": bench, "reserves": reserves},
        "players": players,
    }

    # Structural validation reuses the real validator with lightweight players.
    try:
        _validate_structure(v2, rules)
    except ArrangementError as e:
        for p in e.problems:
            err(f"invalid arrangement: {p}")

    if any(i.severity == "error" for i in issues):
        return None, issues
    return v2, issues


def _validate_structure(v2: dict, rules: RosterRules) -> None:
    """Run domain.validate against stripped-down players (only the fields the
    validator inspects), so we don't need the full Player model to check shape."""
    lite = {
        pid: Player(id=pid, name=pd.get("name", pid), position=pd["position"],
                    is_injured=bool(pd.get("is_injured", False)))
        for pid, pd in v2["players"].items()
    }
    arr = v2["arrangement"]

    def group(section):
        return {pos: [lite[i] for i in ids] for pos, ids in section.items()}

    team = Team(
        id=v2["id"], name=v2["name"], coaches=v2["coaches"],
        starters=group(arr["starters"]), bench=group(arr["bench"]),
        reserves=[lite[i] for i in arr["reserves"]], record=tuple(v2["record"]),
    )
    validate(team.arrangement(), team, rules)


def run_migration(
    legacy_dir: Path | str,
    out_dir: Path | str,
    source: ArrangementSource,
    team_ids: list[str] | None = None,
    rules: RosterRules = DEFAULT_RULES,
    write: bool = True,
) -> list[MigrationIssue]:
    """Migrate every legacy file (or `team_ids`) into `out_dir`. Writes only
    teams that migrate cleanly; returns all issues across all teams."""
    legacy_dir, out_dir = Path(legacy_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_issues: list[MigrationIssue] = []
    ok = failed = 0

    if team_ids is None:
        # Caller maps filenames -> team ids; for the live sheet that's SHEET_ID_NUM.
        raise ValueError("team_ids is required (map legacy filenames to sheet team ids)")

    for team_id in team_ids:
        legacy_path = legacy_dir / _legacy_filename(team_id)   # v1 naming on read
        if not legacy_path.exists():
            all_issues.append(MigrationIssue(team_id, "error", f"no legacy file at {legacy_path}"))
            failed += 1
            continue
        legacy_players = json.loads(legacy_path.read_text())
        v2, issues = migrate_team(team_id, legacy_players, source, rules)
        all_issues.extend(issues)
        if v2 is None:
            failed += 1
            continue
        ok += 1
        if write:
            (out_dir / JsonTeamRepository._filename(team_id)).write_text(json.dumps(v2, indent=2))

    print(f"migration: {ok} ok, {failed} failed")
    for i in all_issues:
        print(f"  [{i.severity}] {i.team_id}: {i.message}")
    return all_issues


def main(write: bool = True, cred_path: str | None = None):  # pragma: no cover - live run
    from handball.constants import SHEET_ID_NUM

    project_root = Path(__file__).parent.parent
    cred = Path(cred_path) if cred_path else project_root / "cred.txt"
    sheet_id = cred.read_text().splitlines()[0].strip()

    here = Path(__file__).parent
    team_ids = [t for t in SHEET_ID_NUM if t not in ("Overview", "EXAMPLE", "Free Agents")]
    return run_migration(
        legacy_dir=here / "datafiles",
        out_dir=here / "datafiles_v2",
        source=SheetArrangementSource.from_sheet_id(sheet_id),
        team_ids=team_ids,
        write=write,
    )


if __name__ == "__main__":
    main()
