"""
The ballot-box rules, DB-free. Mirrors tests/test_free_agency_rules.py: everything
here is a pure function over plain snapshots, including the assertion that the module
never imported sqlalchemy.
"""
import ast

import pytest

from handball.simulation_vars import ALL_STAR_BALLOT, AWARD_POINTS
from handball.voting_rules import (
    BallotError,
    all_star_bench_counts,
    all_star_starter_counts,
    count_all_star_votes,
    select_all_stars,
    tally_award,
    validate_all_star_ballot,
    validate_award_ballot,
)

MVP = "Most Valuable Player"
ELEVENTH = "Eleventh Man of the Year"


def test_the_rules_module_never_touches_the_database():
    """The pure/SQL split is the whole shape of this module; assert it mechanically
    rather than trusting the docstring."""
    import handball.voting_rules as rules

    tree = ast.parse(open(rules.__file__).read())
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any("sqlalchemy" in m for m in imported)


# -- award ballots -----------------------------------------------------------
def test_valid_award_ballot_round_trips():
    got = validate_award_ballot(MVP, ["a", "b", "c"], eligible_ids={"a", "b", "c", "d"})
    assert got == ["a", "b", "c"]


def test_unknown_award_is_refused():
    with pytest.raises(BallotError, match="not a voted award"):
        validate_award_ballot("Top Scorer", ["a"], eligible_ids={"a"})


def test_empty_ballot_is_refused():
    with pytest.raises(BallotError, match="at least one"):
        validate_award_ballot(MVP, [], eligible_ids={"a"})


def test_overlong_ballot_is_refused():
    with pytest.raises(BallotError, match="at most 5"):
        validate_award_ballot(
            MVP, ["a", "b", "c", "d", "e", "f"], eligible_ids=set("abcdef")
        )


def test_short_ballot_is_allowed():
    """Naming three is a real opinion; the unnamed places simply score nothing."""
    assert validate_award_ballot(MVP, ["a", "b"], eligible_ids={"a", "b"}) == ["a", "b"]


def test_duplicate_names_are_refused():
    with pytest.raises(BallotError, match="same candidate twice"):
        validate_award_ballot(MVP, ["a", "b", "a"], eligible_ids={"a", "b"})


def test_ineligible_candidate_is_refused():
    with pytest.raises(BallotError, match="not eligible"):
        validate_award_ballot(ELEVENTH, ["starter"], eligible_ids={"bench1"})


def test_own_team_candidate_is_refused():
    with pytest.raises(BallotError, match="team you own"):
        validate_award_ballot(MVP, ["mine"], eligible_ids={"mine"}, own_ids={"mine"})


# -- the count ---------------------------------------------------------------
def test_points_are_10_7_5_3_1():
    assert tuple(AWARD_POINTS) == (10, 7, 5, 3, 1)
    rows = tally_award([["a", "b", "c", "d", "e"]])
    assert [(r.entity_id, r.points) for r in rows] == [
        ("a", 10), ("b", 7), ("c", 5), ("d", 3), ("e", 1)
    ]


def test_ranks_are_assigned_best_first():
    rows = tally_award([["a", "b"], ["a", "b"], ["b", "a"]])
    assert [(r.entity_id, r.points, r.rank) for r in rows] == [
        ("a", 27, 1), ("b", 24, 2)
    ]


def test_also_rans_get_a_row():
    """A single fifth-place vote is part of the evidence, not noise to drop."""
    rows = tally_award([["a", "b", "c", "d", "e"]])
    assert len(rows) == 5
    assert rows[-1].entity_id == "e" and rows[-1].points == 1


def test_first_place_votes_break_a_points_tie():
    """Both reach 10: `a` on a single first-place vote, `b` on a second plus a
    fourth. Equal points, and the ballot that named someone FIRST is the stronger
    statement, so it decides."""
    rows = tally_award([
        ["a", "p", "q", "r", "s"],      # a: 10
        ["p", "b", "q", "r", "s"],      # b: 7
        ["p", "q", "r", "b", "s"],      # b: 3
    ])
    a = next(r for r in rows if r.entity_id == "a")
    b = next(r for r in rows if r.entity_id == "b")
    assert a.points == b.points == 10
    assert a.first_place_votes == 1 and b.first_place_votes == 0
    assert a.rank < b.rank


def test_name_breaks_a_full_tie():
    """Identical points AND identical first-place votes: the order must still be
    total, and it must not depend on ballot order."""
    rows = tally_award([["x"], ["y"]], names={"x": "Zeta", "y": "Alpha"})
    assert [r.entity_id for r in rows] == ["y", "x"]
    assert rows[0].rank == 1 and rows[1].rank == 2
    # Same ballots, other order in: same result out.
    again = tally_award([["y"], ["x"]], names={"x": "Zeta", "y": "Alpha"})
    assert [r.entity_id for r in again] == ["y", "x"]


def test_no_ballots_is_an_empty_tally():
    assert tally_award([]) == []


# -- All-Star ballots --------------------------------------------------------
def _pools(n=8):
    return {pos: [f"{pos[:1].lower()}{i}" for i in range(n)] for pos in ALL_STAR_BALLOT}


def _full_ballot(pools):
    return {pos: pools[pos][: ALL_STAR_BALLOT[pos]] for pos in ALL_STAR_BALLOT}


