"""
Name: teams.py
Description: This file contains code for the Team classes
Author: Oliver Hvidsten (oliverhvidsten@gmail.com)
Date: 1/20/2025 10:38AM PST
"""
from __future__ import annotations

from handball.constants import DRAFT_PICKS_RANGE
from handball.utils import print_roster
from handball.sheets_handler import SheetHandler
from handball.players import Player, PlayerInfo

from dataclasses import dataclass
import json

@dataclass
class TeamInfo():
    """
    The TeamInfo class holds all of the Information about the team, but not the actual player objects.
    This class is used to directly communicate with the google sheet.

    Convert a TeamInfo object to a Team object to get the inputs to simulate a game.
    This willl require turning player names (str) into player objects (Player)

    Team Structure:
    Coaching Staff:
        - Head Coach

    Starters:
        - 3 Offense
        - 3 Midfield
        - 3 Defense
        - 1 Goalie
    
    Bench
        - 2 Offense
        - 2 Midfield
        - 2 Defense
        - 1 Goalie
    
    Reserve

    Record
    Total Salaries
    Raw Info
    

    --Starters/Bench--
    {
        "Forwards": [...],
        "Midfielders": [...],
        "Defense": [...],
        "Goalie": <str>
        }
    """
    team_name: str  # just the location (e.g. "Boston" not "Boston Foxes")
    coaches: list[str] # 1x3 list of coaching staff [HC, OC, DC]
    starters: dict[str, list[PlayerInfo] | PlayerInfo] # dictionary holding lists for forwards, midfielders, and defense, and one goalie
    bench: dict[str, list[PlayerInfo] | PlayerInfo] # dictionary holding lists for forwards, midfielders, and defense, and one goalie
    reserve: list[PlayerInfo] # 1x4 list of reserve player names
    draft_picks: dict|None # 2 element dict containing 1xN (1st round) and 1xM (2nd round) lists of draft picks that a team owns
    record: list[int] # 1x3 list holding wins,losses,ties
    total_salaries: int # total salaries (in millions)
    raw_data: tuple

    def __str__(self):

        print("look here")
        print(self.starters.keys())

        lines = [
            f"######  {self.team_name.upper()}  ######",
            f"Head Coach: {self.coaches[0]}",
            f"OC: {self.coaches[1]}, DC: {self.coaches[2]}",
            f"",
            f"--- STARTERS ---",
            print_roster(self.starters),
            f"",
            f"--- BENCH ---",
            print_roster(self.bench),
            f"",
            f"--- RESERVES ---" + "".join([f"\n    {playerinfo}" for playerinfo in self.reserve])
        ]
        return "\n".join(lines)

    @classmethod
    def from_sheet(cls, sheet_handler:SheetHandler, team_name, get_draft_picks):
        """
        Get all of the information for the specified team from the google sheet

        Inputs:
            1. sheet_handler (SheetHandler): object holding credentials for the Google Sheet
            2. team_name (str): team name is just the location (e.g. "Boston" not "Boston Foxes")
            3. get_draft_picks (bool): If True, access the draft picks. Otherwise, do not access

        Return:
            (TeamInfo): team object for the specified team
        """
        team_info = sheet_handler.get_full_team_values(team_name)
        player_notes, player_names = sheet_handler.get_player_notes(team_name)
        player_notes_dict = dict()
        for note, player_name in zip(player_notes, player_names):
            if len(player_name) > 1 and player_name not in ["Starters", "Bench", "Reserves"]:
                player_notes_dict[player_name] = note
        if get_draft_picks:
            draft_info = sheet_handler.get_draft_picks(team_name)
            # draft_info is a list of rows [[1st-round, 2nd-round], ...] from the
            # sheet; index by row/column rather than numpy-style slicing.
            draft_picks = {
                "1st Round": [row[0] for row in draft_info if len(row) > 0 and row[0]],
                "2nd Round": [row[1] for row in draft_info if len(row) > 1 and row[1]]}
        else:
            draft_info = None
            draft_picks = None
        coaches = [team_info[0][2], team_info[1][2], team_info[2][2]]
        starters = {
            "Forwards":[
                PlayerInfo.from_sheet(team_info[5], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[6], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[7], player_notes_dict)
            ],
            "Midfielders":[
                PlayerInfo.from_sheet(team_info[8], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[9], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[10], player_notes_dict)
                ],
            "Defense":[
                PlayerInfo.from_sheet(team_info[11], player_notes_dict),
                PlayerInfo.from_sheet(team_info[12], player_notes_dict),
                PlayerInfo.from_sheet(team_info[13], player_notes_dict)
                ],
            "Goalie": PlayerInfo.from_sheet(team_info[14], player_notes_dict)
        }
        bench = {
            "Forwards":[
                PlayerInfo.from_sheet(team_info[17], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[18], player_notes_dict)
                ],
            "Midfielders":[
                PlayerInfo.from_sheet(team_info[19], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[20], player_notes_dict)
                ],
            "Defense":[
                PlayerInfo.from_sheet(team_info[21], player_notes_dict), 
                PlayerInfo.from_sheet(team_info[22], player_notes_dict)
                ],
            "Goalie":PlayerInfo.from_sheet(team_info[23], player_notes_dict)
        }
        reserve = [
            PlayerInfo.from_sheet(team_info[26], player_notes_dict),
            PlayerInfo.from_sheet(team_info[27], player_notes_dict),
            PlayerInfo.from_sheet(team_info[28], player_notes_dict),
            PlayerInfo.from_sheet(team_info[29], player_notes_dict)
        ]

        record = [int(num) for num in team_info[0][5].split("-")] # make W-L-T into [W, L, T]
        total_salaries = int(team_info[1][5][1:-1]) # get everything except for the "$" at the start "M" at the end

        return TeamInfo(
            team_name=team_name,
            coaches=coaches,
            starters=starters,
            bench=bench, 
            reserve=reserve,
            draft_picks=draft_picks,
            record=record,
            total_salaries=total_salaries,
            raw_data=(team_info, draft_info, player_notes_dict)
        )

    # TEAM_RANGE ("A3:F32") spans 30 rows x 6 columns.
    _TEAM_RANGE_ROWS = 30
    _TEAM_RANGE_COLS = 6

    @staticmethod
    def _pad_grid(grid, rows, cols):
        """
        Pad a (possibly ragged / short) 2D grid out to ``rows`` x ``cols`` with
        empty strings. The Google Sheets API omits trailing empty cells/rows, so
        an empty team tab returns far fewer than the full TEAM_RANGE; padding
        lets update_sheet index and write every cell without IndexError.
        """
        padded = [list(row) + [""] * (cols - len(row)) for row in grid]
        while len(padded) < rows:
            padded.append([""] * cols)
        return padded

    @classmethod
    def from_empty_sheet(cls, sheet_handler:SheetHandler, team_name:str):
        """
        Create a TeamInfo object from an empty sheet
        """
        team_info = sheet_handler.get_full_team_values(team_name)
        team_info = cls._pad_grid(team_info, cls._TEAM_RANGE_ROWS, cls._TEAM_RANGE_COLS)
        draft_info = sheet_handler.get_draft_picks(team_name)
        player_notes, player_names = sheet_handler.get_player_notes(team_name)
        player_notes_dict = dict()
        return cls(
            team_name=team_name,
            coaches=["<insert name>"]*3,
            starters=dict(),
            bench=dict(),
            reserve=[],
            draft_picks=None,
            record=[0,0,0],
            total_salaries=0,
            raw_data=(team_info, draft_info, player_notes_dict))
    
    
    def update_sheet(self, sheet_handler:SheetHandler, update_draft_picks=False):
        """ Updates the google sheet to reflect any alterations that have occurred """
        # let's edit the raw data that we saved earlier
        team_info = self.raw_data[0]
        draft_info = self.raw_data[1]

        ## TODO: lots of type errors here because we switch between list[PlayerInfo] and just PlayerInfo 
        ## values in the dictionaries --> think about ways to recitify this

        ## For now, putting type ignore flags
        
        # create list of empty strings to fill in with updated notes
        new_notes = [""] * 25
        
        
        for i in range(3):
            team_info[i][2] = self.coaches[i] # update coaches
            team_info[i+5][1:4] = self.starters["Forwards"][i].name_and_stats() # type: ignore
            team_info[i+8][1:4] = self.starters["Midfielders"][i].name_and_stats() # type: ignore
            team_info[i+11][1:4] = self.starters["Defense"][i].name_and_stats() # type: ignore

            new_notes[i] = self.starters["Forwards"][i].get_notes() # type: ignore
            new_notes[i+3] = self.starters["Midfielders"][i].get_notes() # type: ignore
            new_notes[i+6] = self.starters["Defense"][i].get_notes() # type: ignore

        for i in range(2):
            team_info[i+17][1:4] = self.bench["Forwards"][i].name_and_stats() # type: ignore
            team_info[i+19][1:4] = self.bench["Midfielders"][i].name_and_stats() # type: ignore
            team_info[i+21][1:4] = self.bench["Defense"][i].name_and_stats() # type: ignore

            new_notes[i+12] = self.bench["Forwards"][i].get_notes() # type: ignore
            new_notes[i+14] = self.bench["Midfielders"][i].get_notes() # type: ignore
            new_notes[i+16] = self.bench["Defense"][i].get_notes() # type: ignore
            

        for i in range(4):
            if self.reserve[i].position == "Goalie":
                team_info[i+26][1:3] = self.reserve[i].name_and_stats(is_goalie=True)
            else:
                team_info[i+26][1:4] = self.reserve[i].name_and_stats()
            new_notes[i+21] = self.reserve[i].get_notes()

        team_info[14][1:3] = self.starters["Goalie"].name_and_stats(is_goalie=True) # type: ignore
        new_notes[9] = self.starters["Goalie"].get_notes() # type: ignore
        team_info[23][1:3] = self.bench["Goalie"].name_and_stats(is_goalie=True) # type: ignore
        new_notes[18] = self.starters["Goalie"].get_notes() # type: ignore
        
        team_info[0][5] = "-".join([str(value) for value in self.record])
        team_info[1][5] = f"${self.total_salaries}M"

        # update the values and notes
        sheet_handler.update_full_team_values(team_name=self.team_name, edited_data=team_info)
        sheet_handler.update_player_notes(team_name=self.team_name, new_notes=new_notes)

        # If requested fill in the draft picks
        if update_draft_picks:
            # in case of empty draft_info:
            if len(draft_info) == 0:
                draft_info = [["", ""]] * len(self.draft_picks["1st Round"])
            
            for i,first_round in enumerate(self.draft_picks["1st Round"]): # type: ignore
                draft_info[i][0] = first_round
            
            for i, second_round in enumerate(self.draft_picks["2nd Round"]): # type: ignore
                draft_info[i][1] = second_round
            
            sheet_handler.update_draft_picks(team_name=self.team_name, edited_data=draft_info)

    def get_updates_from_Team(self, team_obj:Team):
        """
        Get relevant values from Team Objects
        """
        for i, player in enumerate(team_obj.starters.forwards):
            self.starters["Forwards"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.starters.midfielders):
            self.starters["Midfielders"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.starters.defense):
            self.starters["Defense"][i].update_from_Player(player) # type: ignore
        self.starters["Goalie"].update_from_Player(team_obj.starters.goalie) # type: ignore

        for i, player in enumerate(team_obj.bench.forwards):
            self.bench["Forwards"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.bench.midfielders):
            self.bench["Midfielders"][i].update_from_Player(player) # type: ignore
        for i, player in enumerate(team_obj.bench.defense):
            self.bench["Defense"][i].update_from_Player(player) # type: ignore
        self.bench["Goalie"].update_from_Player(team_obj.bench.goalie) # type: ignore

        for i, player in enumerate(team_obj.reserves):
            self.reserve[i].update_from_Player(player)

        self.record = team_obj.record

        # TODO: Should we update draft picks here? Unlikely...




