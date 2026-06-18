"""
Name: repository.py
Description: Persistence layer for the domain model -- the SINGLE source of truth
    for full (hidden) team state. The domain classes stay pure; all
    serialization lives here.

    Critical change from the current design: the existing per-team JSON is keyed
    by player NAME and stores NO roster arrangement -- the starters/bench/
    reserves split lives only on the Google Sheet, so a Team cannot be rebuilt
    without reading the sheet. This repository persists the arrangement too
    (players keyed by stable id), making the JSON self-contained: the whole
    simulation can load, run, and save with zero sheet access.

    InMemoryTeamRepository is the offline/test twin. JsonTeamRepository is the
    file-backed implementation. Neither imports anything sheet- or google-
    related.
Author: design sketch
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

from handball.domain import Player, Team, validate
from handball.league_views import PlayerId, TeamArrangement, TeamId
from handball.players import InjuryReport

SCHEMA_VERSION = 2  # v1 == legacy name-keyed, arrangement-on-sheet format


class RepositoryError(RuntimeError):
    pass


@runtime_checkable
class TeamRepository(Protocol):
    def load(self, team_id: TeamId) -> Team: ...
    def save(self, team: Team) -> None: ...
    def all_team_ids(self) -> list[TeamId]: ...
    def load_all(self) -> list[Team]: ...


# ---------------------------------------------------------------------------
# (De)serialization -- the only place that knows the on-disk shape.
# ---------------------------------------------------------------------------
def team_to_dict(team: Team) -> dict:
    """Self-contained snapshot: metadata + arrangement (ids) + players (by id)."""
    arr = team.arrangement()
    return {
        "schema_version": SCHEMA_VERSION,
        "id": team.id,
        "name": team.name,
        "coaches": list(team.coaches),
        "record": list(team.record),
        "arrangement": {
            "starters": {pos: list(ids) for pos, ids in arr.starters.items()},
            "bench": {pos: list(ids) for pos, ids in arr.bench.items()},
            "reserves": list(arr.reserves),
        },
        # Player serialization is generic over the dataclass fields, so it keeps
        # working as the real Player fields are merged onto domain.Player.
        "players": {p.id: asdict(p) for p in team.roster()},
    }


def _player_from_dict(pd: dict) -> Player:
    """Reconstruct a Player, rebuilding the nested InjuryReport. asdict() flattens
    it to a plain dict on save; here we revive the typed object so tick()/add()
    keep working and equality holds."""
    d = dict(pd)
    il = d.get("injury_log")
    if isinstance(il, dict):
        d["injury_log"] = InjuryReport.from_dict(
            {"active_injury": il["active_injury"],
             "injuries": [list(x) for x in il.get("injuries", [])]}
        )
    # Backwards-compat: legacy season logs predate the goalie save/goals-allowed
    # keys. Backfill any missing keys so downstream stat writes never KeyError.
    log = d.get("current_season_log")
    if isinstance(log, dict):
        for key in ("shots_taken", "goals", "performances", "saves", "goals_allowed"):
            log.setdefault(key, [])
    return Player(**d)


def team_from_dict(d: dict, *, validate_on_load: bool = True) -> Team:
    version = d.get("schema_version")
    if version != SCHEMA_VERSION:
        raise RepositoryError(
            f"team {d.get('id')!r}: unsupported schema_version {version!r} "
            f"(expected {SCHEMA_VERSION}). Legacy v1 files need a one-time "
            f"migration that captures arrangement from the sheet."
        )

    players: dict[PlayerId, Player] = {pid: _player_from_dict(pd) for pid, pd in d["players"].items()}
    arr = d["arrangement"]

    def group(section: dict) -> dict[str, list[Player]]:
        return {pos: [players[i] for i in ids] for pos, ids in section.items()}

    team = Team(
        id=d["id"],
        name=d["name"],
        coaches=list(d["coaches"]),
        starters=group(arr["starters"]),
        bench=group(arr["bench"]),
        reserves=[players[i] for i in arr["reserves"]],
        record=tuple(d["record"]),
    )

    if validate_on_load:
        # Integrity check: a persisted team must be a legal arrangement of its
        # own roster. Catches corruption / hand-edited files early.
        try:
            validate(team.arrangement(), team)
        except Exception as e:  # ArrangementError or KeyError on dangling ids
            raise RepositoryError(f"team {team.id!r}: corrupt persisted state: {e}") from e
    return team


# ---------------------------------------------------------------------------
# Offline twin.
# ---------------------------------------------------------------------------
class InMemoryTeamRepository:
    """In-memory TeamRepository for tests/offline runs. Stores a serialized
    snapshot (not the live object), so a load() returns an independent copy --
    matching JsonTeamRepository semantics exactly."""

    def __init__(self) -> None:
        self._store: dict[TeamId, dict] = {}

    def save(self, team: Team) -> None:
        self._store[team.id] = team_to_dict(team)

    def load(self, team_id: TeamId) -> Team:
        if team_id not in self._store:
            raise KeyError(f"no team {team_id!r} in repository")
        return team_from_dict(self._store[team_id])

    def all_team_ids(self) -> list[TeamId]:
        return sorted(self._store)

    def load_all(self) -> list[Team]:
        return [self.load(t) for t in self.all_team_ids()]


# ---------------------------------------------------------------------------
# File-backed implementation.
# ---------------------------------------------------------------------------
class JsonTeamRepository:
    """One JSON file per team under `datafiles_dir`. Self-contained: no sheet
    access needed to load a team."""

    def __init__(self, datafiles_dir: Path | str):
        self.dir = Path(datafiles_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _filename(team_id: TeamId) -> str:
        # Mirrors the existing lowercase convention (boston.json, "St. Louis" ->
        # st_louis.json). One place to change if the convention evolves.
        return team_id.lower().replace(".", "").replace(" ", "_") + ".json"

    def _path(self, team_id: TeamId) -> Path:
        return self.dir / self._filename(team_id)

    def save(self, team: Team) -> None:
        path = self._path(team.id)
        tmp = path.with_suffix(".json.tmp")
        # Atomic write: serialize to a temp file, then replace. A crash mid-write
        # never leaves a half-written team file.
        tmp.write_text(json.dumps(team_to_dict(team), indent=2))
        tmp.replace(path)

    def load(self, team_id: TeamId) -> Team:
        path = self._path(team_id)
        if not path.exists():
            raise KeyError(f"no team file for {team_id!r} at {path}")
        return team_from_dict(json.loads(path.read_text()))

    def all_team_ids(self) -> list[TeamId]:
        return sorted(
            json.loads(p.read_text())["id"]
            for p in self.dir.glob("*.json")
        )

    def load_all(self) -> list[Team]:
        return [self.load(t) for t in self.all_team_ids()]