def test_ballot_shape_matches_the_league_shape():
    """5/5/5/2 names is exactly starters + bench with no reserves -- the reason the
    exhibition can be fielded at all."""
    starters = all_star_starter_counts()
    bench = all_star_bench_counts()
    assert starters == {"Forward": 3, "Midfielder": 3, "Defense": 3, "Goalie": 1}
    assert bench == {"Forward": 2, "Midfielder": 2, "Defense": 2, "Goalie": 1}
    assert sum(ALL_STAR_BALLOT.values()) == sum(starters.values()) + sum(bench.values())


def test_valid_all_star_ballot_round_trips():
    pools = _pools()
    got = validate_all_star_ballot(_full_ballot(pools), eligible_by_position=pools)
    assert {pos: len(ids) for pos, ids in got.items()} == dict(ALL_STAR_BALLOT)


def test_partial_all_star_ballot_is_refused():
    pools = _pools()
    ballot = _full_ballot(pools)
    ballot["Forward"] = ballot["Forward"][:2]
    with pytest.raises(BallotError, match="name exactly 5 at Forward"):
        validate_all_star_ballot(ballot, eligible_by_position=pools)


def test_missing_position_is_refused():
    pools = _pools()
    ballot = _full_ballot(pools)
    del ballot["Goalie"]
    with pytest.raises(BallotError, match="missing Goalie"):
        validate_all_star_ballot(ballot, eligible_by_position=pools)


def test_all_star_own_team_player_is_refused():
    pools = _pools()
    ballot = _full_ballot(pools)
    with pytest.raises(BallotError, match="team you own"):
        validate_all_star_ballot(
            ballot, eligible_by_position=pools, own_ids={ballot["Defense"][0]}
        )


def test_all_star_player_named_twice_across_positions_is_refused():
    pools = _pools()
    pools["Forward"] = pools["Forward"] + ["shared"]
    pools["Midfielder"] = pools["Midfielder"] + ["shared"]
    ballot = _full_ballot(pools)
    ballot["Forward"][0] = "shared"
    ballot["Midfielder"][0] = "shared"
    with pytest.raises(BallotError, match="named twice"):
        validate_all_star_ballot(ballot, eligible_by_position=pools)


def test_counting_is_one_vote_per_name():
    pools = _pools()
    b1 = _full_ballot(pools)
    b2 = _full_ballot(pools)
    counts = count_all_star_votes([b1, b2])
    assert counts["Goalie"][pools["Goalie"][0]] == 2


def test_top_votes_start_and_the_rest_ride_the_bench():
    pools = _pools()
    # Forwards f0..f4 get 5,4,3,2,1 votes -- a clean, unambiguous order.
    counts = {
        "Forward": {f"f{i}": 5 - i for i in range(5)},
        "Midfielder": {f"m{i}": 5 - i for i in range(5)},
        "Defense": {f"d{i}": 5 - i for i in range(5)},
        "Goalie": {f"g{i}": 2 - i for i in range(2)},
    }
    pools = {"Forward": [f"f{i}" for i in range(5)],
             "Midfielder": [f"m{i}" for i in range(5)],
             "Defense": [f"d{i}" for i in range(5)],
             "Goalie": [f"g{i}" for i in range(2)]}
    sel = select_all_stars(counts, eligible_by_position=pools)
    assert sel.starters["Forward"] == ["f0", "f1", "f2"]
    assert sel.bench["Forward"] == ["f3", "f4"]
    assert sel.starters["Goalie"] == ["g0"]
    assert sel.bench["Goalie"] == ["g1"]
    assert len(sel.all_ids()) == sum(ALL_STAR_BALLOT.values()) == 17
    assert sel.votes["f0"] == 5


def test_selection_backfills_a_position_nobody_voted_for():
    """A squad short a goalie is not a smaller squad, it is no game at all -- so an
    unvoted slot is filled from the eligible list, at zero votes, visibly."""
    pools = _pools()
    counts = {"Forward": {"f0": 3}}          # nobody voted anywhere else
    sel = select_all_stars(counts, eligible_by_position=pools)
    assert len(sel.all_ids()) == 17
    assert sel.starters["Forward"][0] == "f0"
    assert sel.votes["f0"] == 3
    assert all(sel.votes[pid] == 0 for pid in sel.all_ids() if pid != "f0")
    assert len(sel.starters["Goalie"]) == 1


def test_selection_refuses_a_conference_that_cannot_fill_a_position():
    pools = _pools()
    pools["Goalie"] = ["g0"]                 # one goalie, ballot wants two
    with pytest.raises(BallotError, match="not enough eligible players at Goalie"):
        select_all_stars({}, eligible_by_position=pools)


def test_selection_is_deterministic_on_a_vote_tie():
    pools = {"Forward": ["f0", "f1", "f2", "f3", "f4"],
             "Midfielder": ["m0", "m1", "m2", "m3", "m4"],
             "Defense": ["d0", "d1", "d2", "d3", "d4"],
             "Goalie": ["g0", "g1"]}
    counts = {"Forward": {f"f{i}": 4 for i in range(5)}}
    names = {f"f{i}": f"Name {4 - i}" for i in range(5)}
    sel = select_all_stars(counts, eligible_by_position=pools, names=names)
    # All tied on votes -> alphabetical by name, which reverses the id order.
    assert sel.starters["Forward"] == ["f4", "f3", "f2"]
