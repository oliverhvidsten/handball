"""
The ranking rule (handball/standings.py) and the division-aware playoff seeding
(handball/postseason.seed_conferences), both DB-free.

The rule: points (3/1/0) -> head-to-head mini-table -> goal difference -> goals for
-> team id.
"""
import pytest

from handball.postseason import seed_conferences
from handball.standings import TeamStanding, rank_teams


def _s(team_id, w, l, t, gf=0, ga=0) -> TeamStanding:
    return TeamStanding(team_id, wins=w, losses=l, ties=t, goals_for=gf, goals_against=ga)


def _h2h(*results) -> dict:
    """(winner, loser) pairs, or (a, b, 'tie'). Builds both sides of each pair."""
    out: dict = {}
    for r in results:
        a, b = r[0], r[1]
        tie = len(r) > 2
        aw, al, at = out.get((a, b), (0, 0, 0))
        bw, bl, bt = out.get((b, a), (0, 0, 0))
        if tie:
            out[(a, b)], out[(b, a)] = (aw, al, at + 1), (bw, bl, bt + 1)
        else:
            out[(a, b)], out[(b, a)] = (aw + 1, al, at), (bw, bl + 1, bt)
    return out


# -- points ------------------------------------------------------------------
def test_points_are_three_for_a_win_one_for_a_tie():
    assert _s("A", 10, 0, 0).points == 30
    assert _s("A", 0, 0, 10).points == 10
    assert _s("A", 5, 3, 2).points == 17


def test_a_tie_heavy_team_can_outrank_a_win_heavy_one():
    """The reason the key changed: under wins-then-losses, A ranked above B."""
    a = _s("A", 30, 20, 5)      # 95 points
    b = _s("B", 29, 15, 11)     # 98 points
    assert rank_teams([a, b]) == ["B", "A"]


def test_ranking_is_best_to_worst():
    teams = [_s("A", 5, 5, 0), _s("B", 9, 1, 0), _s("C", 7, 3, 0)]
    assert rank_teams(teams) == ["B", "C", "A"]


# -- head-to-head ------------------------------------------------------------
def test_head_to_head_breaks_a_two_way_tie():
    teams = [_s("A", 10, 5, 0, gf=100, ga=50), _s("B", 10, 5, 0, gf=200, ga=50)]
    # B has a far better goal difference, but A beat B twice -- head-to-head first.
    assert rank_teams(teams, _h2h(("A", "B"), ("A", "B"))) == ["A", "B"]


def test_an_even_head_to_head_falls_through_to_goal_difference():
    teams = [_s("A", 10, 5, 0, gf=100, ga=90), _s("B", 10, 5, 0, gf=100, ga=50)]
    assert rank_teams(teams, _h2h(("A", "B"), ("B", "A"))) == ["B", "A"]


def test_goals_for_breaks_an_equal_goal_difference():
    teams = [_s("A", 10, 5, 0, gf=100, ga=80), _s("B", 10, 5, 0, gf=120, ga=100)]
    assert rank_teams(teams) == ["B", "A"]          # same +20, B scored more


def test_team_id_is_the_last_resort():
    teams = [_s("Zebra", 10, 5, 0, gf=90, ga=80), _s("Anchor", 10, 5, 0, gf=90, ga=80)]
    assert rank_teams(teams) == ["Anchor", "Zebra"]


def test_head_to_head_ignores_games_outside_the_tied_group():
    """A's wins over C don't help it against B -- only games among the tied teams
    count."""
    teams = [_s("A", 10, 5, 0, gf=100, ga=100), _s("B", 10, 5, 0, gf=100, ga=90),
             _s("C", 2, 13, 0)]
    h2h = _h2h(("A", "C"), ("A", "C"), ("B", "A"))
    assert rank_teams(teams, h2h)[:2] == ["B", "A"]


# -- multi-way ties ----------------------------------------------------------
def test_a_three_way_tie_uses_the_mini_table():
    teams = [_s("A", 10, 5, 0), _s("B", 10, 5, 0), _s("C", 10, 5, 0)]
    # Among themselves: B 2-0, A 1-1, C 0-2.
    h2h = _h2h(("B", "A"), ("B", "C"), ("A", "C"))
    assert rank_teams(teams, h2h) == ["B", "A", "C"]


