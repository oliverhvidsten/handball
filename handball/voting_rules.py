"""
Name: voting_rules.py
Description: The rules of the ballot box, as pure functions over plain snapshots.
    No database, no SQL -- this module does not import sqlalchemy, which is the
    mechanical test that it stayed pure (the same contract free_agency_rules.py
    holds). handball/voting.py is the SQL layer that reads candidates, calls these,
    and writes the results; handball/all_star.py plays the exhibition the second
    half of this module selects.

    Two votes live here, and they are different shapes on purpose:

    THE AWARD BALLOT is RANKED. A voter names up to AWARD_BALLOT_SIZE entities in
    order, placements score AWARD_POINTS (10-7-5-3-1), and the winner is whoever
    accumulates the most. A ranked ballot is the only way a vote of 32 managers
    says anything about second place, and second place is what award_tallies exists
    to record -- a results page that shows only the winner is not a results page.

    THE ALL-STAR BALLOT is POSITIONAL and unranked. A voter names exactly
    ALL_STAR_BALLOT[pos] players per position per conference; every name on it is
    worth one vote, and the top few by vote count start. Ranking it would be
    precision the voters do not have: nobody has an opinion about the fifth-best
    midfielder in the East that survives being ranked against the fourth.

    THE THREE THINGS EVERY BALLOT MUST SATISFY, checked in this order so the voter
    is told about the shape of their ballot before its contents:
      1. SHAPE -- the right number of names, and no duplicates. A duplicate is not a
         harmless typo on a ranked ballot: it would score one entity twice.
      2. ELIGIBILITY -- every name is a candidate for this category. Eligibility is
         computed by the SQL layer (who is a rookie, who is on a bench, who holds an
         open coaching tenure) and arrives here as a set, so the rules stay pure.
      3. SELF-DEALING -- no name from a team the voter owns. This is the rule the
         whole thing rests on and the reason a ballot is validated server-side at
         all: managers vote on an award their own players can win, so "not mine" is
         not a courtesy, it is the integrity of the count. Multi-team owners exist,
         so it is a set of teams, never one.

    THE TIEBREAK is points, then FIRST-PLACE VOTES, then name. Points alone tie
    often at this league's size (32 ballots over 10-7-5-3-1), and when they do, the
    ballot that put someone first is a stronger statement than the one that put them
    third -- so first-place votes break it before anything arbitrary does. Name is
    the last resort purely so the order is TOTAL and the same count never renders
    two different ways.
Author: voting
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from handball.league_views import DEFAULT_RULES, RosterRules
from handball.simulation_vars import (
    ALL_STAR_BALLOT,
    AWARD_BALLOT_SIZE,
    AWARD_POINTS,
    AWARDS,
)


class BallotError(ValueError):
    """A ballot that may not be counted: wrong shape, an ineligible name, or a name
    from one of the voter's own teams. Always a manager-facing sentence -- the API
    turns it straight into a 400 -- so it says which name is the problem."""


# -- award ballots -----------------------------------------------------------
def validate_award_ballot(
    award: str,
    ranked_ids: Sequence[str],
    *,
    eligible_ids: Iterable[str],
    own_ids: Iterable[str] = (),
    ballot_size: int = AWARD_BALLOT_SIZE,
) -> list[str]:
    """Check one ranked award ballot and return it normalized to a list.

    `ranked_ids` is best-first. Length may be anything from 1 to `ballot_size`: a
    short ballot is a real opinion ("I can name three, not five") and scoring it is
    exactly what AWARD_POINTS already does -- the unnamed places simply award no
    points. What is not allowed is an EMPTY ballot, which is an abstention and has
    no business being stored as a vote."""
    if award not in AWARDS:
        raise BallotError(f"{award!r} is not a voted award")

    ids = list(ranked_ids)
    if not ids:
        raise BallotError("a ballot must name at least one candidate")
    if len(ids) > ballot_size:
        raise BallotError(f"a ballot may name at most {ballot_size} candidates, got {len(ids)}")

    dupes = [i for i, n in Counter(ids).items() if n > 1]
    if dupes:
        raise BallotError("a ballot may not name the same candidate twice")

    eligible, own = set(eligible_ids), set(own_ids)
    for entity_id in ids:
        if entity_id not in eligible:
            raise BallotError(f"{entity_id} is not eligible for {award}")
    # Checked after eligibility so an id that is neither is reported as the simpler
    # of the two problems.
    for entity_id in ids:
        if entity_id in own:
            raise BallotError("you may not vote for a candidate from a team you own")
    return ids


@dataclass(frozen=True)
class TallyRow:
    """One line of a finished count. `rank` is 1-based and UNIQUE -- the tiebreak
    chain below is total, so no two rows ever share one. Storing a resolved rank
    rather than leaving the page to re-sort is what makes award_tallies evidence:
    it records the order the award was actually decided in."""
    entity_id: str
    points: int
    first_place_votes: int
    rank: int


def tally_award(
    ballots: Iterable[Sequence[str]],
    *,
    names: Mapping[str, str] | None = None,
    points: Sequence[int] = AWARD_POINTS,
) -> list[TallyRow]:
    """Count ranked ballots into a ranked, tie-broken result (best first).

    Every entity named on any ballot gets a row, including the ones with a single
    fifth-place vote: the also-rans ARE the tally, and dropping them would make the
    stored evidence a summary rather than a record. An entity is scored `points[i]`
    for each i-th placement, and placements past the end of `points` score nothing.

    `names` supplies the last-resort alphabetical tiebreak; a missing name falls
    back to the id, which is stable if not pretty."""
    scored: dict[str, int] = defaultdict(int)
    firsts: dict[str, int] = defaultdict(int)
    for ballot in ballots:
        for place, entity_id in enumerate(ballot):
            if place < len(points):
                scored[entity_id] += points[place]
            else:
                scored[entity_id] += 0        # still a row; just no points
            if place == 0:
                firsts[entity_id] += 1

    names = names or {}
    order = sorted(
        scored,
        key=lambda e: (-scored[e], -firsts[e], names.get(e, e), e),
    )
    return [
        TallyRow(entity_id=e, points=scored[e], first_place_votes=firsts[e], rank=i + 1)
        for i, e in enumerate(order)
    ]


# -- All-Star ballots --------------------------------------------------------
def all_star_starter_counts(rules: RosterRules = DEFAULT_RULES) -> dict[str, int]:
    """How many of each position START the exhibition: the league's own starter
    caps, not a second copy of 3/3/3/1. Derived rather than declared so a change to
    the roster shape can never leave the All-Star game fielding an illegal lineup."""
    return dict(rules.starter_caps)


def all_star_bench_counts(
    ballot: Mapping[str, int] = ALL_STAR_BALLOT, rules: RosterRules = DEFAULT_RULES
) -> dict[str, int]:
    """The rest of the ballot: whoever is voted in but not into a starting slot.
    ALL_STAR_BALLOT is sized so this lands exactly on the bench caps (5/5/5/2 names
    = 3/3/3/1 starters + 2/2/2/1 bench = a legal 17-man team with no reserves), and
    a mismatch here is a configuration error worth failing loudly on."""
    starters = all_star_starter_counts(rules)
    out = {}
    for pos, n in ballot.items():
        bench = n - starters.get(pos, 0)
        if bench < 0:
            raise BallotError(
                f"ALL_STAR_BALLOT names {n} at {pos} but {starters.get(pos, 0)} must start"
            )
        out[pos] = bench
    return out


def validate_all_star_ballot(
    payload: Mapping[str, Sequence[str]],
    *,
    eligible_by_position: Mapping[str, Iterable[str]],
    own_ids: Iterable[str] = (),
    ballot: Mapping[str, int] = ALL_STAR_BALLOT,
) -> dict[str, list[str]]:
    """Check one conference's positional ballot and return it normalized.

    Unlike an award ballot this one must be EXACTLY full at every position. A
    positional ballot is a lineup, not a preference: a half-filled one cannot be
    counted against a full one without quietly weighting the voters who bothered."""
    missing = [pos for pos in ballot if pos not in payload]
    if missing:
        raise BallotError(f"ballot is missing {', '.join(sorted(missing))}")
    extra = [pos for pos in payload if pos not in ballot]
    if extra:
        raise BallotError(f"{', '.join(sorted(extra))} is not a position on the ballot")

    own = set(own_ids)
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    for pos, want in ballot.items():
        ids = list(payload[pos])
        if len(ids) != want:
            raise BallotError(f"name exactly {want} at {pos}, got {len(ids)}")
        if len(set(ids)) != len(ids):
            raise BallotError(f"a ballot may not name the same player twice at {pos}")
        eligible = set(eligible_by_position.get(pos, ()))
        for pid in ids:
            if pid in seen:
                raise BallotError(f"{pid} is named twice on the same ballot")
            if pid not in eligible:
                raise BallotError(f"{pid} is not an eligible {pos} in this conference")
            if pid in own:
                raise BallotError("you may not vote for a player from a team you own")
            seen.add(pid)
        out[pos] = ids
    return out


def count_all_star_votes(
    ballots: Iterable[Mapping[str, Sequence[str]]],
) -> dict[str, dict[str, int]]:
    """position -> {player id: votes}. Every name on a positional ballot is worth
    one vote; there is no ranking to weight."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ballot in ballots:
        for pos, ids in ballot.items():
            for pid in ids:
                counts[pos][pid] += 1
    return {pos: dict(c) for pos, c in counts.items()}


