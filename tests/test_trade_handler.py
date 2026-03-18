"""
Tests for handball.trade_handler — TradeHandler, TradePackage, and helpers.

All tests use synthetic teams with mocked persistence (JSON writes and
Sheet updates are patched out) so they run offline and quickly.
"""

import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from handball.players import Player, PlayerInfo
from handball.teams import Team, Subroster, TeamInfo
from handball.trade_handler import (
    DraftPickMovement,
    PlayerMovement,
    TradeHandler,
    TradePackage,
    _SlotAddress,
    MAX_ROSTER,
)
from handball.free_agency import FreeAgencyHandler


# ======================================================================
# Helpers — synthetic player / team factories
# ======================================================================

_PLAYER_COUNTER = 0


def _make_player(name: str, position: str = "Forward", offense: float = 5.0,
                 defense: float = 4.0, goalie_skill: float = 0.1) -> Player:
    global _PLAYER_COUNTER
    _PLAYER_COUNTER += 1
    return Player(
        name=name,
        age=25,
        years_in_league=3,
        height=72,
        weight=180,
        position=position,
        offense=offense,
        defense=defense,
        goalie_skill=goalie_skill,
        max_offense=offense + 1,
        max_defense=defense + 1,
        max_goalie_skill=goalie_skill,
        variance=0.5,
    )


def _make_full_team(prefix: str, draft_picks=None) -> Team:
    """Build a fully-populated 21-player team."""
    if draft_picks is None:
        draft_picks = {
            "1st Round": [f"{prefix} 2026", f"{prefix} 2027"],
            "2nd Round": [f"{prefix} 2026"],
        }
    return Team(
        team_name=prefix,
        starters=Subroster(
            forwards=[_make_player(f"{prefix}_SF{i}", "Forward") for i in range(3)],
            midfielders=[_make_player(f"{prefix}_SM{i}", "Midfielder") for i in range(3)],
            defense=[_make_player(f"{prefix}_SD{i}", "Defense") for i in range(3)],
            goalie=_make_player(f"{prefix}_SG", "Goalie", 0.1, 0.1, 7.0),
        ),
        bench=Subroster(
            forwards=[_make_player(f"{prefix}_BF{i}", "Forward") for i in range(2)],
            midfielders=[_make_player(f"{prefix}_BM{i}", "Midfielder") for i in range(2)],
            defense=[_make_player(f"{prefix}_BD{i}", "Defense") for i in range(2)],
            goalie=_make_player(f"{prefix}_BG", "Goalie", 0.1, 0.1, 5.0),
        ),
        reserves=[
            _make_player(f"{prefix}_R{i}", "Forward") for i in range(4)
        ],
        draft_picks=draft_picks,
        record=[10, 5, 0],
    )


def _make_team_with_vacancy(prefix: str) -> Team:
    """A team with one fewer starter forward (slot 2 is None)."""
    team = _make_full_team(prefix)
    team.starters.forwards[2] = None  # type: ignore[assignment]
    return team


def _mock_handler(teams_dict, free_agency=None):
    """Create a TradeHandler with mocked sheet_handler and team_infos."""
    sh = MagicMock(spec=[])
    team_infos = {}
    return TradeHandler(
        teams=teams_dict,
        team_infos=team_infos,
        sheet_handler=sh,
        free_agency=free_agency,
    )


# ======================================================================
# _find_player_slot
# ======================================================================

