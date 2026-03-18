"""
Name: trade_handler.py
Description: Handles player and draft-pick trades between teams.

Responsibilities:
  - Validate that a trade is structurally legal (correct teams, players exist,
    roster limits not exceeded).
  - Remove players/picks from source teams.
  - Place incoming players into vacated slots (or the best available slot).
  - Persist changes to Team JSON datafiles.
  - Push updated rosters to the Google Sheet via TeamInfo.

Roster capacity rules:
  Starters  : 3 Forwards, 3 Midfielders, 3 Defense, 1 Goalie  = 10
  Bench     : 2 Forwards, 2 Midfielders, 2 Defense, 1 Goalie  =  7
  Reserves  : 4 (any position)                                 =  4
  Total                                                        = 21
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from handball.players import Player, PlayerInfo
from handball.free_agency import FreeAgencyHandler
from handball.sheets_handler import SheetHandler
from handball.teams import Team, TeamInfo, Subroster

Tier = Literal["starters", "bench", "reserves"]

POSITION_GROUPS = ("Forwards", "Midfielders", "Defense", "Goalie")

STARTER_CAPS: Dict[str, int] = {
    "Forwards": 3,
    "Midfielders": 3,
    "Defense": 3,
    "Goalie": 1,
}

BENCH_CAPS: Dict[str, int] = {
    "Forwards": 2,
    "Midfielders": 2,
    "Defense": 2,
    "Goalie": 1,
}

MAX_RESERVES = 4
MAX_ROSTER = 21

POSITION_TO_GROUP: Dict[str, str] = {
    "Forward": "Forwards",
    "Midfielder": "Midfielders",
    "Defense": "Defense",
    "Goalie": "Goalie",
}

# Placeholder PlayerInfo used when a roster slot is vacant so the
# Sheet-writing code (TeamInfo.update_sheet) does not crash on None.
_VACANT_PLAYER_INFO = PlayerInfo(
    name="",
    position="Forward",
    age=0,
    contract="",
    injured=False,
    offense=0.0,
    defense=0.0,
    goalie_skill=0.0,
)


def _vacant_goalie_info() -> PlayerInfo:
    return PlayerInfo(
        name="",
        position="Goalie",
        age=0,
        contract="",
        injured=False,
        offense=0.0,
        defense=0.0,
        goalie_skill=0.0,
    )


# ======================================================================
# Data classes that describe a trade
# ======================================================================


@dataclass
class PlayerMovement:
    """Describes one player moving in a trade."""

    player_name: str
    from_team: str
    to_team: Optional[str]  # None means released to free agency


@dataclass
class DraftPickMovement:
    """Describes one draft pick moving in a trade."""

    pick_description: str  # e.g. "Boston 2026" — must match the string in the team's draft_picks list
    round: Literal["1st Round", "2nd Round"]
    from_team: str
    to_team: str


@dataclass
class TradePackage:
    """
    Full description of a trade.

    players: list of PlayerMovement objects.
    draft_picks: list of DraftPickMovement objects.
    """

    players: List[PlayerMovement] = field(default_factory=list)
    draft_picks: List[DraftPickMovement] = field(default_factory=list)


# ======================================================================
# Internal helpers
# ======================================================================


@dataclass
class _SlotAddress:
    """Internal: where a player sits on a roster."""

    tier: Tier
    group: str  # "Forwards", "Midfielders", "Defense", "Goalie", or "reserves"
    index: int  # index within the list, or 0 for single-goalie slots


# ======================================================================
# TradeHandler
# ======================================================================


class TradeHandler:
    """
    Orchestrates trades between teams.

    Usage::

        handler = TradeHandler(
            teams={"Boston": boston_team, "New York": ny_team, ...},
            team_infos={"Boston": boston_info, "New York": ny_info, ...},
            sheet_handler=sheet_handler,
        )
        package = TradePackage(
            players=[
                PlayerMovement("Alice", from_team="Boston", to_team="New York"),
                PlayerMovement("Bob",   from_team="New York", to_team="Boston"),
            ],
            draft_picks=[
                DraftPickMovement("Boston 2026", "1st Round",
                                  from_team="Boston", to_team="New York"),
            ],
        )
        handler.execute_trade(package)
    """

    def __init__(
        self,
        teams: Dict[str, Team],
        team_infos: Dict[str, TeamInfo],
        sheet_handler: SheetHandler,
        free_agency: Optional[FreeAgencyHandler] = None,
    ) -> None:
        self.teams = teams
        self.team_infos = team_infos
        self.sheet_handler = sheet_handler
        self.free_agency = free_agency

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_trade(self, package: TradePackage) -> None:
        """
        Execute a full trade:
          1. Validate
          2. Record outgoing slot addresses
          3. Remove outgoing players from their teams
          4. Place incoming players on their new teams
          5. Move draft picks
          6. Persist to JSON and Google Sheets
        """
        self._validate(package)

        involved_teams: set[str] = set()
        for pm in package.players:
            involved_teams.add(pm.from_team)
            if pm.to_team is not None:
                involved_teams.add(pm.to_team)
        for dp in package.draft_picks:
            involved_teams.add(dp.from_team)
            involved_teams.add(dp.to_team)

        # --- Phase 1: find where every outgoing player sits ----
        vacated_slots: Dict[str, Dict[str, _SlotAddress]] = {}
        for pm in package.players:
            team = self.teams[pm.from_team]
            slot = self._find_player_slot(team, pm.player_name)
            if slot is None:
                raise ValueError(
                    f"Player '{pm.player_name}' not found on team '{pm.from_team}'."
                )
            vacated_slots.setdefault(pm.from_team, {})[pm.player_name] = slot

        # --- Phase 2: remove all outgoing players ----
        removed_players: Dict[str, Player] = {}
        for pm in package.players:
            team = self.teams[pm.from_team]
            slot = vacated_slots[pm.from_team][pm.player_name]
            player = self._remove_player(team, slot)
            removed_players[pm.player_name] = player

        # --- Phase 3: place incoming players / release to free agency ----
        incoming: Dict[str, List[Tuple[Player, Optional[_SlotAddress]]]] = {}
        for pm in package.players:
            player = removed_players[pm.player_name]
            if pm.to_team is None:
                if self.free_agency is not None:
                    self.free_agency.add_player(player)
                continue
            preferred = self._pick_preferred_slot(pm.to_team, player, vacated_slots)
            incoming.setdefault(pm.to_team, []).append((player, preferred))

        for team_name, arrivals in incoming.items():
            team = self.teams[team_name]
            for player, preferred in arrivals:
                self._place_player(team, player, preferred)

        # --- Phase 4: move draft picks ----
        for dp in package.draft_picks:
            self._move_draft_pick(dp)

        # --- Phase 5: persist ----
        for team_name in involved_teams:
            self._persist_team(team_name)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, package: TradePackage) -> None:
        for pm in package.players:
            if pm.from_team not in self.teams:
                raise ValueError(f"Source team '{pm.from_team}' not found.")
            if pm.to_team is not None and pm.to_team not in self.teams:
                raise ValueError(f"Destination team '{pm.to_team}' not found.")
            if pm.to_team is None and self.free_agency is None:
                raise ValueError(
                    f"Player '{pm.player_name}' is being released to free agency "
                    "but no FreeAgencyHandler was provided to TradeHandler."
                )
            if self._find_player_slot(self.teams[pm.from_team], pm.player_name) is None:
                raise ValueError(
                    f"Player '{pm.player_name}' is not on '{pm.from_team}'."
                )

        for dp in package.draft_picks:
            if dp.from_team not in self.teams:
                raise ValueError(f"Source team '{dp.from_team}' not found.")
            if dp.to_team not in self.teams:
                raise ValueError(f"Destination team '{dp.to_team}' not found.")

        # Net roster-size check
        net_change: Dict[str, int] = {}
        for pm in package.players:
            net_change[pm.from_team] = net_change.get(pm.from_team, 0) - 1
            if pm.to_team is not None:
                net_change[pm.to_team] = net_change.get(pm.to_team, 0) + 1

        for team_name, delta in net_change.items():
            if delta <= 0:
                continue
            current_count = self._roster_size(self.teams[team_name])
            if current_count + delta > MAX_ROSTER:
                raise ValueError(
                    f"Trade would put '{team_name}' at {current_count + delta} "
                    f"players, exceeding the {MAX_ROSTER}-player roster limit."
                )

    # ------------------------------------------------------------------
    # Roster inspection
    # ------------------------------------------------------------------

    @staticmethod
    def _roster_size(team: Team) -> int:
        count = 0
        for attr in ("forwards", "midfielders", "defense"):
            count += sum(1 for p in getattr(team.starters, attr) if p is not None)
            count += sum(1 for p in getattr(team.bench, attr) if p is not None)
        if team.starters.goalie is not None:
            count += 1
        if team.bench.goalie is not None:
            count += 1
        count += sum(1 for r in team.reserves if r is not None)
        return count

    @staticmethod
    def _find_player_slot(team: Team, player_name: str) -> Optional[_SlotAddress]:
        for tier_name, subroster in [("starters", team.starters), ("bench", team.bench)]:
            for group, attr in [("Forwards", "forwards"), ("Midfielders", "midfielders"), ("Defense", "defense")]:
                for i, p in enumerate(getattr(subroster, attr)):
                    if p is not None and p.name == player_name:
                        return _SlotAddress(tier=tier_name, group=group, index=i)  # type: ignore[arg-type]
            if subroster.goalie is not None and subroster.goalie.name == player_name:
                return _SlotAddress(tier=tier_name, group="Goalie", index=0)  # type: ignore[arg-type]

        for i, p in enumerate(team.reserves):
            if p is not None and p.name == player_name:
                return _SlotAddress(tier="reserves", group="reserves", index=i)

        return None

    # ------------------------------------------------------------------
    # Player removal
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_player(team: Team, slot: _SlotAddress) -> Player:
        if slot.tier == "reserves":
            player = team.reserves[slot.index]
            team.reserves[slot.index] = None  # type: ignore[assignment]
            return player

        subroster: Subroster = team.starters if slot.tier == "starters" else team.bench

        if slot.group == "Goalie":
            player = subroster.goalie
            subroster.goalie = None  # type: ignore[assignment]
            return player

        attr = {"Forwards": "forwards", "Midfielders": "midfielders", "Defense": "defense"}[slot.group]
        player_list: list = getattr(subroster, attr)
        player = player_list[slot.index]
        player_list[slot.index] = None  # type: ignore[assignment]
        return player

    # ------------------------------------------------------------------
    # Player placement
    # ------------------------------------------------------------------

    def _pick_preferred_slot(
        self,
        dest_team_name: str,
        player: Player,
        vacated_slots: Dict[str, Dict[str, _SlotAddress]],
    ) -> Optional[_SlotAddress]:
        """
        If the destination team lost a player from a slot compatible with the
        incoming player's position, prefer that slot.
        """
        if dest_team_name not in vacated_slots:
            return None

        player_group = POSITION_TO_GROUP.get(player.position)

        for vacated_name, slot in list(vacated_slots[dest_team_name].items()):
            if slot.tier == "reserves":
                del vacated_slots[dest_team_name][vacated_name]
                return slot
            if slot.group == player_group:
                del vacated_slots[dest_team_name][vacated_name]
                return slot

        return None

    def _place_player(
        self,
        team: Team,
        player: Player,
        preferred: Optional[_SlotAddress],
    ) -> None:
        """
        Place a player onto a team.

        Strategy:
          1. If a preferred (vacated) slot is given and currently empty, use it.
          2. Otherwise scan starters -> bench -> reserves for an open slot
             that matches the player's position.
          3. If no position-matching slot is open, try any open reserve slot.
          4. Raise if the roster is completely full.
        """
        if preferred is not None and self._slot_is_empty(team, preferred):
            self._set_slot(team, preferred, player)
            return

        player_group = POSITION_TO_GROUP.get(player.position, "Forwards")

        for tier_name, subroster in [("starters", team.starters), ("bench", team.bench)]:
            if player_group == "Goalie":
                if subroster.goalie is None:
                    subroster.goalie = player
                    return
            else:
                attr = {"Forwards": "forwards", "Midfielders": "midfielders", "Defense": "defense"}[player_group]
                player_list: list = getattr(subroster, attr)
                for i in range(len(player_list)):
                    if player_list[i] is None:
                        player_list[i] = player
                        return

        # Reserves (any position fits)
        for i in range(len(team.reserves)):
            if team.reserves[i] is None:
                team.reserves[i] = player
                return

        if len([r for r in team.reserves if r is not None]) < MAX_RESERVES:
            # If there's room because None entries were cleaned, append
            team.reserves.append(player)
            return

        raise RuntimeError(
            f"No open roster slot on '{team.team_name}' for '{player.name}'."
        )

    @staticmethod
    def _slot_is_empty(team: Team, slot: _SlotAddress) -> bool:
        if slot.tier == "reserves":
            return slot.index < len(team.reserves) and team.reserves[slot.index] is None
        subroster = team.starters if slot.tier == "starters" else team.bench
        if slot.group == "Goalie":
            return subroster.goalie is None
        attr = {"Forwards": "forwards", "Midfielders": "midfielders", "Defense": "defense"}[slot.group]
        player_list: list = getattr(subroster, attr)
        return slot.index < len(player_list) and player_list[slot.index] is None

    @staticmethod
    def _set_slot(team: Team, slot: _SlotAddress, player: Player) -> None:
        if slot.tier == "reserves":
            team.reserves[slot.index] = player
            return
        subroster = team.starters if slot.tier == "starters" else team.bench
        if slot.group == "Goalie":
            subroster.goalie = player
            return
        attr = {"Forwards": "forwards", "Midfielders": "midfielders", "Defense": "defense"}[slot.group]
        getattr(subroster, attr)[slot.index] = player

    # ------------------------------------------------------------------
    # Draft pick movement
    # ------------------------------------------------------------------

    def _move_draft_pick(self, dp: DraftPickMovement) -> None:
        src = self.teams[dp.from_team]
        dst = self.teams[dp.to_team]

        if src.draft_picks is None:
            src.draft_picks = {"1st Round": [], "2nd Round": []}
        if dst.draft_picks is None:
            dst.draft_picks = {"1st Round": [], "2nd Round": []}

        src_list: list = src.draft_picks[dp.round]
        if dp.pick_description not in src_list:
            raise ValueError(
                f"Draft pick '{dp.pick_description}' ({dp.round}) not found "
                f"on team '{dp.from_team}'."
            )
        src_list.remove(dp.pick_description)
        dst.draft_picks[dp.round].append(dp.pick_description)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_team(self, team_name: str) -> None:
        """
        Write changes for one team to:
          1. The per-team JSON datafile (player-level data).
          2. The Google Sheet via TeamInfo (roster layout, notes, stats).
        """
        team = self.teams[team_name]

        # -- 1) JSON: only write players that actually exist (skip None) --
        self._write_team_json(team)

        # -- 2) Google Sheet via TeamInfo --
        if team_name not in self.team_infos:
            return

        team_info = self.team_infos[team_name]
        team_info.starters = self._subroster_to_info_dict(team.starters, is_bench=False)
        team_info.bench = self._subroster_to_info_dict(team.bench, is_bench=True)

        team_info.reserve = [
            PlayerInfo.from_Player(p) if p is not None else PlayerInfo(
                name="", position="Forward", age=0, contract="",
                injured=False, offense=0.0, defense=0.0, goalie_skill=0.0,
            )
            for p in team.reserves
        ]
        # Pad reserves to 4 slots so the Sheet columns stay aligned
        while len(team_info.reserve) < MAX_RESERVES:
            team_info.reserve.append(PlayerInfo(
                name="", position="Forward", age=0, contract="",
                injured=False, offense=0.0, defense=0.0, goalie_skill=0.0,
            ))

        team_info.draft_picks = team.draft_picks
        team_info.update_sheet(
            self.sheet_handler,
            update_draft_picks=(team.draft_picks is not None),
        )

    @staticmethod
    def _write_team_json(team: Team) -> None:
        """
        Write player data to the team's JSON datafile.
        Skips None entries so vacant slots don't corrupt the file.
        """
        updated_dict: Dict[str, dict] = {}

        def _add_subroster(sub: Subroster) -> None:
            for attr in ("forwards", "midfielders", "defense"):
                for p in getattr(sub, attr):
                    if p is not None:
                        updated_dict[p.name] = p.to_dict()
            if sub.goalie is not None:
                updated_dict[sub.goalie.name] = sub.goalie.to_dict()

        _add_subroster(team.starters)
        _add_subroster(team.bench)
        for r in team.reserves:
            if r is not None:
                updated_dict[r.name] = r.to_dict()

        # Cast NumPy scalars just like Team.update_team_JSON does
        def _make_json_safe(obj):  # type: ignore[no-untyped-def]
            if isinstance(obj, dict):
                return {k: _make_json_safe(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_make_json_safe(v) for v in obj]
            try:
                import numpy as np  # type: ignore
                if isinstance(obj, (np.generic,)):
                    return obj.item()
            except Exception:
                pass
            return obj

        path = f"/Users/oliverhvidsten/Documents/handball/handball/datafiles/{team.team_name.lower()}.json"
        with open(path, "w") as f:
            json.dump(_make_json_safe(updated_dict), f)

    @staticmethod
    def _subroster_to_info_dict(sub: Subroster, is_bench: bool) -> dict:
        """
        Convert a Subroster (which may contain None entries after a trade)
        into the ``dict[str, list[PlayerInfo] | PlayerInfo]`` format that
        TeamInfo expects.
        """
        cap = BENCH_CAPS if is_bench else STARTER_CAPS

        def _to_info(p: Optional[Player], is_goalie: bool = False) -> PlayerInfo:
            if p is None:
                return _vacant_goalie_info() if is_goalie else _VACANT_PLAYER_INFO
            return PlayerInfo.from_Player(p)

        result: dict = {}
        for group, attr in [("Forwards", "forwards"), ("Midfielders", "midfielders"), ("Defense", "defense")]:
            raw = getattr(sub, attr)
            infos = [_to_info(p) for p in raw]
            # Pad to expected capacity so Sheet indices stay stable
            while len(infos) < cap[group]:
                infos.append(PlayerInfo(
                    name="", position="Forward", age=0, contract="",
                    injured=False, offense=0.0, defense=0.0, goalie_skill=0.0,
                ))
            result[group] = infos

        result["Goalie"] = _to_info(sub.goalie, is_goalie=True)
        return result