@dataclass(frozen=True)
class AllStarSelection:
    """One conference's squad: the ids that start and the ids that come off the
    bench, per position, plus the vote count behind every one of them so the page
    can show the margin rather than just the name."""
    starters: dict[str, list[str]]
    bench: dict[str, list[str]]
    votes: dict[str, int]

    def all_ids(self) -> list[str]:
        out: list[str] = []
        for group in (self.starters, self.bench):
            for ids in group.values():
                out.extend(ids)
        return out


def select_all_stars(
    vote_counts: Mapping[str, Mapping[str, int]],
    *,
    eligible_by_position: Mapping[str, Sequence[str]],
    names: Mapping[str, str] | None = None,
    ballot: Mapping[str, int] = ALL_STAR_BALLOT,
    rules: RosterRules = DEFAULT_RULES,
) -> AllStarSelection:
    """Turn per-position vote counts into a legal 17-man squad.

    Order within a position is votes descending, then name -- the same total order
    the award tally uses, for the same reason. The top starter_caps[pos] START; the
    rest of the ballot's allocation comes off the bench.

    BACKFILL: if fewer players received votes than the ballot has slots (a thin
    position, or a conference whose managers all voted for the same two goalies),
    the remaining slots are filled from `eligible_by_position` in the order it was
    given -- the SQL layer supplies it best-player-first. The alternative is an
    exhibition that cannot field a goalie, and a squad short a position is not a
    smaller squad, it is no game at all. A backfilled player is in the selection
    with zero votes, which is visible on the results page and honest about how they
    got there."""
    starter_counts = all_star_starter_counts(rules)
    bench_counts = all_star_bench_counts(ballot, rules)
    names = names or {}

    starters: dict[str, list[str]] = {}
    bench: dict[str, list[str]] = {}
    votes: dict[str, int] = {}
    for pos, want in ballot.items():
        counts = dict(vote_counts.get(pos, {}))
        ordered = sorted(counts, key=lambda p: (-counts[p], names.get(p, p), p))
        if len(ordered) < want:
            chosen = set(ordered)
            for pid in eligible_by_position.get(pos, ()):
                if len(ordered) >= want:
                    break
                if pid not in chosen:
                    ordered.append(pid)
                    chosen.add(pid)
        if len(ordered) < want:
            raise BallotError(
                f"not enough eligible players at {pos}: need {want}, have {len(ordered)}"
            )
        squad = ordered[:want]
        for pid in squad:
            votes[pid] = counts.get(pid, 0)
        starters[pos] = squad[: starter_counts.get(pos, 0)]
        bench[pos] = squad[starter_counts.get(pos, 0): starter_counts.get(pos, 0)
                           + bench_counts.get(pos, 0)]
    return AllStarSelection(starters=starters, bench=bench, votes=votes)