class TestFindPlayerSlot:
    def test_find_starter_forward(self):
        team = _make_full_team("A")
        slot = TradeHandler._find_player_slot(team, "A_SF1")
        assert slot is not None
        assert slot.tier == "starters"
        assert slot.group == "Forwards"
        assert slot.index == 1

    def test_find_starter_goalie(self):
        team = _make_full_team("A")
        slot = TradeHandler._find_player_slot(team, "A_SG")
        assert slot is not None
        assert slot.tier == "starters"
        assert slot.group == "Goalie"

    def test_find_bench_midfielder(self):
        team = _make_full_team("A")
        slot = TradeHandler._find_player_slot(team, "A_BM0")
        assert slot is not None
        assert slot.tier == "bench"
        assert slot.group == "Midfielders"
        assert slot.index == 0

    def test_find_bench_goalie(self):
        team = _make_full_team("A")
        slot = TradeHandler._find_player_slot(team, "A_BG")
        assert slot is not None
        assert slot.tier == "bench"
        assert slot.group == "Goalie"

    def test_find_reserve(self):
        team = _make_full_team("A")
        slot = TradeHandler._find_player_slot(team, "A_R2")
        assert slot is not None
        assert slot.tier == "reserves"
        assert slot.index == 2

    def test_player_not_found(self):
        team = _make_full_team("A")
        assert TradeHandler._find_player_slot(team, "NOBODY") is None

    def test_find_in_team_with_none_slots(self):
        team = _make_team_with_vacancy("A")
        slot = TradeHandler._find_player_slot(team, "A_SF0")
        assert slot is not None
        assert slot.tier == "starters"
        assert slot.group == "Forwards"
        assert slot.index == 0


# ======================================================================
# _roster_size
# ======================================================================

class TestRosterSize:
    def test_full_team(self):
        team = _make_full_team("A")
        assert TradeHandler._roster_size(team) == 21

    def test_team_with_vacancy(self):
        team = _make_team_with_vacancy("A")
        assert TradeHandler._roster_size(team) == 20

    def test_empty_reserves(self):
        team = _make_full_team("A")
        team.reserves = [None, None, None, None]
        assert TradeHandler._roster_size(team) == 17

    def test_missing_goalie(self):
        team = _make_full_team("A")
        team.bench.goalie = None  # type: ignore[assignment]
        assert TradeHandler._roster_size(team) == 20


# ======================================================================
# _remove_player
# ======================================================================

class TestRemovePlayer:
    def test_remove_starter_forward(self):
        team = _make_full_team("A")
        slot = _SlotAddress(tier="starters", group="Forwards", index=0)
        player = TradeHandler._remove_player(team, slot)
        assert player.name == "A_SF0"
        assert team.starters.forwards[0] is None

    def test_remove_starter_goalie(self):
        team = _make_full_team("A")
        slot = _SlotAddress(tier="starters", group="Goalie", index=0)
        player = TradeHandler._remove_player(team, slot)
        assert player.name == "A_SG"
        assert team.starters.goalie is None

    def test_remove_bench_defender(self):
        team = _make_full_team("A")
        slot = _SlotAddress(tier="bench", group="Defense", index=1)
        player = TradeHandler._remove_player(team, slot)
        assert player.name == "A_BD1"
        assert team.bench.defense[1] is None

    def test_remove_reserve(self):
        team = _make_full_team("A")
        slot = _SlotAddress(tier="reserves", group="reserves", index=3)
        player = TradeHandler._remove_player(team, slot)
        assert player.name == "A_R3"
        assert team.reserves[3] is None


# ======================================================================
# _slot_is_empty / _set_slot
# ======================================================================