def test_a_subgroup_the_mini_table_leaves_tied_is_re_decided_by_its_own_head_to_head():
    """The reason the mini-table recurses. All four are level on points. The table
    over all four separates A (18) and D (0) but leaves B and C level on 6 -- so B
    and C are re-decided by their OWN head-to-head, which B swept. A single pass
    would have sent them to goal difference instead, where C wins."""
    teams = [_s("A", 10, 5, 0), _s("B", 10, 5, 0, gf=10, ga=0),
             _s("C", 10, 5, 0, gf=99, ga=0),          # C has the far better GD
             _s("D", 10, 5, 0)]
    h2h = _h2h(
        ("A", "B"), ("A", "B"), ("A", "C"), ("A", "C"), ("A", "D"), ("A", "D"),  # A 18
        ("B", "C"), ("B", "C"),                                                  # B 6
        ("C", "D"), ("C", "D"),                                                  # C 6
    )
    assert rank_teams(teams, h2h) == ["A", "B", "C", "D"]


def test_a_circular_tie_falls_through_to_goal_difference():
    """A beat B, B beat C, C beat A: everyone has 3 mini-table points, so the
    head-to-head step separates nobody."""
    teams = [_s("A", 10, 5, 0, gf=50, ga=50), _s("B", 10, 5, 0, gf=70, ga=50),
             _s("C", 10, 5, 0, gf=60, ga=50)]
    h2h = _h2h(("A", "B"), ("B", "C"), ("C", "A"))
    assert rank_teams(teams, h2h) == ["B", "C", "A"]


def test_teams_on_different_points_never_reach_head_to_head():
    teams = [_s("A", 10, 5, 0), _s("B", 11, 4, 0)]
    assert rank_teams(teams, _h2h(("A", "B"), ("A", "B"))) == ["B", "A"]


# -- division-aware seeding --------------------------------------------------
def _conference_of(t: str) -> str:
    return "East" if t.startswith("E") else "West"


def _division_of(t: str) -> str:
    return t[:2]            # "E1".."E4", "W1".."W4" -- already conference-unique


def _conference_field(n_divisions=4, per_division=4) -> list[str]:
    """Team ids ranked best->worst, laid out so the ranking is obvious from the name:
    E1a is the best team overall, then E1b, ... (a is best within its division)."""
    return [f"E{d}{chr(ord('a') + i)}"
            for i in range(per_division) for d in range(1, n_divisions + 1)]


def test_division_winners_take_the_top_seeds():
    # Ranked best->worst: E1a E2a E3a E4a (the four division winners), then E1b...
    ranked = _conference_field()
    seeded = seed_conferences(ranked, _conference_of, 8, _division_of)
    assert seeded["East"][:4] == ["E1a", "E2a", "E3a", "E4a"]
    assert seeded["East"][4:] == ["E1b", "E2b", "E3b", "E4b"]


def test_a_division_winner_outranked_by_wildcards_still_gets_a_top_four_seed():
    """The whole point of divisions: E4's winner finished 7th in the conference and
    still hosts as the 4 seed."""
    ranked = ["E1a", "E1b", "E2a", "E2b", "E3a", "E3b", "E4a", "E4b", "E1c", "E2c"]
    seeded = seed_conferences(ranked, _conference_of, 8, _division_of)
    assert seeded["East"][:4] == ["E1a", "E2a", "E3a", "E4a"]     # winners, in order
    assert seeded["East"][4:] == ["E1b", "E2b", "E3b", "E4b"]     # best of the rest


def test_wildcards_are_simply_the_best_remaining():
    ranked = ["E1a", "E1b", "E1c", "E2a", "E2b", "E3a", "E4a", "E3b", "E4b", "E1d"]
    seeded = seed_conferences(ranked, _conference_of, 8, _division_of)
    assert seeded["East"][:4] == ["E1a", "E2a", "E3a", "E4a"]
    assert seeded["East"][4:] == ["E1b", "E1c", "E2b", "E3b"]


def test_both_conferences_are_seeded_independently():
    ranked = []
    for i in range(4):                      # interleave so neither conference leads
        for d in range(1, 5):
            ranked += [f"E{d}{chr(ord('a') + i)}", f"W{d}{chr(ord('a') + i)}"]
    seeded = seed_conferences(ranked, _conference_of, 8, _division_of)
    assert len(seeded["East"]) == len(seeded["West"]) == 8
    assert all(t.startswith("E") for t in seeded["East"])
    assert all(t.startswith("W") for t in seeded["West"])


def test_without_a_division_map_seeding_is_pure_ranking_order():
    """The offline stack has no divisions; it should still get a sane bracket."""
    ranked = _conference_field()
    assert seed_conferences(ranked, _conference_of, 8) == {"East": ranked[:8]}


def test_seeding_never_repeats_a_team():
    ranked = _conference_field()
    seeded = seed_conferences(ranked, _conference_of, 8, _division_of)
    assert len(set(seeded["East"])) == 8
