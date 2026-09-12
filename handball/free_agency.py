"""
Name: free_agency.py
Description: The offseason market's write path -- the SQL layer over
    handball/free_agency_rules.py, which holds every actual decision. This module
    reads snapshots, calls a pure function, and writes what it says. If you are
    looking for "what does the league consider the better offer" or "when is a board
    deadlocked", it is not here; it is there.

    The shape of a period (alembic 0011 for the tables):

        open_period          commissioner opens the offseason market
          └── round N        submit_offer / withdraw_offer, sealed
                close_offer_round     -> RFA windows, sole-offer signings, bidding
                  ├── match_offer / decline_match      restricted resolution
                  ├── place_bid                        match | raise | forfeit
                  ├── force_forfeit / award_auction    commissioner interventions
                  └── (last board resolves -> round complete)
                open_next_round       repeat while the last round drew offers
          └── close_period     leftovers become the ordinary 1yr/$0 pool again

    LOCK ORDER, league-wide, extending the team-then-player order signing_service
    already uses (skipping levels is always safe; taking them out of order is not):

        fa_periods -> fa_rounds -> fa_auctions -> teams -> players

    The round row is the reader/writer gate: manager actions take it FOR SHARE and
    run in parallel; the commissioner's phase transitions take it FOR UPDATE and
    therefore wait out every action in flight. Every path re-reads status AFTER
    acquiring its lock, so an offer queued behind a round close is told the round
    closed rather than acting on a stale snapshot.

    Signings go through signing_service.apply_signing -- the same write tail as the
    fixed 1yr/$0 pool deal -- so there is exactly one place in the codebase where a
    free agent becomes a rostered player.
Author: relational backend
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball import signing_service as signing
from handball.free_agency_rules import (  # noqa: F401 (FreeAgencyError re-exported)
    BID_FORFEIT,
    BID_MATCH,
    BID_RAISE,
    OUTCOME_ALL_FORFEITED,
    OUTCOME_COMMISSIONER_AWARD,
    OUTCOME_NO_OFFERS,
    OUTCOME_RFA_MATCHED,
    STATUS_AWAITING_AWARD,
    STATUS_BIDDING,
    STATUS_MATCHING,
    STATUS_RESOLVED,
    STATUS_VOID,
    AuctionInput,
    BidError,
    BiddingState,
    FreeAgencyError,
    Offer,
    OfferError,
    PendingOffer,
    Seat,
    TeamExposure,
    apply_bid,
    check_award,
    check_exposure,
    check_offer_legal,
    plan_after_decline,
    plan_auction,
    with_pending_signing,
)
from handball.league_views import DEFAULT_RULES, RosterRules
from handball.signing_service import SigningError
from handball.simulation_vars import FA_TURN_LIMIT_HOURS

LIVE_AUCTION_STATUSES = ("collecting", "matching", "bidding", "awaiting_award")

# The live statuses a board may be SHOWN in. 'collecting' is deliberately absent: a
# board is created by the first offer on a player, so while a round is taking offers
# the board and everything on it IS the sealed information -- its very existence says
# somebody bid on that player. Boards become public the moment the round closes, which
# is when 'collecting' turns into one of these. Managers see their own offers all
# along, layered on per caller by the API from team_offers().
PUBLIC_AUCTION_STATUSES = ("matching", "bidding", "awaiting_award")


# ---------------------------------------------------------------------------
# Period and round lifecycle (commissioner).
# ---------------------------------------------------------------------------
def open_period(engine: Engine, season: int, *, actor: str | None = None) -> dict:
    """Open the offseason market for `season`, with its first offer round. At most one
    period may be open league-wide (a partial unique index enforces it), and a season
    gets one period ever -- reopening an accidentally-closed one flips the same row,
    so "the period for season N" is never ambiguous."""
    with engine.begin() as conn:
        if _open_period(conn) is not None:
            raise FreeAgencyError("a free-agency period is already open")
        row = conn.execute(
            text("insert into fa_periods (season, opened_by) "
                 "values (:s, cast(:by as uuid)) "
                 "on conflict (season) do update set status = 'open', closed_at = null, "
                 "closed_by = null, opened_at = now(), opened_by = cast(:by as uuid) "
                 "returning id::text as id, season"),
            {"s": season, "by": actor},
        ).mappings().one()
        rnd = _insert_round(conn, row["id"], 1)
        _log(conn, row["id"], "period_opened", round_id=rnd["id"], actor=actor,
             by_commissioner=True, detail={"season": season})
    return {"period_id": row["id"], "season": season, "round_id": rnd["id"],
            "round_number": 1, "status": "offers"}


def close_offer_round(
    engine: Engine, *, actor: str | None = None, rules: RosterRules = DEFAULT_RULES
) -> dict:
    """Shut the sealed offer window and resolve every board in ONE transaction: RFA
    outcomes, sole-offer signings, and bidding queues. A half-resolved round is not a
    state anyone can reason about, so this is all-or-nothing and a retry is clean.

    Offers that were legal when submitted but are not any more (their team traded
    since) are dropped here rather than at signing time, so a board can fall from two
    offers to a sole-offer signing, or from one to nothing. See
    free_agency_rules.plan_auction."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn, exclusive=True)
        if rnd["status"] != "offers":
            raise FreeAgencyError(
                f"round {rnd['round_number']} is not taking offers (it is "
                f"{rnd['status']!r})")

        auctions = _round_auctions(conn, rnd["id"])
        _prelock(conn, auctions)

        summary = {"signed": 0, "matching": 0, "bidding": 0, "unsold": 0, "dropped": 0}
        offers_total = 0
        for row in auctions:
            offers = _auction_offers(conn, row["id"])
            offers_total += len(offers)
            stale = _stale_offer_ids(conn, row, offers, rules)
            plan = plan_auction(_auction_input(row, offers, stale))
            _apply_plan(conn, period, rnd, row, plan, rules=rules, actor=actor)
            summary["dropped"] += len(plan.voided_offer_ids)
            summary[_plan_bucket(plan)] += 1

        conn.execute(
            text("update fa_rounds set status = 'resolution', closed_at = now(), "
                 "offers_count = :o, auctions_count = :a where id = cast(:r as uuid)"),
            {"o": offers_total, "a": len(auctions), "r": rnd["id"]},
        )
        _log(conn, period["id"], "round_closed", round_id=rnd["id"], actor=actor,
             by_commissioner=True, detail=summary)
        complete = _maybe_complete_round(conn, rnd["id"])
    return {"round_number": rnd["round_number"], "offers": offers_total,
            "round_complete": complete, **summary}


