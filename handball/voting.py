"""
Name: voting.py
Description: The SQL layer under the ballot box -- who may vote, on whom, when the
    polls are open, and what the count wrote. The rules themselves are pure and live
    in handball/voting_rules.py; this module reads the candidates, calls them, and
    persists the result over alembic 0015's ballots / award_tallies / voting_status.
    The All-Star exhibition the positional vote selects is played in
    handball/all_star.py.

    THE PHASE OPENS ITSELF. voting_status is the only state there is, one row per
    (season, kind), and it starts 'closed'. `sync_status` -- called by every read --
    flips it to 'open' once enough periods have run (AWARD_VOTING_OPENS_AFTER_PERIOD
    for the awards, ALL_STAR_AFTER_PERIOD for the All-Star ballot). This is the same
    LAZY pattern free agency uses for its turn clock, and for the same reason: there
    is no scheduler in this deployment, so a phase that must begin on its own has to
    begin on somebody's read. It only ever moves closed -> open; 'tallied' is
    terminal, so a later period can never reopen a vote that has already been
    counted. The threshold is >= rather than ==, so a season that somehow ran two
    periods at once still opens its polls.

    ELIGIBILITY IS COMPUTED HERE, NOT VOTED ON. Each award draws from its own pool
    (see _PLAYER_ELIGIBILITY): the rookie award from players in their first year, the
    Eleventh Man award from players actually sitting on a bench right now, Coach of
    the Year from coaches holding an open tenure. The pool is handed to the pure
    rules as a set of ids, which is what keeps the rules DB-free.

    OWN-TEAM VOTES ARE REFUSED, and this is the whole reason ballots are validated
    server-side. A manager votes on awards their own players can win. "Teams the
    voter owns" is teams.owner_id and it is a SET -- multi-team owners exist in this
    league -- so the check is never against one team id.

    A BALLOT IS AN UPSERT. One per voter per category, replaced on re-submission
    (the unique index does the work). Unlike an fa_offer there is no seniority to
    lose and nobody relies on a vote, so there is no audit trail worth keeping and
    changing your mind costs nothing.

    THE TALLY IS THE COMMISSIONER'S. Opening is automatic; committing a result is
    not. tally_awards writes every candidate's line to award_tallies (the evidence
    behind the name) and the rank-1 line to `awards`, then closes the phase. It
    deletes only the awards it owns, because `awards` also holds the auto-computed
    stat titles -- see offseason._compute_awards, which was taught the same manners
    in the same change.
Author: voting
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.league_structure import CONFERENCES, LEAGUE
from handball.simulation_vars import (
    ALL_STAR_AFTER_PERIOD,
    ALL_STAR_BALLOT,
    AWARD_VOTING_OPENS_AFTER_PERIOD,
    AWARDS,
)
from handball.voting_rules import (
    BallotError,
    tally_award,
    validate_all_star_ballot,
    validate_award_ballot,
)

# The two kinds of vote, as stored in ballots.kind / voting_status.kind.
AWARD_KIND = "award"
ALL_STAR_KIND = "allstar"
KINDS = (AWARD_KIND, ALL_STAR_KIND)

# The one award voted on COACHES rather than players; everything about the count is
# the same, but the recipient lands in awards.coach_id.
COACH_AWARD = "Coach of the Year"

# When each kind's polls open, as a period count.
_OPENS_AFTER = {
    AWARD_KIND: AWARD_VOTING_OPENS_AFTER_PERIOD,
    ALL_STAR_KIND: ALL_STAR_AFTER_PERIOD,
}

# Extra WHERE clauses defining each award's candidate pool, on top of the base
# "rostered, not retired". Written in the same tail-SQL style as
# offseason._top_player so the two ways of naming an award winner read alike.
_PLAYER_ELIGIBILITY: dict[str, str] = {
    "Most Valuable Player": "",
    "Rookie of the Year": "and p.years_in_league = 0",
    "Defensive Player of the Year": "",
    # "sitting in a BENCH slot at ballot time" -- the roster slot, not the position.
    "Eleventh Man of the Year": "and p.slot_group = 'bench'",
    "Most Improved Player": "",
}


class VotingError(Exception):
    """A voting action refused by the phase: the polls are shut, the count is
    already in, or there is nothing to count. Distinct from BallotError, which is
    about the contents of one ballot rather than the state of the vote."""


# -- the phase ---------------------------------------------------------------
def _periods_run(conn, season: int) -> int:
    row = conn.execute(
        text("select periods_run from season_state where season = :s"), {"s": season}
    ).first()
    return int(row[0]) if row else 0


def sync_status(engine: Engine, season: int) -> dict[str, str]:
    """Ensure a voting_status row exists for each kind and open the ones whose
    period threshold has passed. Returns {kind: status}.

    This is a read that writes, deliberately: there is no scheduler, so the polls
    open on the first person to look at them."""
    with engine.begin() as conn:
        return _sync_status(conn, season)


def _sync_status(conn, season: int) -> dict[str, str]:
    periods = _periods_run(conn, season)
    for kind in KINDS:
        conn.execute(
            text("insert into voting_status (season, kind, status) values (:s, :k, 'closed') "
                 "on conflict (season, kind) do nothing"),
            {"s": season, "k": kind},
        )
        if periods >= _OPENS_AFTER[kind]:
            # closed -> open only. 'tallied' is terminal: a counted vote never reopens.
            conn.execute(
                text("update voting_status set status = 'open' "
                     "where season = :s and kind = :k and status = 'closed'"),
                {"s": season, "k": kind},
            )
    rows = conn.execute(
        text("select kind, status from voting_status where season = :s"), {"s": season}
    ).all()
    return {k: s for k, s in rows}


def _status(conn, season: int, kind: str) -> str:
    row = conn.execute(
        text("select status from voting_status where season = :s and kind = :k"),
        {"s": season, "k": kind},
    ).first()
    return row[0] if row else "closed"


def _require_open(conn, season: int, kind: str) -> None:
    status = _status(conn, season, kind)
    if status == "tallied":
        raise VotingError(f"{kind} voting for {season} has already been counted")
    if status != "open":
        raise VotingError(f"{kind} voting for {season} is not open")


# -- candidates --------------------------------------------------------------
def _player_pool(conn, season: int, tail_sql: str) -> list[dict]:
    """Rostered, non-retired players matching an award's extra clause, best first.

    Ordered by raw ability so a picker shows plausible names at the top; it is a
    convenience for the UI and has no bearing on the count."""
    rows = conn.execute(
        text(
            "select p.id, p.legacy_id, p.name, p.position, p.team_id, "
            "t.slug as team_slug, t.name as team_name "
            "from players p join teams t on t.id = p.team_id "
            "where p.retired = false and p.team_id is not null " + tail_sql + " "
            "order by (coalesce(p.offense,0) + coalesce(p.defense,0) "
            "+ coalesce(p.goalie_skill,0)) desc, p.name"
        ),
    ).mappings().all()
    return [
        {"id": str(r["id"]), "legacy_id": r["legacy_id"], "name": r["name"],
         "position": r["position"], "kind": "player", "team_id": str(r["team_id"]),
         "team_slug": r["team_slug"], "team_name": r["team_name"]}
        for r in rows
    ]


def _coach_pool(conn) -> list[dict]:
    """Every coach holding an OPEN tenure (end_season is null), in any of the three
    roles. A coach between jobs is nobody's Coach of the Year."""
    rows = conn.execute(
        text(
            "select c.id, c.legacy_id, c.name, ct.role, ct.team_id, "
            "t.slug as team_slug, t.name as team_name "
            "from coaches c join coach_tenures ct on ct.coach_id = c.id "
            "join teams t on t.id = ct.team_id "
            "where ct.end_season is null "
            "order by t.name, ct.role, c.name"
        )
    ).mappings().all()
    return [
        {"id": str(r["id"]), "legacy_id": r["legacy_id"], "name": r["name"],
         "role": r["role"], "kind": "coach", "team_id": str(r["team_id"]),
         "team_slug": r["team_slug"], "team_name": r["team_name"]}
        for r in rows
    ]