class TestSlotOperations:
    def test_slot_is_empty_true(self):
        team = _make_team_with_vacancy("A")
        slot = _SlotAddress(tier="starters", group="Forwards", index=2)
        assert TradeHandler._slot_is_empty(team, slot) is True

    def test_slot_is_empty_false(self):
        team = _make_full_team("A")
        slot = _SlotAddress(tier="starters", group="Forwards", index=0)
        assert TradeHandler._slot_is_empty(team, slot) is False

    def test_slot_is_empty_goalie_vacant(self):
        team = _make_full_team("A")
        team.bench.goalie = None  # type: ignore[assignment]
        slot = _SlotAddress(tier="bench", group="Goalie", index=0)
        assert TradeHandler._slot_is_empty(team, slot) is True

    def test_set_slot_forward(self):
        team = _make_team_with_vacancy("A")
        new_player = _make_player("NEW_GUY", "Forward")
        slot = _SlotAddress(tier="starters", group="Forwards", index=2)
        TradeHandler._set_slot(team, slot, new_player)
        assert team.starters.forwards[2].name == "NEW_GUY"

    def test_set_slot_goalie(self):
        team = _make_full_team("A")
        team.starters.goalie = None  # type: ignore[assignment]
        new_player = _make_player("NEW_GOALIE", "Goalie", 0.1, 0.1, 8.0)
        slot = _SlotAddress(tier="starters", group="Goalie", index=0)
        TradeHandler._set_slot(team, slot, new_player)
        assert team.starters.goalie.name == "NEW_GOALIE"

    def test_set_slot_reserve(self):
        team = _make_full_team("A")
        team.reserves[0] = None  # type: ignore[assignment]
        new_player = _make_player("R_NEW", "Midfielder")
        slot = _SlotAddress(tier="reserves", group="reserves", index=0)
        TradeHandler._set_slot(team, slot, new_player)
        assert team.reserves[0].name == "R_NEW"


# ======================================================================
# _place_player
# ======================================================================

class TestPlacePlayer:
    def test_place_into_preferred_slot(self):
        team = _make_team_with_vacancy("A")
        handler = _mock_handler({"A": team})
        new_player = _make_player("INCOMING", "Forward")
        slot = _SlotAddress(tier="starters", group="Forwards", index=2)
        handler._place_player(team, new_player, preferred=slot)
        assert team.starters.forwards[2].name == "INCOMING"

    def test_place_finds_open_starter_slot(self):
        team = _make_team_with_vacancy("A")
        handler = _mock_handler({"A": team})
        new_player = _make_player("INCOMING", "Forward")
        handler._place_player(team, new_player, preferred=None)
        assert team.starters.forwards[2].name == "INCOMING"

    def test_place_falls_back_to_reserve(self):
        team = _make_full_team("A")
        team.reserves[3] = None  # type: ignore[assignment]
        handler = _mock_handler({"A": team})
        new_player = _make_player("INCOMING", "Forward")
        handler._place_player(team, new_player, preferred=None)
        assert team.reserves[3].name == "INCOMING"

    def test_place_goalie_into_empty_starter_goalie(self):
        team = _make_full_team("A")
        team.starters.goalie = None  # type: ignore[assignment]
        handler = _mock_handler({"A": team})
        new_goalie = _make_player("NEW_G", "Goalie", 0.1, 0.1, 6.0)
        handler._place_player(team, new_goalie, preferred=None)
        assert team.starters.goalie.name == "NEW_G"

    def test_place_raises_when_full(self):
        team = _make_full_team("A")
        handler = _mock_handler({"A": team})
        extra = _make_player("EXTRA", "Forward")
        with pytest.raises(RuntimeError, match="No open roster slot"):
            handler._place_player(team, extra, preferred=None)


# ======================================================================
# Validation
# ======================================================================