def open_next_round(engine: Engine, *, actor: str | None = None) -> dict:
    """Open a fresh offer round on whoever is still unsigned. Refused while a board is
    live, and refused after a round that drew nothing -- that is the rule's termination
    condition, and the answer to it is to close the period."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn, exclusive=True)
        if rnd["status"] != "complete":
            raise FreeAgencyError(
                f"round {rnd['round_number']} is still live; resolve every board first")
        if not rnd["offers_count"]:
            raise FreeAgencyError(
                f"round {rnd['round_number']} drew no offers -- close free agency "
                f"instead of opening another round")
        nxt = _insert_round(conn, period["id"], rnd["round_number"] + 1)
        _log(conn, period["id"], "round_opened", round_id=nxt["id"], actor=actor,
             by_commissioner=True, detail={"round_number": rnd["round_number"] + 1})
    return {"round_id": nxt["id"], "round_number": rnd["round_number"] + 1}


def close_period(engine: Engine, *, actor: str | None = None) -> dict:
    """End the offseason market. Refused while any board is live. Everyone still
    unsigned goes back to being an ordinary free agent, signable at the fixed 1yr/$0
    pool deal -- and loses restricted status, because their former team has now had
    its window and "restricted" means the FIRST time a player reaches free agency.
    Bird rights (rights_team_id) are deliberately kept: they are a cap fact the pool
    signing path still uses, and nothing says they expire."""
    with engine.begin() as conn:
        period = _open_period(conn, for_update=True)
        if period is None:
            raise FreeAgencyError("no free-agency period is open")
        live = conn.execute(
            text("select count(*) from fa_auctions a join fa_rounds r on r.id = a.round_id "
                 f"where r.period_id = cast(:p as uuid) and a.status in {_IN_LIVE}"),
            {"p": period["id"]},
        ).scalar_one()
        if live:
            raise FreeAgencyError(
                f"{live} auction(s) are still live; resolve them before closing free agency")

        freed = conn.execute(
            text("update players set restricted_free_agent = false, updated_at = now() "
                 "where team_id is null and retired = false "
                 "and restricted_free_agent = true returning legacy_id")
        ).all()
        conn.execute(
            text("update fa_rounds set status = 'complete', completed_at = now() "
                 "where period_id = cast(:p as uuid) and status <> 'complete'"),
            {"p": period["id"]},
        )
        conn.execute(
            text("update fa_periods set status = 'closed', closed_at = now(), "
                 "closed_by = cast(:by as uuid) where id = cast(:p as uuid)"),
            {"by": actor, "p": period["id"]},
        )
        _log(conn, period["id"], "period_closed", actor=actor, by_commissioner=True,
             detail={"unrestricted": len(freed)})
    return {"season": period["season"], "unsigned_unrestricted": len(freed)}


# ---------------------------------------------------------------------------
# Offers (manager, during an offer round).
# ---------------------------------------------------------------------------
def submit_offer(
    engine: Engine,
    team_slug: str,
    legacy_id: str,
    term: int,
    value: int,
    *,
    actor: str | None = None,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """Place this team's offer to a free agent, replacing any offer it already has on
    them. Offers are append-only: the old row is superseded rather than edited, so the
    audit trail is honest and re-offering costs you your place in the submission-order
    tiebreak.

    The whole live set is revalidated afterwards, not just this offer -- adding a
    fourth promise can make the first unaffordable without touching its row."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn)
        if rnd["status"] != "offers":
            raise FreeAgencyError("the offer round has closed")

        team = signing.team_row(conn, team_slug)
        signing.lock_team(conn, team["id"])
        prow = signing.lock_player(conn, legacy_id)
        ctx = signing.signing_context(conn, team, prow)
        try:
            signing.check_signing_allowed(ctx, term, value, rules)
        except SigningError as e:
            raise OfferError(str(e)) from e

        auction = _get_or_create_auction(conn, rnd["id"], prow)
        if auction["status"] != "collecting":
            raise FreeAgencyError(
                f"{ctx.player_name}'s auction is already under way; offers are closed")
        _forbid_second_team_of_one_owner(conn, auction["id"], team["id"])

        old = conn.execute(
            text("update fa_offers set status = 'superseded', resolved_at = now() "
                 "where auction_id = :a and team_id = cast(:t as uuid) and status = 'open' "
                 "returning id"),
            {"a": auction["id"], "t": team["id"]},
        ).scalar()
        offer_id = conn.execute(
            text("insert into fa_offers (auction_id, team_id, term, value, supersedes_id, "
                 "submitted_by) values (:a, cast(:t as uuid), :term, :value, :old, "
                 "cast(:by as uuid)) returning id"),
            {"a": auction["id"], "t": team["id"], "term": term, "value": value,
             "old": old, "by": actor},
        ).scalar_one()

        check_exposure(_exposure(conn, team), rules)
        _log(conn, period["id"], "offer_edited" if old else "offer_submitted",
             round_id=rnd["id"], auction_id=auction["id"], team_id=team["id"], actor=actor,
             detail={"player": ctx.player_name, "term": term, "value": value})
    return {"offer_id": offer_id, "auction_id": auction["id"], "player_id": legacy_id,
            "team": team_slug, "term": term, "value": value, "replaced": old is not None}


