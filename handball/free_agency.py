"""
Name: free_agency.py
Description: Manages free-agent player data across both a local JSON datafile
             and the Google Sheet "Free Agents" tab.

The JSON file (free_agents.json) is the source of truth for full Player data.
The Google Sheet is a derived view that stores the lighter PlayerInfo
representation (name, stats, notes).

JSON structure (mirrors per-team JSONs):
    { "player_name": { ...Player.to_dict()... }, ... }
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from handball.players import Player, PlayerInfo
from handball.sheets_handler import SheetHandler

FREE_AGENTS_JSON_PATH = (
    "/Users/oliverhvidsten/Documents/handball/handball/datafiles/free_agents.json"
)

# Maps Player.position values to the Sheet position-group keys used by
# SheetHandler.write_free_agents / read_free_agents.
_POSITION_TO_SHEET_GROUP: Dict[str, str] = {
    "Forward": "Forwards",
    "Midfielder": "Midfielders",
    "Defense": "Defenders",
    "Goalie": "Goalies",
}

_SHEET_GROUPS = ("Forwards", "Midfielders", "Defenders", "Goalies")


class FreeAgencyHandler:
    """
    Manages the pool of free-agent players.

    Usage::

        fa = FreeAgencyHandler(sheet_handler)
        fa.load_json()                   # load from disk
        fa.add_player(some_player)       # add a player
        fa.remove_player("Alice")        # sign a player to a team
        fa.save_json()                   # persist to disk
        fa.write_to_sheet()              # sync the full pool to Google Sheets
    """

    def __init__(self, sheet_handler: SheetHandler) -> None:
        self.sheet_handler = sheet_handler
        self._players: Dict[str, Player] = {}

    # ------------------------------------------------------------------
    # JSON persistence
    # ------------------------------------------------------------------

    def load_json(self) -> None:
        """
        Load all free-agent Player objects from the JSON datafile.
        If the file does not exist, starts with an empty pool.
        """
        if not os.path.exists(FREE_AGENTS_JSON_PATH):
            self._players = {}
            return

        with open(FREE_AGENTS_JSON_PATH, "r") as f:
            raw: dict = json.load(f)

        self._players = {}
        for name, player_dict in raw.items():
            self._players[name] = Player.from_dict(player_dict)

    def save_json(self) -> None:
        """Write the current free-agent pool to the JSON datafile."""
        os.makedirs(os.path.dirname(FREE_AGENTS_JSON_PATH), exist_ok=True)

        data = {name: player.to_dict() for name, player in self._players.items()}

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

        with open(FREE_AGENTS_JSON_PATH, "w") as f:
            json.dump(_make_json_safe(data), f, indent=2)

    # ------------------------------------------------------------------
    # Google Sheet I/O
    # ------------------------------------------------------------------

    def load_from_sheet(self) -> List[PlayerInfo]:
        """
        Read every position group from the Free Agents sheet and return a
        flat list of PlayerInfo objects.  Useful for bootstrapping when no
        JSON exists yet.

        This does **not** populate ``self._players`` because the Sheet only
        stores PlayerInfo-level data.  To populate ``self._players`` from
        the Sheet, use ``bootstrap_from_sheet`` instead.
        """
        all_infos: List[PlayerInfo] = []
        for group in _SHEET_GROUPS:
            all_infos.extend(self.sheet_handler.read_free_agents(group))
        return all_infos

    def write_to_sheet(self) -> None:
        """
        Overwrite the Free Agents sheet with the current in-memory pool.
        Converts each Player to a PlayerInfo for writing.
        """
        grouped: Dict[str, List[PlayerInfo]] = {g: [] for g in _SHEET_GROUPS}

        for player in self._players.values():
            group = _POSITION_TO_SHEET_GROUP.get(player.position)
            if group is None:
                continue
            grouped[group].append(PlayerInfo.from_Player(player))

        for group, infos in grouped.items():
            if infos:
                self.sheet_handler.write_free_agents(infos, group)

    # ------------------------------------------------------------------
    # Player operations
    # ------------------------------------------------------------------

    def add_player(self, player: Player, persist: bool = True) -> None:
        """
        Add a player to the free-agent pool.

        If *persist* is True (default), the JSON file and Sheet are updated
        immediately.  Set to False when doing bulk adds and call
        ``save_json()`` / ``write_to_sheet()`` manually afterwards.
        """
        self._players[player.name] = player
        if persist:
            self.save_json()
            self._write_position_to_sheet(player.position)

    def add_players(self, players: List[Player]) -> None:
        """Bulk-add players.  Persists once at the end."""
        for p in players:
            self._players[p.name] = p
        self.save_json()
        self.write_to_sheet()

    def remove_player(self, player_name: str, persist: bool = True) -> Player:
        """
        Remove a player from free agency and return the Player object.
        Raises KeyError if the player is not in the pool.
        """
        player = self._players.pop(player_name)
        if persist:
            self.save_json()
            self._write_position_to_sheet(player.position)
        return player

    def get_player(self, player_name: str) -> Optional[Player]:
        return self._players.get(player_name)

    def get_players_by_position(self, position: str) -> List[Player]:
        """
        Return free agents whose ``Player.position`` matches *position*
        (e.g. "Forward", "Midfielder", "Defense", "Goalie").
        """
        return [p for p in self._players.values() if p.position == position]

    def all_players(self) -> List[Player]:
        return list(self._players.values())

    @property
    def count(self) -> int:
        return len(self._players)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_position_to_sheet(self, position: str) -> None:
        """Rewrite a single position group on the Free Agents sheet."""
        group = _POSITION_TO_SHEET_GROUP.get(position)
        if group is None:
            return
        infos = [
            PlayerInfo.from_Player(p)
            for p in self._players.values()
            if p.position == position
        ]
        if infos:
            self.sheet_handler.write_free_agents(infos, group)