def award_candidates(engine: Engine, season: int) -> dict[str, list[dict]]:
    """{award: [candidate, ...]} for every voted award."""
    with engine.connect() as conn:
        return _award_candidates(conn, season)


def _award_candidates(conn, season: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for award in AWARDS:
        if award == COACH_AWARD:
            out[award] = _coach_pool(conn)
        else:
            out[award] = _player_pool(conn, season, _PLAYER_ELIGIBILITY.get(award, ""))
    return out


def conference_teams(conference: str) -> list[str]:
    """The team slugs in a conference, from the league map."""
    return [t for division in LEAGUE[conference].values() for t in division]


def all_star_candidates(engine: Engine, season: int) -> dict[str, dict[str, list[dict]]]:
    """{conference: {position: [candidate, ...]}}, each position's list best first.

    Best-first matters here beyond presentation: it is also the BACKFILL order
    voting_rules.select_all_stars falls back on when a position draws fewer names
    than the ballot has slots."""
    with engine.connect() as conn:
        return _all_star_candidates(conn, season)


def _all_star_candidates(conn, season: int) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = {}
    for conference in CONFERENCES:
        slugs = conference_teams(conference)
        rows = conn.execute(
            text(
                "select p.id, p.legacy_id, p.name, p.position, p.team_id, "
                "t.slug as team_slug, t.name as team_name "
                "from players p join teams t on t.id = p.team_id "
                "where p.retired = false and t.slug = any(:slugs) "
                "order by (coalesce(p.offense,0) + coalesce(p.defense,0) "
                "+ coalesce(p.goalie_skill,0)) desc, p.name"
            ),
            {"slugs": slugs},
        ).mappings().all()
        by_pos: dict[str, list[dict]] = {pos: [] for pos in ALL_STAR_BALLOT}
        for r in rows:
            if r["position"] in by_pos:
                by_pos[r["position"]].append(
                    {"id": str(r["id"]), "legacy_id": r["legacy_id"], "name": r["name"],
                     "position": r["position"], "kind": "player",
                     "team_id": str(r["team_id"]), "team_slug": r["team_slug"],
                     "team_name": r["team_name"]}
                )
        out[conference] = by_pos
    return out


def _own_entity_ids(candidates: list[dict], owned_team_ids) -> set[str]:
    owned = {str(t) for t in owned_team_ids}
    return {c["id"] for c in candidates if c.get("team_id") in owned}


# -- submitting --------------------------------------------------------------
def _upsert_ballot(conn, season: int, kind: str, category: str, voter_user_id: str,
                   payload) -> None:
    conn.execute(
        text("insert into ballots (season, kind, category, voter_user_id, payload) "
             "values (:s, :k, :c, cast(:v as uuid), cast(:p as jsonb)) "
             "on conflict (season, kind, category, voter_user_id) "
             "do update set payload = excluded.payload, submitted_at = now()"),
        {"s": season, "k": kind, "c": category, "v": str(voter_user_id),
         "p": json.dumps(payload)},
    )


def submit_award_ballot(
    engine: Engine,
    season: int,
    award: str,
    voter_user_id: str,
    ranked_ids: list[str],
    owned_team_ids=(),
) -> dict:
    """Store one manager's ranked ballot for one award, replacing any earlier one."""
    with engine.begin() as conn:
        _sync_status(conn, season)
        _require_open(conn, season, AWARD_KIND)
        if award not in AWARDS:
            raise BallotError(f"{award!r} is not a voted award")
        candidates = (
            _coach_pool(conn) if award == COACH_AWARD
            else _player_pool(conn, season, _PLAYER_ELIGIBILITY.get(award, ""))
        )
        ids = validate_award_ballot(
            award,
            [str(i) for i in ranked_ids],
            eligible_ids={c["id"] for c in candidates},
            own_ids=_own_entity_ids(candidates, owned_team_ids),
        )
        _upsert_ballot(conn, season, AWARD_KIND, award, voter_user_id, ids)
    return {"season": season, "award": award, "ballot": ids}


def submit_all_star_ballot(
    engine: Engine,
    season: int,
    conference: str,
    voter_user_id: str,
    payload: dict,
    owned_team_ids=(),
) -> dict:
    """Store one manager's positional ballot for one conference, replacing any
    earlier one. A manager votes on BOTH conferences -- the All-Star game is a
    league event, and restricting each voter to their own half would make the
    Western squad the choice of sixteen managers instead of thirty-two."""
    with engine.begin() as conn:
        _sync_status(conn, season)
        _require_open(conn, season, ALL_STAR_KIND)
        if conference not in CONFERENCES:
            raise BallotError(f"{conference!r} is not a conference")
        pools = _all_star_candidates(conn, season)[conference]
        flat = [c for lst in pools.values() for c in lst]
        clean = validate_all_star_ballot(
            {pos: [str(i) for i in ids] for pos, ids in dict(payload).items()},
            eligible_by_position={pos: [c["id"] for c in lst] for pos, lst in pools.items()},
            own_ids=_own_entity_ids(flat, owned_team_ids),
        )
        _upsert_ballot(conn, season, ALL_STAR_KIND, conference, voter_user_id, clean)
    return {"season": season, "conference": conference, "ballot": clean}


# -- reading ballots back ----------------------------------------------------
def voter_ballots(engine: Engine, season: int, voter_user_id: str) -> dict[str, dict]:
    """This caller's saved ballots: {"award": {award: [ids]}, "allstar": {conf: {...}}}.

    A voter always sees their OWN ballot and never anyone else's: a vote in progress
    is sealed for the same reason a sealed free-agency offer is."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("select kind, category, payload from ballots "
                 "where season = :s and voter_user_id = cast(:v as uuid)"),
            {"s": season, "v": str(voter_user_id)},
        ).mappings().all()
    out: dict[str, dict] = {AWARD_KIND: {}, ALL_STAR_KIND: {}}
    for r in rows:
        out.setdefault(r["kind"], {})[r["category"]] = r["payload"]
    return out


def ballot_counts(engine: Engine, season: int) -> dict[str, dict[str, int]]:
    """{kind: {category: how many managers have voted}} -- the commissioner's view
    of turnout. A COUNT, never the contents: the panel says how many ballots are in,
    not what is on them."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("select kind, category, count(*) as n from ballots "
                 "where season = :s group by kind, category"),
            {"s": season},
        ).mappings().all()
    out: dict[str, dict[str, int]] = {AWARD_KIND: {}, ALL_STAR_KIND: {}}
    for r in rows:
        out.setdefault(r["kind"], {})[r["category"]] = int(r["n"])
    return out