def withdraw_offer(
    engine: Engine, team_slug: str, legacy_id: str, *, actor: str | None = None
) -> dict:
    """Pull this team's live offer while the round is still open."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn)
        if rnd["status"] != "offers":
            raise FreeAgencyError("the offer round has closed; you are committed")
        team = signing.team_row(conn, team_slug)
        signing.lock_team(conn, team["id"])
        row = conn.execute(
            text("update fa_offers o set status = 'withdrawn', resolved_at = now() "
                 "from fa_auctions a join players p on p.id = a.player_id "
                 "where o.auction_id = a.id and a.round_id = cast(:r as uuid) "
                 "and p.legacy_id = :lid and o.team_id = cast(:t as uuid) "
                 "and o.status = 'open' returning o.id, a.id as auction_id"),
            {"r": rnd["id"], "lid": legacy_id, "t": team["id"]},
        ).mappings().first()
        if row is None:
            raise FreeAgencyError(f"{team_slug} has no live offer to {legacy_id}")
        _log(conn, period["id"], "offer_withdrawn", round_id=rnd["id"],
             auction_id=row["auction_id"], team_id=team["id"], actor=actor,
             detail={"player": legacy_id})
    return {"offer_id": row["id"], "player_id": legacy_id, "team": team_slug}


# ---------------------------------------------------------------------------
# Restricted resolution.
# ---------------------------------------------------------------------------
def match_offer(
    engine: Engine,
    auction_id: int,
    team_slug: str,
    *,
    actor: str | None = None,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """The rights team matches the top offer exactly and keeps its restricted free
    agent. Distinct from a bidding MATCH on purpose: this one WINS the player.

    A match is a brand-new contract that converts immediately, so unlike an ordinary
    live offer it is not exposure-neutral -- the team's other promises are rechecked
    against the payroll it will have (with_pending_signing). If the match cannot be
    made legally, that is the rule's "cannot legally take the contract on", and the
    caller is told to decline or make room."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn)
        auction = _lock_auction(conn, auction_id)
        if auction["status"] != STATUS_MATCHING:
            raise FreeAgencyError("this player is not awaiting a match")
        team = signing.team_row(conn, team_slug)
        if str(auction["rights_team_id"]) != team["id"]:
            raise FreeAgencyError("only the team holding this player's rights may match")

        sheet = conn.execute(
            text("select term, value from fa_offers where id = :o"),
            {"o": auction["match_offer_id"]},
        ).mappings().one()

        signing.lock_team(conn, team["id"])
        prow = signing.lock_player(conn, auction["legacy_id"])
        offer_id = conn.execute(
            text("insert into fa_offers (auction_id, team_id, term, value, is_rfa_match, "
                 "origin, submitted_by) values (:a, cast(:t as uuid), :term, :value, true, "
                 "'match', cast(:by as uuid)) returning id"),
            {"a": auction_id, "t": team["id"], "term": sheet["term"],
             "value": sheet["value"], "by": actor},
        ).scalar_one()

        exposure = _exposure(conn, team, exclude_offer_ids=(offer_id,))
        check_exposure(with_pending_signing(exposure, sheet["value"]), rules)
        result = _sign(conn, team, prow, sheet["term"], sheet["value"], rules=rules)

        _resolve(conn, auction, OUTCOME_RFA_MATCHED, team["id"], offer_id,
                 sheet["term"], sheet["value"])
        _log(conn, period["id"], "rfa_matched", round_id=rnd["id"], auction_id=auction_id,
             team_id=team["id"], actor=actor,
             detail={"player": prow["name"], "term": sheet["term"], "value": sheet["value"]})
        _maybe_complete_round(conn, rnd["id"])
    return {"auction_id": auction_id, "player_id": auction["legacy_id"], "team": team_slug,
            "term": sheet["term"], "value": sheet["value"], "outcome": OUTCOME_RFA_MATCHED,
            "placed": result["placed"]}