class TestValidation:
    def test_source_team_not_found(self):
        handler = _mock_handler({})
        pkg = TradePackage(
            players=[PlayerMovement("X", from_team="Ghost", to_team=None)]
        )
        with pytest.raises(ValueError, match="Source team 'Ghost' not found"):
            handler._validate(pkg)

    def test_destination_team_not_found(self):
        team_a = _make_full_team("A")
        handler = _mock_handler({"A": team_a})
        pkg = TradePackage(
            players=[PlayerMovement("A_SF0", from_team="A", to_team="Ghost")]
        )
        with pytest.raises(ValueError, match="Destination team 'Ghost' not found"):
            handler._validate(pkg)

    def test_player_not_on_team(self):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})
        pkg = TradePackage(
            players=[PlayerMovement("NOBODY", from_team="A", to_team="B")]
        )
        with pytest.raises(ValueError, match="is not on 'A'"):
            handler._validate(pkg)

    def test_release_without_free_agency_handler(self):
        team_a = _make_full_team("A")
        handler = _mock_handler({"A": team_a}, free_agency=None)
        pkg = TradePackage(
            players=[PlayerMovement("A_SF0", from_team="A", to_team=None)]
        )
        with pytest.raises(ValueError, match="FreeAgencyHandler was provided"):
            handler._validate(pkg)

    def test_roster_overflow_rejected(self):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})
        # A sends nobody but receives two — net +2 on a 21-player roster
        pkg = TradePackage(
            players=[
                PlayerMovement("B_R0", from_team="B", to_team="A"),
                PlayerMovement("B_R1", from_team="B", to_team="A"),
            ]
        )
        with pytest.raises(ValueError, match="exceeding the 21-player roster limit"):
            handler._validate(pkg)

    def test_draft_pick_source_team_not_found(self):
        team_a = _make_full_team("A")
        handler = _mock_handler({"A": team_a})
        pkg = TradePackage(
            draft_picks=[DraftPickMovement("X 2026", "1st Round", "Ghost", "A")]
        )
        with pytest.raises(ValueError, match="Source team 'Ghost' not found"):
            handler._validate(pkg)


# ======================================================================
# _move_draft_pick
# ======================================================================

class TestMoveDraftPick:
    def test_basic_pick_move(self):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})
        dp = DraftPickMovement("A 2026", "1st Round", from_team="A", to_team="B")
        handler._move_draft_pick(dp)
        assert "A 2026" not in team_a.draft_picks["1st Round"]
        assert "A 2026" in team_b.draft_picks["1st Round"]

    def test_pick_not_found_raises(self):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})
        dp = DraftPickMovement("FAKE 2030", "1st Round", from_team="A", to_team="B")
        with pytest.raises(ValueError, match="Draft pick 'FAKE 2030'"):
            handler._move_draft_pick(dp)

    def test_initializes_none_draft_picks(self):
        team_a = _make_full_team("A")
        team_a.draft_picks = None
        team_b = _make_full_team("B")
        team_b.draft_picks = None
        handler = _mock_handler({"A": team_a, "B": team_b})
        # After initialization, picks won't exist, so moving should fail
        dp = DraftPickMovement("X", "1st Round", from_team="A", to_team="B")
        with pytest.raises(ValueError):
            handler._move_draft_pick(dp)
        # But the dicts should have been initialized
        assert team_a.draft_picks == {"1st Round": [], "2nd Round": []}
        assert team_b.draft_picks == {"1st Round": [], "2nd Round": []}

    def test_move_second_round_pick(self):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})
        dp = DraftPickMovement("A 2026", "2nd Round", from_team="A", to_team="B")
        handler._move_draft_pick(dp)
        assert "A 2026" not in team_a.draft_picks["2nd Round"]
        assert "A 2026" in team_b.draft_picks["2nd Round"]


# ======================================================================
# execute_trade — full integration (persistence mocked)
# ======================================================================