@dataclass
class Subroster():
    # Lists hold player objects
    forwards: list
    midfielders: list
    defense: list
    goalie: Player

    @classmethod
    def from_TeamInfo(cls, subroster_type, team_dict):
        """ Create each subroster object from the data contained in the Team Info object """
        return cls(
            forwards=[Player.from_dict(team_dict[playerinfo.name]) for playerinfo in subroster_type["Forwards"]],
            midfielders=[Player.from_dict(team_dict[playerinfo.name]) for playerinfo in subroster_type["Midfielders"]],
            defense=[Player.from_dict(team_dict[playerinfo.name]) for playerinfo in subroster_type["Defense"]],
            goalie=Player.from_dict(team_dict[subroster_type["Goalie"].name])
        )
    
    def update_team_dict(self, new_dict):
        for (forward, middie, defender) in zip(self.forwards, self.midfielders, self.defense):
            new_dict[forward.name] = forward.to_dict()
            new_dict[middie.name] = middie.to_dict()
            new_dict[defender.name] = defender.to_dict()
        new_dict[self.goalie.name] = self.goalie.to_dict()
            
    


    
@dataclass
class Team():

    team_name: str
    starters: Subroster
    bench: Subroster
    reserves: list # list of player objects
    draft_picks: dict|None # keys: "1st Round" and "2nd Round"
    record: list[int] # wins, losses, ties

    def win(self):
        self.record[0] += 1

    def lose(self):
        self.record[1] += 1

    def tie(self):
        self.record[2] += 1

    def all_players(self):
        """Return a flat list of every Player on the team (starters, bench, reserves)."""
        players = []
        for subroster in (self.starters, self.bench):
            players.extend(subroster.forwards)
            players.extend(subroster.midfielders)
            players.extend(subroster.defense)
            players.append(subroster.goalie)
        players.extend(self.reserves)
        return players

    # ------------------------------------------------------------------
    # Injury substitution (next-man-up by position)
    # ------------------------------------------------------------------
    # Map a player's position to the Subroster attribute holding that group.
    _POSITION_GROUP = {
        "Forward": "forwards",
        "Midfielder": "midfielders",
        "Defense": "defense",
        "Goalie": "goalie",
    }

    def _subroster(self, tier):
        return self.starters if tier == "starters" else self.bench

    def _get_slot(self, tier, group, index):
        sub = self._subroster(tier)
        if group == "goalie":
            return sub.goalie
        return getattr(sub, group)[index]

    def _set_slot(self, tier, group, index, player):
        sub = self._subroster(tier)
        if group == "goalie":
            sub.goalie = player
        else:
            getattr(sub, group)[index] = player

    def _locate_active(self, player):
        """Find a player among starters/bench by identity: (tier, group, index)."""
        for tier in ("starters", "bench"):
            sub = self._subroster(tier)
            for group in ("forwards", "midfielders", "defense"):
                for i, p in enumerate(getattr(sub, group)):
                    if p is player:
                        return tier, group, i
            if sub.goalie is player:
                return tier, "goalie", None
        return None

    def _find_healthy_reserve(self, position):
        for i, p in enumerate(self.reserves):
            if p.position == position and not p.is_injured:
                return i, p
        return None

    def _find_healthy_bench(self, group):
        sub = self.bench
        if group == "goalie":
            g = sub.goalie
            return (None, g) if (g is not None and not g.is_injured) else None
        for i, p in enumerate(getattr(sub, group)):
            if not p.is_injured:
                return i, p
        return None

    def _pop_reserve(self, index):
        return self.reserves.pop(index)

    def apply_injury_substitution(self, injured):
        """
        Substitute an injured active player using next-man-up by position:
          - starter injured: bench->starter, reserve->bench, injured->reserves
          - bench injured:   reserve->bench, injured->reserves

        Returns a reversal record (used by reverse_injury_substitution), or None
        if the player isn't an active player or there is no healthy reserve of
        their position to backfill (in which case they keep playing — hurt).
        """
        group = self._POSITION_GROUP.get(injured.position)
        if group is None:
            return None
        loc = self._locate_active(injured)
        if loc is None:
            return None
        tier, grp, idx = loc

        res = self._find_healthy_reserve(injured.position)
        if res is None:
            return None  # no replacement depth -> player plays hurt
        res_idx, reserve_player = res

        if tier == "starters":
            bench = self._find_healthy_bench(grp)
            if bench is None:
                return None
            b_idx, bench_player = bench
            self._set_slot("starters", grp, idx, bench_player)
            self._set_slot("bench", grp, b_idx, reserve_player)
            self._pop_reserve(res_idx)
            self.reserves.append(injured)
            return {
                "type": "starter", "group": grp,
                "injured": injured.name, "starter_index": idx,
                "bench_player": bench_player.name, "bench_index": b_idx,
                "reserve_player": reserve_player.name, "reserve_index": res_idx,
            }

        # bench injury
        self._set_slot("bench", grp, idx, reserve_player)
        self._pop_reserve(res_idx)
        self.reserves.append(injured)
        return {
            "type": "bench", "group": grp,
            "injured": injured.name, "bench_index": idx,
            "reserve_player": reserve_player.name, "reserve_index": res_idx,
        }

    def reverse_injury_substitution(self, record):
        """
        Undo a substitution (manual, on a recovered player): players return to
        their original slots. Resolves players by name from the current roster
        so the record can be persisted/reloaded. Returns True on success.
        """
        by_name = {p.name: p for p in self.all_players()}
        injured = by_name.get(record["injured"])
        if injured is None:
            return False
        grp = record["group"]

        # Remove the (now-recovered) injured player from reserves by identity.
        for i, p in enumerate(self.reserves):
            if p is injured:
                self.reserves.pop(i)
                break

        if record["type"] == "starter":
            bench_player = by_name.get(record["bench_player"])
            reserve_player = by_name.get(record["reserve_player"])
            if bench_player is None or reserve_player is None:
                return False
            self._set_slot("starters", grp, record["starter_index"], injured)
            self._set_slot("bench", grp, record["bench_index"], bench_player)
        else:
            reserve_player = by_name.get(record["reserve_player"])
            if reserve_player is None:
                return False
            self._set_slot("bench", grp, record["bench_index"], injured)

        insert_at = min(record["reserve_index"], len(self.reserves))
        self.reserves.insert(insert_at, reserve_player)
        return True


    @classmethod
    def from_TeamInfo(cls, team_info:TeamInfo):
        """ Get Team object from Team Info object """
        datafile_path = f"/Users/oliverhvidsten/Documents/handball/handball/datafiles/{team_info.team_name.lower()}.json"
        try:
            with open(datafile_path, "r") as f:
                team_dict = json.load(f)
        except FileNotFoundError:
            # Only synthetic/example teams are allowed to be constructed without a
            # backing JSON datafile. For all "real" teams this is a hard error.
            if team_info.team_name.upper() not in {"EXAMPLE"}:
                raise FileNotFoundError(
                    f"No team data JSON found for '{team_info.team_name}' at '{datafile_path}'. "
                    "Real teams must have a persisted team_dict; this is a configuration error."
                )

            # Fallback for synthetic/example teams: synthesize a minimal team_dict
            # from the PlayerInfo objects. This is intentionally lossy and should
            # only be used for demo/testing sheets like 'EXAMPLE'.
            team_dict = {}

            def add_playerinfo(pi):
                if not hasattr(pi, "name") or not pi.name:
                    return
                if pi.name in team_dict:
                    return
                # Create a rough Player object from the available PlayerInfo data.
                # This is primarily used for test/example teams where no JSON exists.
                base_off = float(getattr(pi, "offense", 1.0))
                base_def = float(getattr(pi, "defense", 1.0))
                base_goalie = float(getattr(pi, "goalie_skill", 0.1))
                player = Player(
                    name=pi.name,
                    age=getattr(pi, "age", 25),
                    years_in_league=0,
                    height=70,
                    weight=175,
                    position=getattr(pi, "position", "Forward"),
                    offense=base_off,
                    defense=base_def,
                    goalie_skill=base_goalie,
                    max_offense=base_off,
                    max_defense=base_def,
                    max_goalie_skill=base_goalie,
                    variance=0.5,
                )
                team_dict[pi.name] = player.to_dict()

            for plist in team_info.starters.values():
                if isinstance(plist, list):
                    for pi in plist:
                        add_playerinfo(pi)
                else:
                    add_playerinfo(plist)
            for plist in team_info.bench.values():
                if isinstance(plist, list):
                    for pi in plist:
                        add_playerinfo(pi)
                else:
                    add_playerinfo(plist)
            for pi in team_info.reserve:
                add_playerinfo(pi)

        # Build reserve player objects, supporting both PlayerInfo and str entries.
        reserve_players = []
        for res in team_info.reserve:
            if isinstance(res, PlayerInfo):
                key = res.name
            else:
                key = res
            if key in team_dict:
                reserve_players.append(Player.from_dict(team_dict[key]))

        return cls(
            team_name=team_info.team_name,
            starters=Subroster.from_TeamInfo(team_info.starters, team_dict),
            bench=Subroster.from_TeamInfo(team_info.bench, team_dict),
            reserves=reserve_players,
            draft_picks=team_info.draft_picks, # might be None if TeamInfo did not scrape this data
            record=team_info.record
        )
    
    def update_team_JSON(self):
        """ Write data from Team object to JSON"""
        updated_dict = dict() #create empty dictionary to hold player data
        self.starters.update_team_dict(updated_dict) #update starters dictionary
        self.bench.update_team_dict(updated_dict) #update bench dictionary
        for reserve in self.reserves:
            updated_dict[reserve.name] = reserve.to_dict() #update reserve dictionary

        # Ensure everything is JSON-serializable (cast NumPy scalars, etc.)
        def _make_json_safe(obj):
            if isinstance(obj, dict):
                return {k: _make_json_safe(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_make_json_safe(v) for v in obj]
            # Handle NumPy scalar types by casting to Python scalars
            try:
                import numpy as np  # type: ignore
                if isinstance(obj, (np.generic,)):
                    return obj.item()
            except Exception:
                pass
            return obj

        safe_dict = _make_json_safe(updated_dict)

        with open(f"/Users/oliverhvidsten/Documents/handball/handball/datafiles/{self.team_name.lower()}.json", "w") as f: #write to JSON file
            json.dump(safe_dict, f)

    # Backwards-compatible alias used in some tests
    def update_team_dict(self):
        """Alias for update_team_JSON to maintain test compatibility."""
        self.update_team_JSON()


    def update_performances(self, performances):
        """ Add the individual game performances to the players' objects """
        for i, performance in enumerate(performances):
            if i < 3:
                self.starters.forwards[i].current_season_log["performances"].append(performance)
            elif i < 6:
                self.starters.midfielders[i-3].current_season_log["performances"].append(performance)
            elif i < 9:
                self.starters.defense[i-6].current_season_log["performances"].append(performance)
            elif i < 11:
                self.bench.forwards[i-9].current_season_log["performances"].append(performance)
            elif i < 13:
                self.bench.midfielders[i-11].current_season_log["performances"].append(performance)
            elif i < 15:
                self.bench.defense[i-13].current_season_log["performances"].append(performance)
            elif i == 15:
                self.starters.goalie.current_season_log["performances"].append(performance)
            else:
                self.bench.goalie.current_season_log["performances"].append(performance)
        
        # Reserves do not play, set performances to 0
        for reserve in self.reserves:
            reserve.current_season_log["performances"].append(0)





    def update_offensive_stats(self, goals_scored, shots_taken):
        """ 
        Add the goals scored by all of the eligible goal scorers as well as how many shots they took
        Additionally, put 0 for all offensive records of non-offensive players
        """
        
        for i, (goals, shots) in enumerate(zip(goals_scored, shots_taken)):
            if i < 3:
                self.starters.forwards[i].current_season_log["goals"].append(goals)
                self.starters.forwards[i].current_season_log["shots_taken"].append(shots)
            elif i < 6:
                self.starters.midfielders[i-3].current_season_log["goals"].append(goals)
                self.starters.midfielders[i-3].current_season_log["shots_taken"].append(shots)
            elif i < 8:
                self.bench.forwards[i-6].current_season_log["goals"].append(goals)
                self.bench.forwards[i-6].current_season_log["shots_taken"].append(shots)
            else:
                self.bench.midfielders[i-8].current_season_log["goals"].append(goals)
                self.bench.midfielders[i-8].current_season_log["shots_taken"].append(shots)

        # set all goals scored to 0 for defenders, goalies, and reserves
        for defender in self.starters.defense:
            defender.current_season_log["goals"].append(0)
            defender.current_season_log["shots_taken"].append(0)
        for defender in self.bench.defense:
            defender.current_season_log["goals"].append(0)
            defender.current_season_log["shots_taken"].append(0)

        self.starters.goalie.current_season_log["goals"].append(0)
        self.starters.goalie.current_season_log["shots_taken"].append(0)
        self.bench.goalie.current_season_log["goals"].append(0)
        self.bench.goalie.current_season_log["shots_taken"].append(0)

        for reserve in self.reserves:
            reserve.current_season_log["goals"].append(0)
            reserve.current_season_log["shots_taken"].append(0)

    def update_goalie_stats(self, saves: int, goals_allowed: int):
        """
        Update goalie stats after a game.
        Both starting and bench goalies play during a game (halftime swap),
        so we split the stats proportionally (starter gets 60%, bench gets 40%).
        """
        # Approximate split: starter plays ~60% of game, bench plays ~40%
        starter_saves = int(round(saves * 0.6))
        bench_saves = saves - starter_saves
        starter_goals_allowed = int(round(goals_allowed * 0.6))
        bench_goals_allowed = goals_allowed - starter_goals_allowed

        self.starters.goalie.current_season_log["saves"].append(starter_saves)
        self.starters.goalie.current_season_log["goals_allowed"].append(starter_goals_allowed)
        self.bench.goalie.current_season_log["saves"].append(bench_saves)
        self.bench.goalie.current_season_log["goals_allowed"].append(bench_goals_allowed)