def decline_match(
    engine: Engine,
    auction_id: int,
    team_slug: str | None = None,
    *,
    actor: str | None = None,
    forced: bool = False,
    by_commissioner: bool | None = None,
    reason: str | None = None,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """The rights team passes. The board is re-planned as an ordinary free agency with
    the offers already on the table -- the rights team's own lower offer included,
    which is what the rule says literally. `forced` is the commissioner declining for a
    rights holder who has stalled the board.

    `by_commissioner` is what the audit log records, and defaults to `forced` because
    that is what a forced decline normally is. The turn clock passes it False: an
    expiry is the league's own rule firing, not a person intervening, and a log that
    blamed the commissioner for it would be lying about the most important thing it
    records."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn)
        auction = _lock_auction(conn, auction_id)
        if auction["status"] != STATUS_MATCHING:
            raise FreeAgencyError("this player is not awaiting a match")
        if not forced:
            team = signing.team_row(conn, team_slug)
            if str(auction["rights_team_id"]) != team["id"]:
                raise FreeAgencyError("only the team holding this player's rights may decline")

        offers = _auction_offers(conn, auction_id)
        _prelock(conn, [auction])
        plan = plan_after_decline(_auction_input(auction, offers, frozenset()))
        _apply_plan(conn, period, rnd, auction, plan, rules=rules, actor=actor)
        _log(conn, period["id"], "rfa_declined", round_id=rnd["id"], auction_id=auction_id,
             team_id=str(auction["rights_team_id"]), actor=actor,
             by_commissioner=forced if by_commissioner is None else by_commissioner,
             detail={"player": auction["name"], "became": plan.status,
                     "reason": reason})
        _maybe_complete_round(conn, rnd["id"])
    return {"auction_id": auction_id, "status": plan.status, "outcome": plan.outcome}


# ---------------------------------------------------------------------------
# Sequential bidding.
# ---------------------------------------------------------------------------
def place_bid(
    engine: Engine,
    auction_id: int,
    team_slug: str,
    action: str,
    *,
    term: int | None = None,
    value: int | None = None,
    actor: str | None = None,
    forced: bool = False,
    by_commissioner: bool | None = None,
    reason: str | None = None,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """One turn: 'match', 'raise' or 'forfeit'. The auction row is locked first (it is
    the turn token), so two managers hitting the button at once serialize and the loser
    is told it is not their turn.

    `by_commissioner` is what the audit log records, defaulting to `forced`. The turn
    clock passes it False -- see decline_match."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn)
        auction = _lock_auction(conn, auction_id)
        if auction["status"] != STATUS_BIDDING:
            raise FreeAgencyError(
                f"this player is not open for bidding (the board is {auction['status']!r})")
        team = signing.team_row(conn, team_slug)
        # Lock EVERY team seated on this board, ascending, before the player. A forfeit
        # can resolve the board in favour of a different team, which then has to be
        # locked to sign -- and taking a team lock after the player lock would invert
        # the league-wide order. Locking the whole board up front costs one small lock
        # per team and removes the inversion entirely.
        for seat_team in _seat_team_ids(conn, auction["id"], team["id"]):
            signing.lock_team(conn, seat_team)
        prow = signing.lock_player(conn, auction["legacy_id"])

        state = _bidding_state(conn, auction)
        plan = apply_bid(state, team["id"], action, term=term, value=value, forced=forced)

        if plan.supersede_offer_id is not None:
            conn.execute(
                text("update fa_offers set status = 'superseded', resolved_at = now() "
                     "where id = :o"),
                {"o": plan.supersede_offer_id},
            )
        if plan.forfeit_offer_id is not None:
            conn.execute(
                text("update fa_offers set status = 'forfeited', resolved_at = now() "
                     "where id = :o"),
                {"o": plan.forfeit_offer_id},
            )
        if plan.forfeit_seat:
            conn.execute(
                text("update fa_auction_seats set state = 'forfeited', forfeited_at = now(), "
                     "forfeit_reason = :why where auction_id = :a and team_id = cast(:t as uuid)"),
                {"why": reason or ("commissioner" if forced else "voluntary"),
                 "a": auction_id, "t": team["id"]},
            )
        new_offer_id = None
        if plan.new_offer is not None:
            new_offer_id = conn.execute(
                text("insert into fa_offers (auction_id, team_id, term, value, origin, "
                     "submitted_by) values (:a, cast(:t as uuid), :term, :value, :origin, "
                     "cast(:by as uuid)) returning id"),
                {"a": auction_id, "t": team["id"], "term": plan.new_offer.term,
                 "value": plan.new_offer.value, "origin": action, "by": actor},
            ).scalar_one()
            # A bid is a bigger promise than the one it replaced; recheck the whole set.
            check_exposure(_exposure(conn, team), rules)

        _finish_bid(conn, period, rnd, auction, plan, new_offer_id, rules=rules, actor=actor)
        _log(conn, period["id"], f"bid_{action}", round_id=rnd["id"], auction_id=auction_id,
             team_id=team["id"], actor=actor,
             by_commissioner=forced if by_commissioner is None else by_commissioner,
             detail={"player": prow["name"], "term": plan.new_offer.term if plan.new_offer else None,
                     "value": plan.new_offer.value if plan.new_offer else None,
                     "reason": reason})
        _maybe_complete_round(conn, rnd["id"])
    return {"auction_id": auction_id, "action": action, "status": plan.status,
            "outcome": plan.outcome, "next_team_id": plan.turn_team_id,
            "winning_team_id": plan.winning_team_id}


def force_forfeit(
    engine: Engine,
    auction_id: int,
    team_slug: str,
    *,
    actor: str | None = None,
    reason: str = "commissioner",
    by_commissioner: bool = True,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """Commissioner: drop a team that is stalling a board, or one whose offer is no
    longer legal. Routes to a decline when the board is an unresolved RFA window --
    the equivalent intervention there.

    Also the one move the turn clock makes (sweep_expired_turns), which is why
    `by_commissioner` is a parameter: the mechanics of an expiry and an intervention
    are identical, but only one of them is a person."""
    with engine.connect() as conn:
        status = conn.execute(
            text("select status from fa_auctions where id = :a"), {"a": auction_id}
        ).scalar()
    if status == STATUS_MATCHING:
        return decline_match(engine, auction_id, actor=actor, forced=True,
                             by_commissioner=by_commissioner, reason=reason, rules=rules)
    return place_bid(engine, auction_id, team_slug, BID_FORFEIT, actor=actor,
                     forced=True, by_commissioner=by_commissioner, reason=reason,
                     rules=rules)


def award_auction(
    engine: Engine,
    auction_id: int,
    team_slug: str,
    *,
    reason: str | None = None,
    actor: str | None = None,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """Commissioner: break a no-raise deadlock by awarding the player to one of the
    teams still in, at the contract that team itself last offered. Reachable only from
    'awaiting_award' -- the tool for a stalling manager is force_forfeit, and keeping
    the two apart keeps the state machine honest."""
    with engine.begin() as conn:
        period, rnd = _lock_round(conn)
        auction = _lock_auction(conn, auction_id)
        if auction["status"] != STATUS_AWAITING_AWARD:
            raise FreeAgencyError("this board is not deadlocked; it does not need an award")
        team = signing.team_row(conn, team_slug)
        deal = check_award(_bidding_state(conn, auction), team["id"])

        signing.lock_team(conn, team["id"])
        prow = signing.lock_player(conn, auction["legacy_id"])
        result = _sign(conn, team, prow, deal.term, deal.value, rules=rules)
        _resolve(conn, auction, OUTCOME_COMMISSIONER_AWARD, team["id"], deal.offer_id,
                 deal.term, deal.value, award_reason=reason)
        _mark_losers(conn, auction_id, keep=deal.offer_id)
        _log(conn, period["id"], "awarded", round_id=rnd["id"], auction_id=auction_id,
             team_id=team["id"], actor=actor, by_commissioner=True,
             detail={"player": prow["name"], "term": deal.term, "value": deal.value,
                     "reason": reason})
        _maybe_complete_round(conn, rnd["id"])
    return {"auction_id": auction_id, "team": team_slug, "term": deal.term,
            "value": deal.value, "outcome": OUTCOME_COMMISSIONER_AWARD,
            "placed": result["placed"]}


# ---------------------------------------------------------------------------
# The turn clock.
# ---------------------------------------------------------------------------
# Sequential bidding is strictly ordered: the board waits on exactly one team, and a
# manager who stops answering stops the board, the round, and eventually the whole
# offseason. Before this there was no timer at all -- the only remedy was the
# commissioner noticing and forcing a forfeit by hand.
#
# There is no scheduler in this deployment (the one background slot belongs to a
# period or a playoff round), so the clock is LAZY: it advances whenever anyone reads
# the free-agency state, which the page polls while a board is live. That is enough
# for the property that matters -- a stalled board cannot stay stalled while anybody
# is watching -- and it needs no infrastructure. The common case is one indexed count
# that returns zero.
#
# What expiry does is exactly what the commissioner would have done by hand: forfeit
# the team on the clock, or decline the match window. It is never applied to
# 'awaiting_award', which is waiting on the COMMISSIONER -- expiring the league's own
# turn would be nonsense.
def overdue_turns(conn, *, limit_hours: int = FA_TURN_LIMIT_HOURS) -> list[dict]:
    """Boards whose turn has run out, oldest first. Cheap enough to call on every
    read: one index scan over the live boards of the open period."""
    rows = conn.execute(
        text("select a.id, a.status, a.waiting_since, "
             "coalesce(tt.slug, rt.slug) as team, "
             "coalesce(tt.name, rt.name) as team_name, p.name as player_name "
             "from fa_auctions a "
             "join fa_rounds r on r.id = a.round_id "
             "join fa_periods f on f.id = r.period_id and f.status = 'open' "
             "join players p on p.id = a.player_id "
             "left join teams tt on tt.id = a.turn_team_id "
             "left join teams rt on rt.id = a.rights_team_id "
             "where a.status in ('matching', 'bidding') "
             "  and a.waiting_since is not null "
             "  and a.waiting_since < now() - make_interval(hours => :h) "
             "order by a.waiting_since"),
        {"h": limit_hours},
    ).mappings().all()
    return [dict(r) for r in rows]


def sweep_expired_turns(engine: Engine, *, limit_hours: int = FA_TURN_LIMIT_HOURS,
                        rules: RosterRules = DEFAULT_RULES) -> list[dict]:
    """Act for every team that has run out its turn. Returns what was done, one entry
    per board, so a caller can tell the league about it.

    Races are expected and ignored: two managers polling at once both see the same
    overdue board, and the one that loses the auction lock finds it already moved on.
    Each board is its own transaction (force_forfeit opens one), so a board that
    cannot be swept -- an offer that has since become illegal, say -- does not stop
    the others."""
    with engine.connect() as conn:
        due = overdue_turns(conn, limit_hours=limit_hours)
    swept = []
    for board in due:
        try:
            force_forfeit(
                engine, int(board["id"]), board["team"],
                reason=f"turn expired: no response within {limit_hours}h",
                by_commissioner=False, rules=rules,
            )
        except (FreeAgencyError, BidError, SigningError):
            continue                    # already moved on, or cannot be forfeited
        swept.append({"auction_id": int(board["id"]), "player": board["player_name"],
                      "team": board["team"], "team_name": board["team_name"],
                      "was": board["status"]})
    return swept


# ---------------------------------------------------------------------------
# Hooks other modules call, inside THEIR transaction.
# ---------------------------------------------------------------------------
def open_period_row(conn) -> dict | None:
    """The open free-agency period, or None. signing_service's pool-signing guard and
    season_readiness both ask this question, so it is one indexed lookup."""
    return _open_period(conn)


def void_player_auctions(conn, player_uuids, *, reason: str) -> int:
    """Kill any live auction and live offers for these players -- they have left the
    market (retired). Releases every bidder's exposure. Called by
    offseason.retire_players inside its own transaction; returns auctions voided."""
    uuids = [str(u) for u in player_uuids]
    if not uuids:
        return 0
    rows = conn.execute(
        text("update fa_auctions set status = 'void', outcome = 'player_ineligible', "
             "turn_team_id = null, resolved_at = now(), award_reason = :why "
             f"where player_id = any(cast(:ids as uuid[])) and status in {_IN_LIVE} "
             "returning id"),
        {"why": reason, "ids": uuids},
    ).all()
    if rows:
        conn.execute(
            text("update fa_offers set status = 'void', resolved_at = now() "
                 "where auction_id = any(:aids) and status = 'open'"),
            {"aids": [r[0] for r in rows]},
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Reads.
# ---------------------------------------------------------------------------
def free_agency_state(engine: Engine, *,
                      turn_limit_hours: int = FA_TURN_LIMIT_HOURS) -> dict:
    """The period/round phase plus every PUBLIC board -- the backbone of the state
    document the website polls. Per-viewer detail (your offers, whose turn it is for
    your teams) is layered on by the API, which knows who is asking.

    Boards still 'collecting' are withheld: they belong to a round that is still taking
    sealed offers, and both the offers on them and the fact that they exist at all are
    the sealed information. See PUBLIC_AUCTION_STATUSES."""
    with engine.connect() as conn:
        period = _open_period(conn)
        if period is None:
            return {"period": None}
        rnd = conn.execute(
            text("select id::text as id, round_number, status, offers_count "
                 "from fa_rounds where period_id = cast(:p as uuid) "
                 "order by round_number desc limit 1"),
            {"p": period["id"]},
        ).mappings().first()
        boards = conn.execute(
            text("select a.id, a.status, a.restricted, a.no_raise_streak, "
                 "a.turn_team_id::text as turn_team_id, tt.slug as turn_team, "
                 "tt.name as turn_team_name, a.waiting_since, "
                 # The turn clock, computed in the database so the countdown is
                 # against the server's clock rather than the browser's. Null on a
                 # board that isn't waiting on a team (awaiting_award).
                 "case when a.status in ('matching','bidding') "
                 "     then a.waiting_since + make_interval(hours => :h) end as turn_deadline, "
                 "case when a.status in ('matching','bidding') "
                 "     then floor(extract(epoch from (a.waiting_since "
                 "          + make_interval(hours => :h) - now())))::bigint end as turn_seconds_left, "
                 "a.rights_team_id::text as rights_team_id, rt.slug as rights_team, "
                 "p.legacy_id as player_id, p.name as player_name, p.position "
                 "from fa_auctions a join players p on p.id = a.player_id "
                 "left join teams tt on tt.id = a.turn_team_id "
                 "left join teams rt on rt.id = a.rights_team_id "
                 f"where a.round_id = cast(:r as uuid) and a.status in {_IN_PUBLIC} "
                 "order by p.name"),
            {"r": rnd["id"], "h": turn_limit_hours},
        ).mappings().all()
        out = []
        for b in boards:
            out.append({**dict(b), "id": int(b["id"]),
                        "offers": _board_offers(conn, b["id"]),
                        "seats": _board_seats(conn, b["id"])})
        signings = conn.execute(
            text("select p.legacy_id as player_id, p.name as player_name, t.slug as team, "
                 "a.signed_term as term, a.signed_value as value, a.outcome, "
                 "r.round_number, a.resolved_at "
                 "from fa_auctions a join fa_rounds r on r.id = a.round_id "
                 "join players p on p.id = a.player_id "
                 "join teams t on t.id = a.winning_team_id "
                 "where r.period_id = cast(:p as uuid) and a.status = 'resolved' "
                 "order by a.resolved_at desc"),
            {"p": period["id"]},
        ).mappings().all()
    return {
        "period": {"id": period["id"], "season": period["season"], "status": period["status"]},
        "round": dict(rnd) if rnd else None,
        "auctions": out,
        "signings": [dict(s) for s in signings],
        "turn_limit_hours": turn_limit_hours,
    }


def period_history(engine: Engine, season: int | None = None) -> dict:
    """Who bid what, for every board that has FINISHED -- the public record of a
    closed round.

    Everything here was sealed once and is not any more. A board only leaves
    'collecting' when its round closes, and at that moment the offers on it become
    the public thing everyone is watching; a resolved or void board is that same
    information after the fact. So this reveals every offer ever made on a finished
    board -- including the losing ones, which is the whole point. What it will not
    show is a board still collecting: those rounds are still sealed (see
    PUBLIC_AUCTION_STATUSES), and they are excluded by status, not by round.

    `season` selects a period; the default is the most recent one, open or closed, so
    the page has something to show the moment a round resolves."""
    with engine.connect() as conn:
        period = conn.execute(
            text("select id::text as id, season, status from fa_periods "
                 + ("where season = :s " if season is not None else "")
                 + "order by season desc limit 1"),
            {"s": season} if season is not None else {},
        ).mappings().first()
        if period is None:
            return {"period": None, "boards": []}
        boards = conn.execute(
            text("select a.id, r.round_number, a.status, a.outcome, a.restricted, "
                 "a.signed_term, a.signed_value, a.resolved_at, a.award_reason, "
                 "p.legacy_id as player_id, p.name as player_name, p.position, "
                 "wt.slug as winning_team, wt.name as winning_team_name, "
                 "rt.slug as rights_team, rt.name as rights_team_name "
                 "from fa_auctions a "
                 "join fa_rounds r on r.id = a.round_id "
                 "join players p on p.id = a.player_id "
                 "left join teams wt on wt.id = a.winning_team_id "
                 "left join teams rt on rt.id = a.rights_team_id "
                 "where r.period_id = cast(:p as uuid) "
                 "  and a.status in ('resolved', 'void') "
                 "order by r.round_number, a.resolved_at, p.name"),
            {"p": period["id"]},
        ).mappings().all()
        # Every offer on those boards, in one query rather than one per board: a
        # period can hold a few hundred, and this is a page load.
        ids = [int(b["id"]) for b in boards]
        offers: dict[int, list[dict]] = {i: [] for i in ids}
        if ids:
            for o in conn.execute(
                text("select o.auction_id, o.id, t.slug as team, t.name as team_name, "
                     "o.term, o.value, o.status, o.origin, o.is_rfa_match, o.submitted_at "
                     "from fa_offers o join teams t on t.id = o.team_id "
                     "where o.auction_id = any(:a) order by o.auction_id, o.id"),
                {"a": ids},
            ).mappings().all():
                offers[int(o["auction_id"])].append(
                    {k: v for k, v in o.items() if k != "auction_id"})
    return {
        "period": {"id": period["id"], "season": period["season"],
                   "status": period["status"]},
        "boards": [{**dict(b), "id": int(b["id"]), "offers": offers[int(b["id"])]}
                   for b in boards],
    }


def team_offers(engine: Engine, team_slug: str) -> list[dict]:
    """This team's live offers -- what it has promised, and to whom."""
    with engine.connect() as conn:
        team = signing.team_row(conn, team_slug)
        rows = conn.execute(
            text("select o.id, o.term, o.value, o.auction_id, a.status as auction_status, "
                 "p.legacy_id as player_id, p.name as player_name, "
                 "coalesce(p.rights_team_id = o.team_id, false) as own_player "
                 "from fa_offers o join fa_auctions a on a.id = o.auction_id "
                 "join players p on p.id = a.player_id "
                 "where o.team_id = cast(:t as uuid) and o.status = 'open' "
                 "order by p.name"),
            {"t": team["id"]},
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------
_IN_LIVE = "(" + ", ".join(f"'{s}'" for s in LIVE_AUCTION_STATUSES) + ")"
_IN_PUBLIC = "(" + ", ".join(f"'{s}'" for s in PUBLIC_AUCTION_STATUSES) + ")"


def _open_period(conn, *, for_update: bool = False) -> dict | None:
    row = conn.execute(
        text("select id::text as id, season, status from fa_periods where status = 'open'"
             + (" for update" if for_update else ""))
    ).mappings().first()
    return dict(row) if row else None


def _lock_round(conn, *, exclusive: bool = False) -> tuple[dict, dict]:
    """Take the phase gate and re-read it. Managers take it FOR SHARE and run in
    parallel; the commissioner's transitions take it FOR UPDATE and wait them out.
    Re-reading AFTER the lock is what turns "my offer raced the round close" into a
    clean error instead of a write into a closed round."""
    period = _open_period(conn, for_update=exclusive)
    if period is None:
        raise FreeAgencyError("no free-agency period is open")
    rnd = conn.execute(
        text("select id::text as id, round_number, status, offers_count from fa_rounds "
             "where period_id = cast(:p as uuid) order by round_number desc limit 1 "
             + ("for update" if exclusive else "for share")),
        {"p": period["id"]},
    ).mappings().first()
    if rnd is None:
        raise FreeAgencyError("this free-agency period has no round")
    return period, dict(rnd)


def _insert_round(conn, period_id: str, number: int) -> dict:
    row = conn.execute(
        text("insert into fa_rounds (period_id, round_number) "
             "values (cast(:p as uuid), :n) returning id::text as id, round_number"),
        {"p": period_id, "n": number},
    ).mappings().one()
    return dict(row)


def _lock_auction(conn, auction_id: int) -> dict:
    row = conn.execute(
        text("select a.*, p.legacy_id, p.name from fa_auctions a "
             "join players p on p.id = a.player_id where a.id = :a for update of a"),
        {"a": auction_id},
    ).mappings().first()
    if row is None:
        raise FreeAgencyError(f"no auction {auction_id}")
    return dict(row)


def _get_or_create_auction(conn, round_id: str, prow) -> dict:
    """The board for this player in this round, created on the FIRST offer. Restricted
    status and the rights holder are snapshot here, so the deal a manager was shown
    cannot change under them mid-round."""
    conn.execute(
        text("insert into fa_auctions (round_id, player_id, restricted, rights_team_id) "
             "values (cast(:r as uuid), cast(:p as uuid), :restricted, :rights) "
             "on conflict (round_id, player_id) do nothing"),
        {"r": round_id, "p": str(prow["id"]),
         "restricted": bool(prow["restricted_free_agent"]) and prow["rights_team_id"] is not None,
         "rights": prow["rights_team_id"]},
    )
    row = conn.execute(
        text("select * from fa_auctions where round_id = cast(:r as uuid) "
             "and player_id = cast(:p as uuid) for update"),
        {"r": round_id, "p": str(prow["id"])},
    ).mappings().one()
    return dict(row)


def _forbid_second_team_of_one_owner(conn, auction_id: int, team_id: str) -> None:
    """A manager who owns several teams may back only ONE of them per player. Bidding
    against yourself is a free option: park a second team in the auction, drive a rival
    up, forfeit at the end, cost nothing. Trades have commissioner approval as their
    check on self-dealing; a sealed auction has none, so the rule is structural."""
    clash = conn.execute(
        text("select t.slug from fa_offers o join teams t on t.id = o.team_id "
             "where o.auction_id = :a and o.status = 'open' and o.team_id <> cast(:t as uuid) "
             "and t.owner_id is not null and t.owner_id = "
             "  (select owner_id from teams where id = cast(:t as uuid)) limit 1"),
        {"a": auction_id, "t": team_id},
    ).scalar()
    if clash:
        raise OfferError(
            f"you already have a live offer on this player from {clash}; a manager may "
            f"back only one of their teams per player")


def _round_auctions(conn, round_id: str) -> list[dict]:
    rows = conn.execute(
        text("select a.*, p.legacy_id, p.name, p.retired, p.team_id as player_team_id "
             "from fa_auctions a join players p on p.id = a.player_id "
             "where a.round_id = cast(:r as uuid) and a.status = 'collecting' order by a.id"),
        {"r": round_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _auction_offers(conn, auction_id: int) -> list[dict]:
    rows = conn.execute(
        text("select id, team_id::text as team_id, term, value from fa_offers "
             "where auction_id = :a and status = 'open' order by id"),
        {"a": auction_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _auction_input(row: dict, offers: list[dict], stale: frozenset) -> AuctionInput:
    return AuctionInput(
        auction_id=int(row["id"]),
        player_id=row["legacy_id"],
        player_name=row["name"],
        offers=tuple(Offer(o["id"], o["team_id"], o["term"], o["value"]) for o in offers),
        restricted=bool(row["restricted"]),
        rights_team_id=str(row["rights_team_id"]) if row["rights_team_id"] else None,
        eligible=not row.get("retired", False) and row.get("player_team_id") is None,
        illegal_offer_ids=stale,
    )


def _stale_offer_ids(conn, row: dict, offers: list[dict], rules: RosterRules) -> frozenset:
    """Offers that were legal when submitted but are not any more -- their team traded
    since. Checked here, at the last moment before an offer can become a contract, so a
    board re-plans without it rather than resolving into a signing the team can't make."""
    stale = set()
    for o in offers:
        team = conn.execute(
            text("select id::text as id, slug, name from teams where id = cast(:t as uuid)"),
            {"t": o["team_id"]},
        ).mappings().one()
        payroll, roster = signing.payroll_and_roster(conn, team["id"])
        exposure = TeamExposure(team_id=team["id"], team_name=team["name"],
                                payroll=payroll, roster_size=roster)
        own = row["rights_team_id"] is not None and str(row["rights_team_id"]) == team["id"]
        try:
            check_offer_legal(
                exposure,
                PendingOffer(o["id"], row["legacy_id"], row["name"], o["term"], o["value"], own),
                rules,
            )
        except OfferError:
            stale.add(o["id"])
    return frozenset(stale)


def _prelock(conn, auctions: list[dict]) -> None:
    """Lock every team and player the coming writes will touch, each individually and
    in ascending id order. Ascending order is the deadlock rule (trade approval takes
    team locks too); individually because Postgres does not promise FOR UPDATE
    acquires locks in an ORDER BY's order when the sort runs above the scan."""
    if not auctions:
        return
    ids = [int(a["id"]) for a in auctions]
    teams = conn.execute(
        text("select distinct team_id::text as t from fa_offers "
             "where auction_id = any(:a) and status = 'open' order by 1"),
        {"a": ids},
    ).scalars().all()
    for team_id in sorted(teams):
        signing.lock_team(conn, team_id)
    for player_uuid in sorted(str(a["player_id"]) for a in auctions):
        conn.execute(
            text("select 1 from players where id = cast(:p as uuid) for update"),
            {"p": player_uuid},
        ).first()


def _seat_team_ids(conn, auction_id: int, *extra: str) -> list[str]:
    """Every team seated on this board plus the caller's, sorted -- the ascending lock
    order the deadlock rule requires."""
    seated = conn.execute(
        text("select team_id::text from fa_auction_seats where auction_id = :a"),
        {"a": auction_id},
    ).scalars().all()
    return sorted({*seated, *extra})


def _plan_bucket(plan) -> str:
    return {STATUS_RESOLVED: "signed", STATUS_MATCHING: "matching",
            STATUS_BIDDING: "bidding", STATUS_VOID: "unsold"}[plan.status]


def _apply_plan(conn, period, rnd, row, plan, *, rules, actor) -> None:
    """Write out one AuctionPlan: sign, open a match window, seat a bidding board, or
    void the player."""
    if plan.voided_offer_ids:
        conn.execute(
            text("update fa_offers set status = 'void', resolved_at = now() where id = any(:o)"),
            {"o": list(plan.voided_offer_ids)},
        )
    if plan.lost_offer_ids:
        conn.execute(
            text("update fa_offers set status = 'lost', resolved_at = now() where id = any(:o)"),
            {"o": list(plan.lost_offer_ids)},
        )

    if plan.status == STATUS_RESOLVED:
        team = conn.execute(
            text("select id::text as id, slug, name from teams where id = cast(:t as uuid)"),
            {"t": plan.winning_team_id},
        ).mappings().one()
        prow = signing.lock_player(conn, row["legacy_id"])
        _sign(conn, team, prow, plan.winning_term, plan.winning_value, rules=rules)
        _resolve(conn, row, plan.outcome, team["id"], plan.winning_offer_id,
                 plan.winning_term, plan.winning_value)
        _log(conn, period["id"], "signed", round_id=rnd["id"], auction_id=row["id"],
             team_id=team["id"], actor=actor,
             detail={"player": row["name"], "term": plan.winning_term,
                     "value": plan.winning_value, "how": plan.outcome})
    elif plan.status == STATUS_MATCHING:
        conn.execute(
            text("update fa_auctions set status = 'matching', match_offer_id = :o, "
                 "waiting_since = now() where id = :a"),
            {"o": plan.match_offer_id, "a": row["id"]},
        )
    elif plan.status == STATUS_BIDDING:
        for seat in plan.seats:
            conn.execute(
                text("insert into fa_auction_seats (auction_id, team_id, turn_order) "
                     "values (:a, cast(:t as uuid), :o)"),
                {"a": row["id"], "t": seat.team_id, "o": seat.turn_order},
            )
        conn.execute(
            text("update fa_auctions set status = 'bidding', turn_team_id = cast(:t as uuid), "
                 "waiting_since = now() where id = :a"),
            {"t": plan.turn_team_id, "a": row["id"]},
        )
    else:                                   # void
        conn.execute(
            text("update fa_auctions set status = 'void', outcome = :o, "
                 "resolved_at = now() where id = :a"),
            {"o": plan.outcome, "a": row["id"]},
        )


def _finish_bid(conn, period, rnd, auction, plan, new_offer_id, *, rules, actor) -> None:
    """Persist the board state a BidPlan implies: pass the turn, resolve to a winner,
    park it for the commissioner, or void it."""
    if plan.status == STATUS_BIDDING:
        conn.execute(
            text("update fa_auctions set turn_team_id = cast(:t as uuid), "
                 "no_raise_streak = :s, waiting_since = now() where id = :a"),
            {"t": plan.turn_team_id, "s": plan.no_raise_streak, "a": auction["id"]},
        )
    elif plan.status == STATUS_AWAITING_AWARD:
        conn.execute(
            text("update fa_auctions set status = 'awaiting_award', turn_team_id = null, "
                 "no_raise_streak = :s, waiting_since = now() where id = :a"),
            {"s": plan.no_raise_streak, "a": auction["id"]},
        )
    elif plan.status == STATUS_RESOLVED:
        team = conn.execute(
            text("select id::text as id, slug, name from teams where id = cast(:t as uuid)"),
            {"t": plan.winning_team_id},
        ).mappings().one()
        # Already locked by place_bid, which takes every seated team before the player.
        prow = signing.lock_player(conn, auction["legacy_id"])
        _sign(conn, team, prow, plan.winning_term, plan.winning_value, rules=rules)
        won = conn.execute(
            text("select id from fa_offers where auction_id = :a and team_id = cast(:t as uuid) "
                 "and status = 'open' order by id desc limit 1"),
            {"a": auction["id"], "t": team["id"]},
        ).scalar()
        _resolve(conn, auction, plan.outcome, team["id"], won,
                 plan.winning_term, plan.winning_value)
        _mark_losers(conn, auction["id"], keep=won)
        _log(conn, period["id"], "signed", round_id=rnd["id"], auction_id=auction["id"],
             team_id=team["id"], actor=actor,
             detail={"player": auction["name"], "term": plan.winning_term,
                     "value": plan.winning_value, "how": plan.outcome})
    else:                                   # void: everybody walked away
        conn.execute(
            text("update fa_auctions set status = 'void', outcome = :o, turn_team_id = null, "
                 "resolved_at = now() where id = :a"),
            {"o": plan.outcome or OUTCOME_ALL_FORFEITED, "a": auction["id"]},
        )
        conn.execute(
            text("update fa_offers set status = 'void', resolved_at = now() "
                 "where auction_id = :a and status = 'open'"),
            {"a": auction["id"]},
        )


def _sign(conn, team, prow, term: int, value: int, *, rules: RosterRules) -> dict:
    """Turn a won offer into a contract, through the same write path as every other
    signing. The cap is checked here for real, against CURRENT facts -- an offer being
    legal when it was made is not enough."""
    ctx = signing.signing_context(conn, team, prow)
    try:
        signing.check_signing_allowed(ctx, term, value, rules)
    except SigningError as e:
        raise FreeAgencyError(
            f"{team['name']} can no longer sign {ctx.player_name} for {term}yr/"
            f"${value}M: {e}") from e
    return signing.apply_signing(conn, team["id"], prow, term, value, rules=rules)


def _resolve(conn, auction, outcome: str, team_id: str, offer_id, term: int, value: int,
             *, award_reason: str | None = None) -> None:
    conn.execute(
        text("update fa_auctions set status = 'resolved', outcome = :outcome, "
             "winning_team_id = cast(:t as uuid), winning_offer_id = :o, signed_term = :term, "
             "signed_value = :value, turn_team_id = null, award_reason = :why, "
             "resolved_at = now() where id = :a"),
        {"outcome": outcome, "t": team_id, "o": offer_id, "term": term, "value": value,
         "why": award_reason, "a": auction["id"]},
    )
    if offer_id is not None:
        conn.execute(
            text("update fa_offers set status = 'won', resolved_at = now() where id = :o"),
            {"o": offer_id},
        )
    _mark_losers(conn, auction["id"], keep=offer_id)


def _mark_losers(conn, auction_id: int, *, keep) -> None:
    conn.execute(
        text("update fa_offers set status = 'lost', resolved_at = now() "
             "where auction_id = :a and status = 'open' and (:keep is null or id <> :keep)"),
        {"a": auction_id, "keep": keep},
    )


def _maybe_complete_round(conn, round_id: str) -> bool:
    """A round is complete when no board in it is still live. Run at the end of every
    resolving transaction, so the round never sits finished-but-open."""
    live = conn.execute(
        text(f"select count(*) from fa_auctions where round_id = cast(:r as uuid) "
             f"and status in {_IN_LIVE}"),
        {"r": round_id},
    ).scalar_one()
    if live:
        return False
    conn.execute(
        text("update fa_rounds set status = 'complete', completed_at = now() "
             "where id = cast(:r as uuid) and status <> 'complete'"),
        {"r": round_id},
    )
    return True


def _bidding_state(conn, auction) -> BiddingState:
    seats = conn.execute(
        text("select team_id::text as team_id, turn_order, state from fa_auction_seats "
             "where auction_id = :a order by turn_order"),
        {"a": auction["id"]},
    ).mappings().all()
    offers = _auction_offers(conn, auction["id"])
    return BiddingState(
        auction_id=int(auction["id"]),
        seats=tuple(Seat(s["team_id"], s["turn_order"], s["state"] == "active") for s in seats),
        offers=tuple(Offer(o["id"], o["team_id"], o["term"], o["value"]) for o in offers),
        turn_team_id=str(auction["turn_team_id"]) if auction["turn_team_id"] else None,
        no_raise_streak=auction["no_raise_streak"],
    )


def _exposure(conn, team, *, exclude_offer_ids: tuple[int, ...] = ()) -> TeamExposure:
    """Everything this team has promised right now: its live offers across every board,
    with Bird rights resolved per player (the ceiling differs for each)."""
    payroll, roster = signing.payroll_and_roster(conn, team["id"])
    rows = conn.execute(
        text("select o.id, o.term, o.value, p.legacy_id as player_id, p.name as player_name, "
             "coalesce(p.rights_team_id = o.team_id, false) as own_player "
             "from fa_offers o join fa_auctions a on a.id = o.auction_id "
             "join players p on p.id = a.player_id "
             "where o.team_id = cast(:t as uuid) and o.status = 'open'"),
        {"t": team["id"]},
    ).mappings().all()
    return TeamExposure(
        team_id=team["id"], team_name=team["name"], payroll=payroll, roster_size=roster,
        live_offers=tuple(
            PendingOffer(r["id"], r["player_id"], r["player_name"], r["term"], r["value"],
                         r["own_player"])
            for r in rows if r["id"] not in exclude_offer_ids
        ),
    )


def _board_offers(conn, auction_id: int) -> list[dict]:
    rows = conn.execute(
        text("select o.id, t.slug as team, t.name as team_name, o.term, o.value "
             "from fa_offers o join teams t on t.id = o.team_id "
             "where o.auction_id = :a and o.status = 'open' order by o.value desc, o.term desc, o.id"),
        {"a": auction_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _board_seats(conn, auction_id: int) -> list[dict]:
    rows = conn.execute(
        text("select s.turn_order, s.state, t.slug as team, t.name as team_name "
             "from fa_auction_seats s join teams t on t.id = s.team_id "
             "where s.auction_id = :a order by s.turn_order"),
        {"a": auction_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _log(conn, period_id: str, action: str, *, round_id: str | None = None,
         auction_id: int | None = None, team_id: str | None = None,
         actor: str | None = None, by_commissioner: bool = False,
         detail: dict | None = None) -> None:
    conn.execute(
        text("insert into fa_actions (period_id, round_id, auction_id, team_id, "
             "actor_user_id, by_commissioner, action, detail) "
             "values (cast(:p as uuid), cast(:r as uuid), :a, cast(:t as uuid), "
             "cast(:actor as uuid), :comm, :action, cast(:detail as jsonb))"),
        {"p": period_id, "r": round_id, "a": auction_id, "t": team_id, "actor": actor,
         "comm": by_commissioner, "action": action, "detail": json.dumps(detail or {})},
    )