class TestExecuteTrade:
    @patch.object(TradeHandler, "_persist_team")
    def test_simple_one_for_one_swap(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(players=[
            PlayerMovement("A_SF0", from_team="A", to_team="B"),
            PlayerMovement("B_SF0", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        # A's starter forward slot 0 should now hold B's former player
        assert team_a.starters.forwards[0].name == "B_SF0"
        assert team_b.starters.forwards[0].name == "A_SF0"
        assert mock_persist.call_count == 2

    @patch.object(TradeHandler, "_persist_team")
    def test_trade_with_draft_picks(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(
            players=[
                PlayerMovement("A_R0", from_team="A", to_team="B"),
                PlayerMovement("B_R0", from_team="B", to_team="A"),
            ],
            draft_picks=[
                DraftPickMovement("A 2026", "1st Round", from_team="A", to_team="B"),
            ],
        )
        handler.execute_trade(pkg)

        assert "A 2026" in team_b.draft_picks["1st Round"]
        assert "A 2026" not in team_a.draft_picks["1st Round"]

    @patch.object(TradeHandler, "_persist_team")
    def test_release_to_free_agency(self, mock_persist):
        team_a = _make_full_team("A")
        mock_fa = MagicMock(spec=FreeAgencyHandler)
        handler = _mock_handler({"A": team_a}, free_agency=mock_fa)

        pkg = TradePackage(players=[
            PlayerMovement("A_R0", from_team="A", to_team=None),
        ])
        handler.execute_trade(pkg)

        mock_fa.add_player.assert_called_once()
        released = mock_fa.add_player.call_args[0][0]
        assert released.name == "A_R0"
        assert team_a.reserves[0] is None

    @patch.object(TradeHandler, "_persist_team")
    def test_cross_position_no_room_raises(self, mock_persist):
        """Cross-position trade to a full team with no matching or reserve
        vacancy correctly raises RuntimeError."""
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        # A sends a Forward, B sends a Defender.  B loses a Defense slot
        # but all Forward and reserve slots remain full — no room for the
        # incoming Forward.
        pkg = TradePackage(players=[
            PlayerMovement("A_SF0", from_team="A", to_team="B"),
            PlayerMovement("B_SD0", from_team="B", to_team="A"),
        ])
        with pytest.raises(RuntimeError, match="No open roster slot"):
            handler.execute_trade(pkg)

    @patch.object(TradeHandler, "_persist_team")
    def test_cross_position_trade_uses_reserve(self, mock_persist):
        """Defender goes to a team that lost a Forward — lands in reserve
        when a reserve slot is open."""
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        # Free up a reserve slot on BOTH teams so the cross-position players
        # have somewhere to land.
        team_a.reserves[3] = None  # type: ignore[assignment]
        team_b.reserves[3] = None  # type: ignore[assignment]
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(players=[
            PlayerMovement("A_SD0", from_team="A", to_team="B"),
            PlayerMovement("B_SF0", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        # A lost SD0 (Defense starter). B_SF0 is a Forward.
        # Preferred slot is starters/Defense/0 but group mismatch (Forwards != Defense).
        # Scans starters.forwards (full), bench.forwards (full), reserves[3] is None -> fills it.
        assert team_a.reserves[3].name == "B_SF0"
        # B lost SF0 (Forwards starter). A_SD0 is Defense.
        # Same logic: no Forwards match, lands in reserve.
        assert team_b.reserves[3].name == "A_SD0"

    @patch.object(TradeHandler, "_persist_team")
    def test_multi_team_trade(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(players=[
            PlayerMovement("A_R0", from_team="A", to_team="B"),
            PlayerMovement("A_R1", from_team="A", to_team="B"),
            PlayerMovement("B_R0", from_team="B", to_team="A"),
            PlayerMovement("B_R1", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        a_reserve_names = {r.name for r in team_a.reserves if r is not None}
        b_reserve_names = {r.name for r in team_b.reserves if r is not None}
        assert "B_R0" in a_reserve_names
        assert "B_R1" in a_reserve_names
        assert "A_R0" in b_reserve_names
        assert "A_R1" in b_reserve_names

    @patch.object(TradeHandler, "_persist_team")
    def test_trade_preserves_record_and_picks(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        original_a_record = team_a.record.copy()
        original_b_picks = deepcopy(team_b.draft_picks)
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(players=[
            PlayerMovement("A_R0", from_team="A", to_team="B"),
            PlayerMovement("B_R0", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        assert team_a.record == original_a_record
        assert team_b.draft_picks == original_b_picks

    @patch.object(TradeHandler, "_persist_team")
    def test_trade_reduces_roster_size(self, mock_persist):
        """Team sends a player to free agency, roster shrinks by 1."""
        team_a = _make_full_team("A")
        mock_fa = MagicMock(spec=FreeAgencyHandler)
        handler = _mock_handler({"A": team_a}, free_agency=mock_fa)

        pkg = TradePackage(players=[
            PlayerMovement("A_R3", from_team="A", to_team=None),
        ])
        handler.execute_trade(pkg)

        assert TradeHandler._roster_size(team_a) == 20

    @patch.object(TradeHandler, "_persist_team")
    def test_goalie_swap(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(players=[
            PlayerMovement("A_SG", from_team="A", to_team="B"),
            PlayerMovement("B_SG", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        assert team_a.starters.goalie.name == "B_SG"
        assert team_b.starters.goalie.name == "A_SG"


# ======================================================================
# Persistence — _write_team_json / _subroster_to_info_dict
# ======================================================================

class TestPersistence:
    def test_write_team_json_skips_none(self, tmp_path, monkeypatch):
        team = _make_full_team("testteam")
        team.starters.forwards[1] = None  # type: ignore[assignment]
        team.reserves[2] = None  # type: ignore[assignment]

        json_path = str(tmp_path / "testteam.json")
        monkeypatch.setattr(
            "handball.trade_handler.json.dump",
            json.dump,  # keep real json.dump
        )
        # Patch the path construction in _write_team_json
        import handball.trade_handler as th_mod

        original_write = th_mod.TradeHandler._write_team_json

        def patched_write(team_obj):
            import builtins
            real_open = builtins.open

            def fake_open(path, mode="r"):
                if "datafiles" in str(path) and mode == "w":
                    return real_open(json_path, mode)
                return real_open(path, mode)

            with monkeypatch.context() as m:
                m.setattr(builtins, "open", fake_open)
                original_write(team_obj)

        patched_write(team)

        with open(json_path, "r") as f:
            data = json.load(f)

        # None slots should not appear as keys
        assert "testteam_SF1" not in data
        assert "testteam_R2" not in data
        # Existing players should be present
        assert "testteam_SF0" in data
        assert "testteam_SG" in data

    def test_subroster_to_info_dict_starter(self):
        team = _make_full_team("A")
        result = TradeHandler._subroster_to_info_dict(team.starters, is_bench=False)
        assert len(result["Forwards"]) == 3
        assert len(result["Midfielders"]) == 3
        assert len(result["Defense"]) == 3
        assert isinstance(result["Goalie"], PlayerInfo)

    def test_subroster_to_info_dict_with_none(self):
        team = _make_full_team("A")
        team.starters.forwards[1] = None  # type: ignore[assignment]
        result = TradeHandler._subroster_to_info_dict(team.starters, is_bench=False)
        assert len(result["Forwards"]) == 3
        assert result["Forwards"][1].name == ""

    def test_subroster_to_info_dict_bench_pads(self):
        sub = Subroster(
            forwards=[_make_player("BF0", "Forward")],  # only 1, bench expects 2
            midfielders=[_make_player("BM0", "Midfielder"), _make_player("BM1", "Midfielder")],
            defense=[_make_player("BD0", "Defense"), _make_player("BD1", "Defense")],
            goalie=_make_player("BG", "Goalie", 0.1, 0.1, 5.0),
        )
        result = TradeHandler._subroster_to_info_dict(sub, is_bench=True)
        assert len(result["Forwards"]) == 2
        assert result["Forwards"][1].name == ""


# ======================================================================
# _pick_preferred_slot
# ======================================================================

class TestPickPreferredSlot:
    def test_preferred_matches_position_group(self):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})
        vacated = {
            "B": {"B_SF0": _SlotAddress(tier="starters", group="Forwards", index=0)},
        }
        incoming = _make_player("NEW", "Forward")
        result = handler._pick_preferred_slot("B", incoming, vacated)
        assert result is not None
        assert result.tier == "starters"
        assert result.group == "Forwards"

    def test_preferred_reserve_accepts_any_position(self):
        team_b = _make_full_team("B")
        handler = _mock_handler({"B": team_b})
        vacated = {
            "B": {"B_R0": _SlotAddress(tier="reserves", group="reserves", index=0)},
        }
        incoming = _make_player("NEW", "Midfielder")
        result = handler._pick_preferred_slot("B", incoming, vacated)
        assert result is not None
        assert result.tier == "reserves"

    def test_no_preferred_when_no_vacated_slots(self):
        handler = _mock_handler({})
        vacated: dict = {}
        incoming = _make_player("NEW", "Forward")
        result = handler._pick_preferred_slot("B", incoming, vacated)
        assert result is None

    def test_no_preferred_when_position_mismatch(self):
        team_b = _make_full_team("B")
        handler = _mock_handler({"B": team_b})
        vacated = {
            "B": {"B_SD0": _SlotAddress(tier="starters", group="Defense", index=0)},
        }
        incoming = _make_player("NEW", "Forward")
        result = handler._pick_preferred_slot("B", incoming, vacated)
        assert result is None


# ======================================================================
# Edge cases
# ======================================================================

class TestEdgeCases:
    @patch.object(TradeHandler, "_persist_team")
    def test_trade_only_draft_picks_no_players(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(
            draft_picks=[
                DraftPickMovement("A 2026", "1st Round", from_team="A", to_team="B"),
                DraftPickMovement("B 2026", "2nd Round", from_team="B", to_team="A"),
            ]
        )
        handler.execute_trade(pkg)

        assert "A 2026" in team_b.draft_picks["1st Round"]
        assert "B 2026" in team_a.draft_picks["2nd Round"]

    @patch.object(TradeHandler, "_persist_team")
    def test_uneven_trade_team_gains_player(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        mock_fa = MagicMock(spec=FreeAgencyHandler)
        handler = _mock_handler({"A": team_a, "B": team_b}, free_agency=mock_fa)

        # B gives R0 and R1 to A, A gives R0 to B and R1 to free agency
        # Net: A gains 1 (22 would overflow), so first remove from A to balance
        # Actually: A sends R0 to B (+0 net for B), A sends R1 to FA (-1 A).
        # B sends R0 to A (+1 A). Net A: -1+1 = 0. Net B: -1+1 = 0. Fine.
        pkg = TradePackage(players=[
            PlayerMovement("A_R0", from_team="A", to_team="B"),
            PlayerMovement("A_R1", from_team="A", to_team=None),
            PlayerMovement("B_R0", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        assert TradeHandler._roster_size(team_a) == 20
        assert mock_fa.add_player.call_count == 1

    @patch.object(TradeHandler, "_persist_team")
    def test_empty_trade_package(self, mock_persist):
        handler = _mock_handler({})
        pkg = TradePackage()
        handler.execute_trade(pkg)
        mock_persist.assert_not_called()

    @patch.object(TradeHandler, "_persist_team")
    def test_same_team_not_allowed_by_design(self, mock_persist):
        """Trading a player from a team to the same team is structurally
        fine (remove then re-add) but unusual. Verify it doesn't crash."""
        team_a = _make_full_team("A")
        handler = _mock_handler({"A": team_a})
        pkg = TradePackage(players=[
            PlayerMovement("A_R0", from_team="A", to_team="A"),
        ])
        handler.execute_trade(pkg)
        a_reserve_names = [r.name for r in team_a.reserves if r is not None]
        assert "A_R0" in a_reserve_names

    @patch.object(TradeHandler, "_persist_team")
    def test_bench_goalie_trade(self, mock_persist):
        team_a = _make_full_team("A")
        team_b = _make_full_team("B")
        handler = _mock_handler({"A": team_a, "B": team_b})

        pkg = TradePackage(players=[
            PlayerMovement("A_BG", from_team="A", to_team="B"),
            PlayerMovement("B_BG", from_team="B", to_team="A"),
        ])
        handler.execute_trade(pkg)

        assert team_a.bench.goalie.name == "B_BG"
        assert team_b.bench.goalie.name == "A_BG"