def _ballots_for(conn, season: int, kind: str, category: str) -> list:
    return [
        r[0] for r in conn.execute(
            text("select payload from ballots where season = :s and kind = :k "
                 "and category = :c order by id"),
            {"s": season, "k": kind, "c": category},
        ).all()
    ]


def _entity_labels(conn, ids: list[str]) -> dict[str, dict]:
    """id -> {kind, name, legacy_id} over players AND coaches.

    Resolved at TALLY time from the tables rather than from the eligibility pools,
    because a ballot is counted as cast: a bench player promoted to the starting
    lineup after voting closed still earned the votes they were given."""
    if not ids:
        return {}
    out: dict[str, dict] = {}
    for table, kind in (("players", "player"), ("coaches", "coach")):
        for r in conn.execute(
            text(f"select id, legacy_id, name from {table} where id = any(cast(:ids as uuid[]))"),
            {"ids": ids},
        ).mappings().all():
            out[str(r["id"])] = {"kind": kind, "name": r["name"],
                                 "legacy_id": r["legacy_id"]}
    return out


# -- the count ---------------------------------------------------------------
def tally_awards(engine: Engine, season: int) -> dict:
    """Count every award's ballots, write the evidence to award_tallies and the
    winners to `awards`, and close the phase. Commissioner-driven and idempotent
    only in the sense that it runs once: a second call is refused because the
    status is no longer 'open'."""
    with engine.begin() as conn:
        _sync_status(conn, season)
        _require_open(conn, season, AWARD_KIND)

        conn.execute(text("delete from award_tallies where season = :s"), {"s": season})
        # Only the VOTED awards. `awards` also carries the auto-computed stat titles
        # (Top Scorer / Top Goalie) written at rollover, which are not ours to drop.
        conn.execute(
            text("delete from awards where season = :s and award = any(:labels)"),
            {"s": season, "labels": list(AWARDS)},
        )

        results: dict[str, dict | None] = {}
        for award in AWARDS:
            ballots = _ballots_for(conn, season, AWARD_KIND, award)
            if not ballots:
                results[award] = None
                continue
            named = sorted({str(i) for b in ballots for i in b})
            labels = _entity_labels(conn, named)
            # Drop ids that no longer resolve (a deleted row); everything else counts.
            ballots = [[str(i) for i in b if str(i) in labels] for b in ballots]
            ballots = [b for b in ballots if b]
            if not ballots:
                results[award] = None
                continue
            rows = tally_award(
                ballots, names={k: v["name"] for k, v in labels.items()}
            )
            for row in rows:
                conn.execute(
                    text("insert into award_tallies (season, award, entity_kind, entity_id, "
                         "points, first_place_votes, rank) "
                         "values (:s, :a, :k, cast(:e as uuid), :p, :f, :r)"),
                    {"s": season, "a": award, "k": labels[row.entity_id]["kind"],
                     "e": row.entity_id, "p": row.points,
                     "f": row.first_place_votes, "r": row.rank},
                )
            winner = rows[0]
            col = "coach_id" if labels[winner.entity_id]["kind"] == "coach" else "player_id"
            conn.execute(
                text(f"insert into awards ({col}, season, award) "
                     "values (cast(:e as uuid), :s, :a)"),
                {"e": winner.entity_id, "s": season, "a": award},
            )
            results[award] = {
                "entity_id": winner.entity_id,
                "entity_kind": labels[winner.entity_id]["kind"],
                "legacy_id": labels[winner.entity_id]["legacy_id"],
                "name": labels[winner.entity_id]["name"],
                "points": winner.points,
                "first_place_votes": winner.first_place_votes,
                "ballots": len(ballots),
            }

        conn.execute(
            text("update voting_status set status = 'tallied', closed_at = now() "
                 "where season = :s and kind = :k"),
            {"s": season, "k": AWARD_KIND},
        )
    return {"season": season, "winners": results}


