"""
Offline tests for DraftService and PlayoffService on the new stack: deterministic
via seeding, no sheet, no archive.

`pytest tests/test_postseason.py` or `python tests/test_postseason.py`.
"""
import random

import numpy as np
import pytest

from handball.domain import Player, Team
from handball.orchestration import SimpleGameEngine
from handball.postseason import DraftService, PlayoffService
from handball.repository import InMemoryTeamRepository


# --- DraftService ----------------------------------------------------------
def test_draft_order_is_reverse_standings():
    np.random.seed(0)
    random.seed(0)
    ranked = ["A", "B", "C", "D"]   # A best, D worst
    prospects = [(f"Rookie{i}", "Forward") for i in range(8)]
    picks = DraftService().run(ranked, prospects, rounds=2)

    assert len(picks) == 8
    # worst team (D) holds the first pick of each round
    first_round = [p for p in picks if p.round_num == 1]
    assert first_round[0].holder_team_id == "D"
    assert first_round[-1].holder_team_id == "A"
    # overall numbering is contiguous
    assert [p.overall for p in picks] == list(range(1, 9))


def test_draft_honors_pick_ownership():
    np.random.seed(0)
    random.seed(0)
    ranked = ["A", "B"]
    # B owns A's first-round slot (a trade)
    ownership = {1: {"A": "B"}}
    picks = DraftService().run(ranked, [("X", "Forward"), ("Y", "Forward")],
                               rounds=1, pick_ownership=ownership)
    by_original = {p.original_team_id: p.holder_team_id for p in picks}
    assert by_original["A"] == "B"     # A's slot was made by B
    assert by_original["B"] == "B"


def test_draft_players_are_domain_players_with_rookie_contracts():
    np.random.seed(0)
    random.seed(0)
    picks = DraftService().run(["A", "B"], [("Jordan Quick", "Goalie")], rounds=1)
    p = picks[0].player
    assert isinstance(p, Player)
    assert p.position == "Goalie"
    assert p.rookie_contract is True
    assert p.years_remaining == 3
    assert p.id.startswith("b-")       # stable id keyed off holder + name (worst picks first -> B)


def test_draft_stops_when_prospects_run_out():
    np.random.seed(0)
    random.seed(0)
    picks = DraftService().run(["A", "B", "C"], [("Only", "Forward")], rounds=2)
    assert len(picks) == 1


def test_draft_ids_are_unique_even_with_duplicate_names():
    np.random.seed(0)
    random.seed(0)
    # same rookie name drafted by the same team twice -> ids must not collide
    picks = DraftService().run(["A"], [("Sam Reed", "Forward"), ("Sam Reed", "Defense")], rounds=2)
    ids = [p.player.id for p in picks]
    assert len(ids) == len(set(ids))


# --- PlayoffService --------------------------------------------------------
def _team(tid, strength):
    def p(pid, pos, off=5.0, gk=0.1):
        return Player(id=pid, name=pid, position=pos, offense=off, goalie_skill=gk)
    return Team(
        id=tid, name=tid, coaches=[],
        starters={
            "Forward": [p(f"{tid}-sf{i}", "Forward", off=strength) for i in range(3)],
            "Midfielder": [p(f"{tid}-sm{i}", "Midfielder", off=strength) for i in range(3)],
            "Defense": [p(f"{tid}-sd{i}", "Defense") for i in range(3)],
            "Goalie": [p(f"{tid}-sg", "Goalie", gk=3.0)],
        },
        bench={
            "Forward": [p(f"{tid}-bf{i}", "Forward") for i in range(2)],
            "Midfielder": [p(f"{tid}-bm{i}", "Midfielder") for i in range(2)],
            "Defense": [p(f"{tid}-bd{i}", "Defense") for i in range(2)],
            "Goalie": [p(f"{tid}-bg", "Goalie", gk=2.0)],
        },
        reserves=[],
    )


@pytest.fixture
def repo_and_conf():
    # 4 teams per conference, two conferences. Strength decreases down the list,
    # so seeding == strength order and the top seed wins everything.
    repo = InMemoryTeamRepository()
    east = ["E1", "E2", "E3", "E4"]
    west = ["W1", "W2", "W3", "W4"]
    strength = 12.0
    ranked = []
    # overall ranking strictly by strength (alternating conferences as it falls)
    ordered = ["E1", "W1", "E2", "W2", "E3", "W3", "E4", "W4"]
    for tid in ordered:
        repo.save(_team(tid, strength))
        strength -= 1.0
        ranked.append(tid)
    conf = {**{t: "East" for t in east}, **{t: "West" for t in west}}
    return repo, ranked, conf


def test_seed_takes_top_n_per_conference(repo_and_conf):
    repo, ranked, conf = repo_and_conf
    svc = PlayoffService(SimpleGameEngine(), conf.get, teams_per_conference=4)
    seeded = svc.seed(ranked)
    assert seeded["East"] == ["E1", "E2", "E3", "E4"]
    assert seeded["West"] == ["W1", "W2", "W3", "W4"]


def test_bracket_produces_a_champion_and_leaves_repo_untouched(repo_and_conf):
    repo, ranked, conf = repo_and_conf
    before = {t: repo.load(t).record for t in ranked}

    svc = PlayoffService(SimpleGameEngine(), conf.get, teams_per_conference=4)
    bracket = svc.run(repo, ranked)

    # top seed of each conference wins out (strongest), and the overall #1 wins
    assert bracket.conference_champions["East"] == "E1"
    assert bracket.conference_champions["West"] == "W1"
    assert bracket.champion == "E1"
    # quarterfinals(2)+semis... here 4-team: 2 rounds/conf = (2+1)*2 series + final
    assert any(s.label == "Final" for s in bracket.series)
    # canonical records never mutated (playoffs ran on throwaway copies)
    after = {t: repo.load(t).record for t in ranked}
    assert before == after == {t: (0, 0, 0) for t in ranked}


def test_higher_seed_hosts_and_advances_on_tie():
    # two identical teams -> tie under SimpleGameEngine -> higher seed advances
    repo = InMemoryTeamRepository()
    repo.save(_team("E1", 5.0))
    repo.save(_team("E2", 5.0))
    conf = {"E1": "East", "E2": "East"}
    svc = PlayoffService(SimpleGameEngine(), conf.get, teams_per_conference=2)
    bracket = svc.run(repo, ["E1", "E2"])
    assert bracket.conference_champions["East"] == "E1"   # equal strength -> host wins


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
