"""
Voting endpoints: award ballots, the All-Star ballot, the tallies, and the
exhibition game. Rules live in handball/voting_rules.py (pure), the SQL in
handball/voting.py, and the game in handball/all_star.py -- this file is only
authorization, request shape, and error translation.

    GET  /voting/state                  what is open, this caller's ballots, candidates
    POST /voting/awards/ballot          manager submits/replaces a ranked award ballot
    POST /voting/all-star/ballot        manager submits/replaces a positional ballot
    POST /voting/awards/tally           commissioner counts the award vote
    POST /voting/all-star/play          commissioner plays the exhibition
    GET  /voting/results?season=        the finished count + the All-Star box score

/voting/state is the one document the Vote page reads, and it is PER CALLER: it
carries the caller's own saved ballots and nobody else's. A vote in progress is
sealed for exactly the reason a sealed free-agency offer is, so the only aggregate
anyone sees before the tally is a ballot COUNT (how many voted, never for whom), and
that is commissioner-only.

Submitting is `require_owns`-free but NOT identity-free: a ballot belongs to a
Manager.user_id, and the teams that manager owns are what the own-team rule is
checked against. The commissioner gets no bypass on ballot validation -- they are a
manager with teams of their own, and their vote is subject to the same rule as
everyone's. Their powers here are the explicit ones: tally, and play.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from handball import all_star
from handball import voting
from handball.simulation_vars import ALL_STAR_BALLOT, AWARD_BALLOT_SIZE, AWARD_POINTS, AWARDS
from handball.league_structure import CONFERENCES
from handball.roster_layout import RosterLayoutError
from handball.domain import ArrangementError
from handball.voting_rules import BallotError

from api.auth import Manager, get_current_manager
from api.deps import active_season, engine, require_commissioner

router = APIRouter()


class AwardBallotBody(BaseModel):
    award: str
    ranked_ids: list[str] = Field(default_factory=list)


class AllStarBallotBody(BaseModel):
    conference: str
    ballot: dict[str, list[str]] = Field(default_factory=dict)


def _bad(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _conflict(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


@router.get("/voting/state")
def voting_state(mgr: Manager = Depends(get_current_manager)):
    """Everything the Vote page needs: the phase per kind, the caller's saved
    ballots, and the eligible candidates with the caller's own players already
    marked so the picker can grey them out rather than let someone discover the
    rule by being rejected."""
    season = active_season()
    status = voting.sync_status(engine, season)
    owned = {str(t) for t in mgr.owned_team_ids}

    def mark(cands: list[dict]) -> list[dict]:
        return [{**c, "own_team": c.get("team_id") in owned} for c in cands]

    awards = {a: mark(c) for a, c in voting.award_candidates(engine, season).items()}
    all_star_pools = {
        conf: {pos: mark(lst) for pos, lst in pools.items()}
        for conf, pools in voting.all_star_candidates(engine, season).items()
    }
    return {
        "season": season,
        "status": status,
        "awards": list(AWARDS),
        "award_ballot_size": AWARD_BALLOT_SIZE,
        "award_points": list(AWARD_POINTS),
        "all_star_ballot": dict(ALL_STAR_BALLOT),
        "conferences": list(CONFERENCES),
        "candidates": {"awards": awards, "all_star": all_star_pools},
        "my_ballots": voting.voter_ballots(engine, season, mgr.user_id),
        "ballot_counts": (
            voting.ballot_counts(engine, season) if mgr.is_commissioner else None
        ),
        "all_star_game": all_star.game(engine, season),
    }


@router.post("/voting/awards/ballot")
def submit_award_ballot(
    body: AwardBallotBody, mgr: Manager = Depends(get_current_manager)
):
    """Submit (or replace) this manager's ranked ballot for one award."""
    season = active_season()
    try:
        return voting.submit_award_ballot(
            engine, season, body.award, mgr.user_id, body.ranked_ids,
            owned_team_ids=mgr.owned_team_ids,
        )
    except BallotError as e:
        raise _bad(e)
    except voting.VotingError as e:
        raise _conflict(e)


@router.post("/voting/all-star/ballot")
def submit_all_star_ballot(
    body: AllStarBallotBody, mgr: Manager = Depends(get_current_manager)
):
    """Submit (or replace) this manager's positional ballot for one conference."""
    season = active_season()
    try:
        return voting.submit_all_star_ballot(
            engine, season, body.conference, mgr.user_id, body.ballot,
            owned_team_ids=mgr.owned_team_ids,
        )
    except BallotError as e:
        raise _bad(e)
    except voting.VotingError as e:
        raise _conflict(e)


@router.post("/voting/awards/tally")
def tally_awards(mgr: Manager = Depends(get_current_manager)):
    """Count the award ballots, write the tallies and the winners, close the vote.
    Commissioner-only, and required before /season/advance."""
    require_commissioner(mgr)
    try:
        return voting.tally_awards(engine, active_season())
    except voting.VotingError as e:
        raise _conflict(e)


@router.post("/voting/all-star/play")
def play_all_star(mgr: Manager = Depends(get_current_manager)):
    """Select both squads from the vote and play the exhibition. Commissioner-only,
    and required before period 4."""
    require_commissioner(mgr)
    try:
        return all_star.play(engine, active_season())
    except (all_star.AllStarError, voting.VotingError) as e:
        raise _conflict(e)
    except (RosterLayoutError, ArrangementError) as e:
        # A conference whose 17 selected players cannot field a legal lineup. Rare,
        # and a real problem with the league rather than with the request.
        raise _conflict(e)


@router.get("/voting/results")
def results(
    season: int | None = Query(default=None),
    mgr: Manager = Depends(get_current_manager),
):
    """The finished count for a season plus its All-Star game. Any manager; results
    are public once they exist. Defaults to the most recently counted season so the
    Awards page has something to show without guessing a year."""
    del mgr           # authenticated, but results are the same for everyone
    seasons = sorted(
        set(voting.awarded_seasons(engine)) | set(all_star.seasons(engine)), reverse=True
    )
    if season is None:
        season = seasons[0] if seasons else active_season()
    return {
        "season": season,
        "seasons": seasons,
        "awards": voting.award_results(engine, season),
        "all_star_game": all_star.game(engine, season),
    }