def award_results(engine: Engine, season: int) -> list[dict]:
    """The finished count, award by award, best first -- what the Awards page
    renders. Empty until the commissioner tallies."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select at.award, at.entity_kind, at.entity_id, at.points, "
                "at.first_place_votes, at.rank, "
                "coalesce(p.name, c.name) as name, "
                "coalesce(p.legacy_id, c.legacy_id) as legacy_id, "
                "p.position, t.slug as team_slug, t.name as team_name "
                "from award_tallies at "
                "left join players p on p.id = at.entity_id "
                "left join coaches c on c.id = at.entity_id "
                "left join teams t on t.id = p.team_id "
                "where at.season = :s order by at.award, at.rank"
            ),
            {"s": season},
        ).mappings().all()
    by_award: dict[str, list[dict]] = {}
    for r in rows:
        by_award.setdefault(r["award"], []).append(
            {"entity_id": str(r["entity_id"]), "entity_kind": r["entity_kind"],
             "name": r["name"], "legacy_id": r["legacy_id"], "position": r["position"],
             "team_slug": r["team_slug"], "team_name": r["team_name"],
             "points": int(r["points"]), "first_place_votes": int(r["first_place_votes"]),
             "rank": int(r["rank"]) if r["rank"] is not None else None}
        )
    return [
        {"award": award, "winner": lines[0] if lines else None, "tally": lines}
        for award, lines in sorted(by_award.items(), key=lambda kv: AWARDS.index(kv[0])
                                   if kv[0] in AWARDS else 99)
    ]


def awarded_seasons(engine: Engine) -> list[int]:
    """Seasons that have a counted award vote, newest first -- the Awards page's
    season list."""
    with engine.connect() as conn:
        return [
            int(r[0]) for r in conn.execute(
                text("select distinct season from award_tallies order by season desc")
            ).all()
        ]


def assert_awards_tallied(engine: Engine, season: int) -> None:
    """Raise unless the season's award vote is settled. Called by /season/advance.

    What this protects is an UNCOUNTED VOTE. The rollover zeroes the records and
    stat lines every ballot was cast against, so an award not committed before it
    runs can never be committed at all -- the evidence is gone. Hence: a vote whose
    polls are open is in progress and the rollover waits for it, and ballots that
    exist without a tally are votes about to be thrown away.

    A season whose polls were never opened AND drew no ballots is let through. There
    is no vote there to destroy, and a league that does not use the ballot box (a
    fresh install, a test fixture, a commissioner who runs the awards by hand) should
    not be unable to start its next season because of a phase it never entered."""
    with engine.connect() as conn:
        status = _status(conn, season, AWARD_KIND)
        if status == "tallied":
            return
        cast = conn.execute(
            text("select count(*) from ballots where season = :s and kind = :k"),
            {"s": season, "k": AWARD_KIND},
        ).scalar_one()
    if status == "open" or cast:
        raise VotingError("tally the award ballots before advancing the season")


def status_of(engine: Engine, season: int, kind: str) -> str:
    """One kind's phase, without the opening side effect sync_status has."""
    with engine.connect() as conn:
        return _status(conn, season, kind)
