"""
Name: draft.py
Description: The live draft -- the write path over alembic 0014's draft_lotteries /
    draft_state / draft_prospects and the new draft_picks columns. Every actual
    decision lives in handball/draft_rules.py; this module reads snapshots, calls a
    pure function, and writes what it says.

    The shape of a draft:

        (rollover)        seed_draft_order writes round 1 picks M+1..N from the
                          bracket, round 2 in full, the lottery POOL, and a
                          draft_state row at 'pending'
          └── run_lottery         commissioner draws picks 1..M, then resolves every
                                  protection the draw just decided   -> 'lottery_drawn'
          └── upload_prospects    commissioner puts the class on the board
          └── open_draft          -> 'open', the clock starts on pick 1
                make_pick               the holder's manager, or the commissioner,
                                        or -- after DRAFT_TURN_LIMIT_HOURS -- the clock
                ...                     repeat, 1..N*rounds
          └── (last pick)   -> 'complete', and every undrafted prospect becomes an
                               ordinary free agent

    LOCK ORDER extends the league's existing one (free_agency.py: fa_periods ->
    fa_rounds -> fa_auctions -> teams -> players). The draft sits at the top of its
    own chain and then rejoins:

        draft_state -> teams -> players

    draft_state is the room's single gate: EVERY pick takes it FOR UPDATE, so two
    managers hammering the button on the same turn serialize and the second is told
    the pick is already in. It is also what makes the auto-pick safe against a race
    with a real one -- the clock takes the same lock, so a manager who beats it by a
    second wins, and the sweep finds the turn already moved on.

    THE TURN CLOCK IS LAZY, for the same reason free agency's is: there is no
    scheduler in this deployment, so an expired turn is swept by whoever reads the
    state, and the draft room polls that read. A stalled draft cannot stay stalled
    while anybody is watching.

    Signing a pick does NOT go through signing_service: a draftee is not a free
    agent, there is no pool to check them out of, and the rookie scale is exempt
    from the cap by design (a team must be able to sign its picks -- an over-cap
    team becomes a season_readiness blocker instead). What it does share is the
    tail: the contract goes on through domain.Player.update_contract, and the lineup
    is rebuilt best-effort with roster_layout.try_rebuild_layout, exactly as a pool
    signing into a full roster does.
Author: rules alignment
"""
from __future__ import annotations

import json
import os
import random
import re
import tempfile
from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.draft_rules import (
    DraftRulesError,
    best_available,
    build_draft_order,
    draw_lottery,
    pick_coordinates,
    prospect_rating,
    resolve_protection,
    rookie_deal,
)
from handball.league_views import DEFAULT_RULES, RosterRules
from handball.pg_repository import PLAYER_SCALAR_COLS
from handball.repository import _player_from_dict
from handball.roster_layout import try_rebuild_layout
from handball.signing_service import lock_team
from handball.simulation_vars import (
    DRAFT_ROUNDS,
    DRAFT_TURN_LIMIT_HOURS,
    LOTTERY_WEIGHTS,
)

STATUS_PENDING = "pending"
STATUS_LOTTERY_DRAWN = "lottery_drawn"
STATUS_OPEN = "open"
STATUS_COMPLETE = "complete"


class DraftError(RuntimeError):
    """Something the draft's rules forbid -- drawing a lottery twice, opening a room
    with no board, picking out of turn. Carries a sentence fit to show the manager
    who tried it, the way FreeAgencyError and SigningError do."""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


# ---------------------------------------------------------------------------
# Seeding, at the rollover.
# ---------------------------------------------------------------------------
def playoff_results(conn, season: int) -> tuple[dict[int, list[str]], str | None]:
    """({round: [losing team slugs]}, champion slug) from `season`'s bracket.

    Read off playoff_series, which records a round number and a winner per matchup,
    so the loser is simply the other side of a decided series. Undecided series are
    skipped: a half-played bracket contributes the rounds it did finish, which is
    the most that can honestly be said about it.

    Empty when the league played no postseason -- build_draft_order then treats the
    whole league as non-playoff, which is the pre-playoff behaviour."""
    rows = conn.execute(
        text("select ps.round, "
             "  hi.slug as high, lo.slug as low, wt.slug as winner "
             "from playoff_series ps "
             "join teams hi on hi.id = ps.high_seed_team_id "
             "join teams lo on lo.id = ps.low_seed_team_id "
             "left join teams wt on wt.id = ps.winner_team_id "
             "where ps.season = :s order by ps.round"),
        {"s": season},
    ).mappings().all()

    losers: dict[int, list[str]] = {}
    champion, last_round = None, -1
    for r in rows:
        if r["winner"] is None:
            continue
        loser = r["low"] if r["winner"] == r["high"] else r["high"]
        losers.setdefault(int(r["round"]), []).append(loser)
        if int(r["round"]) > last_round:
            last_round, champion = int(r["round"]), r["winner"]
    return losers, champion


def seed_draft_order(conn, ranked_team_ids: list[str], new_season: int,
                     finished_season: int | None = None) -> int:
    """Write next season's pick order, inside the rollover's transaction. Returns
    picks seeded.

    `ranked_team_ids` is best->worst, read before the rollover zeroes the records.
    `finished_season` is the season whose bracket decides the playoff half of round
    1; with None (or with no bracket) every team counts as a non-playoff team and
    this degrades to plain reverse standings -- see draft_rules.build_draft_order.

    Round 1 picks 1..M are left with pick_number NULL on purpose: those slots belong
    to the lottery, and a number written here would be a number somebody could read
    before the draw. The pool that will be drawn over is recorded in
    draft_lotteries.standings_order instead, because the odds depend on an ordering
    of teams the rollover is about to destroy.

    Upserts on (season, round, original_team_id), always setting pick_number but
    defaulting holder_team_id = original_team_id only on a true insert -- so a pick
    traded years ahead keeps its holder. Idempotent for the season."""
    losers, champion = ({}, None)
    if finished_season is not None:
        losers, champion = playoff_results(conn, finished_season)
    order = build_draft_order(ranked_team_ids, losers, champion)

    slug_to_id = {slug: str(tid) for slug, tid in
                  conn.execute(text("select slug, id from teams")).all()}
    teams = order.teams

    # A league that played no postseason holds no lottery: there is no field to draw
    # over, and "the teams that missed the playoffs" is every team. Round 1 is then
    # plain reverse standings, exactly as it was before the draft existed, and no
    # draft_state row is written -- so a league in that shape is never gated on a
    # draft phase it does not have (see is_complete).
    drafting = bool(order.round_one_playoff)

    rows: list[dict] = []
    # Round 1: the lottery pool first (numbered only when there is no lottery to
    # draw), then the playoff teams.
    for i, slug in enumerate(order.lottery_pool):
        if slug in slug_to_id:
            rows.append({"s": new_season, "r": 1, "tid": slug_to_id[slug],
                         "n": None if drafting else i + 1})
    for i, slug in enumerate(order.round_one_playoff):
        if slug in slug_to_id:
            rows.append({"s": new_season, "r": 1, "tid": slug_to_id[slug],
                         "n": order.lottery_slots + i + 1})
    # Round 2 onwards: record order, non-playoff teams ahead of playoff teams.
    for rnd in range(2, DRAFT_ROUNDS + 1):
        for i, slug in enumerate(order.round_two):
            if slug in slug_to_id:
                rows.append({"s": new_season, "r": rnd, "tid": slug_to_id[slug],
                             "n": (rnd - 1) * teams + i + 1})
    if not rows:
        return 0

    conn.execute(
        text("insert into draft_picks "
             "(season, round, original_team_id, holder_team_id, pick_number) "
             "values (:s, :r, cast(:tid as uuid), cast(:tid as uuid), :n) "
             "on conflict (season, round, original_team_id) "
             "do update set pick_number = excluded.pick_number"),
        rows,
    )

    if not drafting:
        return len(rows)

    # The lottery pool and the room's cursor. Neither is overwritten once it has
    # been used: a re-run rollover must not redraw a lottery or reopen a draft.
    conn.execute(
        text("insert into draft_lotteries (season, standings_order) "
             "values (:s, cast(:order as jsonb)) "
             "on conflict (season) do update set standings_order = excluded.standings_order "
             "where draft_lotteries.results is null"),
        {"s": new_season,
         "order": json.dumps([slug_to_id[s] for s in order.lottery_pool if s in slug_to_id])},
    )
    conn.execute(
        text("insert into draft_state (season, status) values (:s, 'pending') "
             "on conflict (season) do nothing"),
        {"s": new_season},
    )
    return len(rows)


# ---------------------------------------------------------------------------
# State reads and the gate every write goes through.
# ---------------------------------------------------------------------------
def _state(conn, season: int, *, for_update: bool = False) -> dict | None:
    row = conn.execute(
        text("select season, status, current_overall, turn_started_at, "
             "(turn_started_at is not null and turn_started_at < "
             "   now() - make_interval(hours => :h)) as turn_expired "
             "from draft_state where season = :s"
             + (" for update" if for_update else "")),
        {"s": season, "h": DRAFT_TURN_LIMIT_HOURS},
    ).mappings().first()
    return dict(row) if row else None


def _require_state(conn, season: int, *, for_update: bool = False) -> dict:
    row = _state(conn, season, for_update=for_update)
    if row is None:
        raise DraftError(
            f"there is no draft for {season}; one is created by the season rollover")
    return row


def _total_picks(conn, season: int) -> int:
    return int(conn.execute(
        text("select count(*) from draft_picks where season = :s"), {"s": season}
    ).scalar_one())


def status(engine: Engine, season: int) -> str | None:
    """The draft's status for `season`, or None if the league has no draft for it."""
    with engine.connect() as conn:
        row = _state(conn, season)
    return row["status"] if row else None


def is_complete(engine: Engine, season: int) -> bool:
    """Whether `season`'s draft has finished. A season with NO draft counts as
    complete: leagues that predate the draft (and every offline fixture) must not be
    blocked by a phase they never had."""
    st = status(engine, season)
    return st is None or st == STATUS_COMPLETE


def assert_complete(engine: Engine, season: int) -> None:
    """Enforcement point for the phases that must wait for the draft -- free agency
    opening, and the season_readiness check on period 1. Raises DraftError with the
    sentence to show the commissioner."""
    st = status(engine, season)
    if st is None or st == STATUS_COMPLETE:
        return
    where = {
        STATUS_PENDING: "the lottery has not been drawn",
        STATUS_LOTTERY_DRAWN: "the room has not opened",
        STATUS_OPEN: "the draft is still on the clock",
    }.get(st, f"the draft is {st!r}")
    raise DraftError(
        f"the {season} draft is not finished ({where}); finish the draft first")


def on_the_clock(engine: Engine, season: int) -> dict | None:
    """The pick currently on the clock -- {overall, round, team, team_name, ...} --
    or None when the room is not open. The API reads this to decide WHOSE
    authorization to demand before it calls make_pick; make_pick re-checks the same
    thing under the lock, so this read cannot be raced into a pick out of turn."""
    with engine.connect() as conn:
        st = _state(conn, season)
        if not st or st["status"] != STATUS_OPEN or st["current_overall"] is None:
            return None
        return _pick_on_the_clock(conn, season, int(st["current_overall"]))


def _pick_on_the_clock(conn, season: int, overall: int, *, for_update: bool = False) -> dict:
    row = conn.execute(
        text("select dp.id::text as id, dp.round, dp.pick_number, dp.used, "
             "dp.holder_team_id::text as holder_team_id, "
             "ht.slug as team, ht.name as team_name, "
             "ot.slug as original, ot.name as original_name "
             "from draft_picks dp "
             "join teams ht on ht.id = dp.holder_team_id "
             "join teams ot on ot.id = dp.original_team_id "
             "where dp.season = :s and dp.pick_number = :n"
             + (" for update of dp" if for_update else "")),
        {"s": season, "n": overall},
    ).mappings().first()
    if row is None:
        raise DraftError(f"there is no pick {overall} in the {season} draft")
    return dict(row)


# ---------------------------------------------------------------------------
# The lottery.
# ---------------------------------------------------------------------------
def run_lottery(engine: Engine, season: int, *, seed: int | None = None,
                weights=LOTTERY_WEIGHTS) -> dict:
    """Draw every lottery slot and assign round 1's first M pick numbers, then
    resolve the protections the draw just decided. One transaction: a draw that
    numbered the picks but left a protection unresolved is not a state anyone can
    reason about.

    The seed is stored with the result. A draw nobody can replay is a draw nobody
    has to believe, and the one thing a commissioner will be asked about a lottery
    is whether it was the draw that actually happened. An explicit `seed` re-draws
    the same order; without one a seed is generated and recorded.

    Refused once the room is open or the draft is complete -- redrawing the order of
    a draft that is under way would silently reassign picks already made."""
    with engine.begin() as conn:
        st = _require_state(conn, season, for_update=True)
        if st["status"] in (STATUS_OPEN, STATUS_COMPLETE):
            raise DraftError(
                f"the {season} draft is already {st['status']}; the lottery cannot be "
                f"redrawn once the room has opened")

        lot = conn.execute(
            text("select standings_order, results from draft_lotteries "
                 "where season = :s for update"),
            {"s": season},
        ).mappings().first()
        if lot is None:
            raise DraftError(
                f"no lottery pool was recorded for {season}; it is written by the "
                f"season rollover")
        pool = [str(t) for t in (lot["standings_order"] or [])]
        if not pool:
            raise DraftError(f"the {season} lottery pool is empty; nothing to draw")

        if seed is None:
            seed = random.SystemRandom().randrange(1, 2**31 - 1)
        drawn = draw_lottery(pool, random.Random(seed), weights)

        results = [{"team_id": tid, "slot": i + 1} for i, tid in enumerate(drawn)]
        for entry in results:
            conn.execute(
                text("update draft_picks set pick_number = :n "
                     "where season = :s and round = 1 "
                     "and original_team_id = cast(:t as uuid)"),
                {"n": entry["slot"], "s": season, "t": entry["team_id"]},
            )
        conn.execute(
            text("update draft_lotteries set results = cast(:r as jsonb), seed = :seed, "
                 "drawn_at = now() where season = :s"),
            {"r": json.dumps(results), "seed": int(seed), "s": season},
        )

        protections = _resolve_protections(conn, season)

        conn.execute(
            text("update draft_state set status = 'lottery_drawn', updated_at = now() "
                 "where season = :s"),
            {"s": season},
        )
        named = _name_lottery(conn, results)
    return {"season": season, "seed": int(seed), "results": named,
            "protections": protections}


def _resolve_protections(conn, season: int) -> list[dict]:
    """Settle every unresolved round-1 protection now that the picks have numbers.

    Only a pick that actually MOVED can have a condition to settle, and only round 1
    can carry one (the rules layer enforces that at trade time). A caught pick goes
    home and the obligation ends -- no rollover to a later year."""
    rows = conn.execute(
        text("select dp.id::text as id, dp.pick_number, dp.protection_top_n, "
             "dp.holder_team_id::text as holder_team_id, "
             "dp.original_team_id::text as original_team_id, "
             "ht.name as holder_name, ot.name as original_name "
             "from draft_picks dp "
             "join teams ht on ht.id = dp.holder_team_id "
             "join teams ot on ot.id = dp.original_team_id "
             "where dp.season = :s and dp.round = 1 "
             "  and dp.protection_top_n is not null "
             "  and dp.protection_outcome is null "
             "  and dp.pick_number is not null "
             "  and dp.holder_team_id is distinct from dp.original_team_id "
             "order by dp.pick_number for update of dp"),
        {"s": season},
    ).mappings().all()

    out = []
    for r in rows:
        holder, outcome = resolve_protection(
            int(r["pick_number"]), int(r["protection_top_n"]),
            r["holder_team_id"], r["original_team_id"],
        )
        conn.execute(
            text("update draft_picks set holder_team_id = cast(:h as uuid), "
                 "protection_outcome = :o where id = cast(:id as uuid)"),
            {"h": holder, "o": outcome, "id": r["id"]},
        )
        out.append({
            "pick_number": int(r["pick_number"]),
            "protection_top_n": int(r["protection_top_n"]),
            "outcome": outcome,
            "original_team": r["original_name"],
            "holder_team": r["holder_name"],
        })
    return out


def _name_lottery(conn, results: list[dict]) -> list[dict]:
    if not results:
        return []
    names = {str(tid): (slug, name) for tid, slug, name in conn.execute(
        text("select id::text, slug, name from teams where id = any(cast(:ids as uuid[]))"),
        {"ids": [r["team_id"] for r in results]},
    ).all()}
    return [{**r,
             "team": names.get(r["team_id"], ("?", "?"))[0],
             "team_name": names.get(r["team_id"], ("?", "?"))[1]}
            for r in results]


def lottery(engine: Engine, season: int) -> dict | None:
    """The recorded draw for `season`, named, or None if there is no lottery row."""
    with engine.connect() as conn:
        row = conn.execute(
            text("select standings_order, results, seed, drawn_at "
                 "from draft_lotteries where season = :s"),
            {"s": season},
        ).mappings().first()
        if row is None:
            return None
        results = [dict(r) for r in (row["results"] or [])]
        return {"season": season, "seed": row["seed"], "drawn_at": row["drawn_at"],
                "pool_size": len(row["standings_order"] or []),
                "results": _name_lottery(conn, results)}


# ---------------------------------------------------------------------------
# The prospect class.
# ---------------------------------------------------------------------------
def parse_prospects(content: str, filename: str | None = None) -> list[tuple[str, str | None]]:
    """(name, position|None) pairs from an uploaded names file.

    Deliberately routed through draft_simulator.load_prospect_names rather than
    re-parsed here, so the file the commissioner uploads is byte-for-byte the file
    the offline draft class generator has always read -- one format, one parser, one
    place to fix. That function takes a PATH, so the upload is written to a temp
    file with the right suffix (the suffix is what selects CSV vs one-name-per-line)
    and removed again."""
    from handball.draft_simulator import load_prospect_names

    suffix = ".csv" if (filename or "").lower().endswith(".csv") else ".txt"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="prospects-")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(content)
        return load_prospect_names(path)
    finally:
        os.unlink(path)


def upload_prospects(engine: Engine, season: int, content: str,
                     *, filename: str | None = None) -> dict:
    """Put a draft class on the board for `season`, replacing whatever was there.

    Each prospect is GENERATED ONCE, here, and the whole generated player is stored
    in draft_prospects.player_json; the visible ratings are lifted into columns so
    the board can sort on them. Generating at pick time instead would mean the board
    showed ratings the signed player did not have -- managers would be drafting a
    name, not a player.

    Refused once the room is open: replacing the board mid-draft would change what
    is left on it, and the picks already made were made against the old one."""
    from handball.draft_simulator import assign_random_position, create_draft_player

    pairs = parse_prospects(content, filename)
    if not pairs:
        raise DraftError("that file has no names in it")

    with engine.begin() as conn:
        st = _require_state(conn, season, for_update=True)
        if st["status"] in (STATUS_OPEN, STATUS_COMPLETE):
            raise DraftError(
                f"the {season} draft is already {st['status']}; the prospect class "
                f"cannot be replaced once the room has opened")

        conn.execute(text("delete from draft_prospects where season = :s"), {"s": season})
        rows = []
        for ord_, (name, position) in enumerate(pairs, start=1):
            position = position or assign_random_position()
            try:
                player = create_draft_player(
                    name, position, id=f"draft-{season}-{ord_}-{_slug(name)}")
            except ValueError as e:               # an unknown Position column value
                raise DraftError(f"row {ord_} ({name}): {e}") from e
            rows.append({
                "s": season, "ord": ord_, "name": player.name, "pos": player.position,
                "age": player.age, "off": player.offense, "def": player.defense,
                "gk": player.goalie_skill, "json": json.dumps(asdict(player)),
            })
        conn.execute(
            text("insert into draft_prospects "
                 "(season, ord, name, position, age, offense, defense, goalie_skill, "
                 " player_json) values (:s, :ord, :name, cast(:pos as player_position), "
                 ":age, :off, :def, :gk, cast(:json as jsonb))"),
            rows,
        )
        needed = _total_picks(conn, season)
    return {"season": season, "prospects": len(rows), "picks": needed,
            "enough": len(rows) >= needed}


def prospects(engine: Engine, season: int, *, available_only: bool = True) -> list[dict]:
    """The board: every prospect with the ratings managers can see, best first."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("select dp.id::text as id, dp.ord, dp.name, dp.position::text as position, "
                 "dp.age, dp.offense, dp.defense, dp.goalie_skill, "
                 "p.legacy_id as player_id "
                 "from draft_prospects dp left join players p on p.id = dp.player_id "
                 "where dp.season = :s"
                 + (" and dp.player_id is null" if available_only else "")
                 + " order by dp.ord"),
            {"s": season},
        ).mappings().all()
    out = [{**dict(r), "rating": round(prospect_rating(r), 2)} for r in rows]
    out.sort(key=lambda p: (-p["rating"], p["ord"]))
    return out


# ---------------------------------------------------------------------------
# The room.
# ---------------------------------------------------------------------------
def open_draft(engine: Engine, season: int) -> dict:
    """Open the room and start the clock on pick 1. Needs a drawn lottery (so every
    pick has a number) and a board with at least one prospect per pick -- a draft
    that runs out of players mid-round is not a draft, it is a bug the commissioner
    would have to unwind by hand."""
    with engine.begin() as conn:
        st = _require_state(conn, season, for_update=True)
        if st["status"] == STATUS_OPEN:
            raise DraftError(f"the {season} draft is already open")
        if st["status"] == STATUS_COMPLETE:
            raise DraftError(f"the {season} draft is already complete")
        if st["status"] != STATUS_LOTTERY_DRAWN:
            raise DraftError(
                f"draw the {season} lottery before opening the room")

        picks = _total_picks(conn, season)
        if not picks:
            raise DraftError(f"there are no {season} draft picks to make")
        unnumbered = conn.execute(
            text("select count(*) from draft_picks where season = :s and pick_number is null"),
            {"s": season},
        ).scalar_one()
        if unnumbered:
            raise DraftError(
                f"{unnumbered} {season} pick(s) have no number; the order is incomplete")
        board = conn.execute(
            text("select count(*) from draft_prospects where season = :s and player_id is null"),
            {"s": season},
        ).scalar_one()
        if board < picks:
            raise DraftError(
                f"the board has {board} prospect(s) for {picks} picks; upload at least "
                f"{picks} before opening the draft")

        conn.execute(
            text("update draft_state set status = 'open', current_overall = 1, "
                 "turn_started_at = now(), updated_at = now() where season = :s"),
            {"s": season},
        )
        clock = _pick_on_the_clock(conn, season, 1)
    return {"season": season, "status": STATUS_OPEN, "picks": picks,
            "on_the_clock": clock}


def make_pick(
    engine: Engine,
    season: int,
    prospect_id: str | None = None,
    *,
    team_slug: str | None = None,
    auto: bool = False,
    rules: RosterRules = DEFAULT_RULES,
) -> dict:
    """Make the pick that is on the clock, in ONE transaction.

    `prospect_id` names the player; None means "whatever the rules call the best
    available", which is what the clock does and what a commissioner forcing a pick
    for an absent manager usually wants. `team_slug`, when given, must be the team
    actually on the clock -- the API has already checked that the caller owns it,
    and re-checking it HERE, under the lock, is what makes that check meaningful:
    the turn can move between the authorization read and this write.

    The draft_state row is taken FOR UPDATE first, so every pick in the league
    serializes through one row. Then team, then player -- the league's existing lock
    order, rejoined."""
    with engine.begin() as conn:
        st = _require_state(conn, season, for_update=True)
        if st["status"] != STATUS_OPEN:
            raise DraftError(
                f"the {season} draft is not open (it is {st['status']!r})")
        overall = int(st["current_overall"])
        pick = _pick_on_the_clock(conn, season, overall, for_update=True)
        if pick["used"]:                       # defensive: the cursor and the row disagree
            raise DraftError(f"pick {overall} has already been made")
        if team_slug is not None and team_slug != pick["team"]:
            raise DraftError(
                f"{pick['team_name']} is on the clock at pick {overall}, not {team_slug}")

        prospect = _take_prospect(conn, season, prospect_id)
        years, salary = rookie_deal(overall)

        lock_team(conn, pick["holder_team_id"])
        player = _player_from_dict(dict(prospect["player_json"]))
        player.update_contract(years, salary, rookie=True)
        player_uuid = _insert_player(conn, player, team_id=pick["holder_team_id"],
                                     restricted=True)
        placed = try_rebuild_layout(conn, pick["holder_team_id"], rules)

        conn.execute(
            text("update draft_prospects set player_id = cast(:p as uuid) "
                 "where id = cast(:id as uuid)"),
            {"p": player_uuid, "id": prospect["id"]},
        )
        conn.execute(
            text("update draft_picks set used = true, player_id = cast(:p as uuid), "
                 "picked_at = now(), auto_pick = :auto where id = cast(:id as uuid)"),
            {"p": player_uuid, "auto": bool(auto), "id": pick["id"]},
        )

        total = _total_picks(conn, season)
        complete = overall >= total
        undrafted = 0
        if complete:
            conn.execute(
                text("update draft_state set status = 'complete', current_overall = null, "
                     "turn_started_at = null, updated_at = now() where season = :s"),
                {"s": season},
            )
            undrafted = _release_undrafted(conn, season)
            next_clock = None
        else:
            conn.execute(
                text("update draft_state set current_overall = :n, turn_started_at = now(), "
                     "updated_at = now() where season = :s"),
                {"n": overall + 1, "s": season},
            )
            next_clock = _pick_on_the_clock(conn, season, overall + 1)

        teams = max(1, total // _rounds(conn, season))
        _, in_round = pick_coordinates(overall, teams)

    return {
        "season": season, "overall": overall, "round": int(pick["round"]),
        "pick_in_round": in_round,
        "team": pick["team"], "team_name": pick["team_name"],
        "original_team": pick["original"],
        "player_id": player.id, "player_name": player.name,
        "position": player.position, "term": years, "value": salary,
        "auto_pick": bool(auto), "placed": placed,
        "complete": complete, "undrafted_released": undrafted,
        "on_the_clock": next_clock,
    }


def _rounds(conn, season: int) -> int:
    return max(1, int(conn.execute(
        text("select coalesce(max(round), 1) from draft_picks where season = :s"),
        {"s": season},
    ).scalar_one()))


def _take_prospect(conn, season: int, prospect_id: str | None) -> dict:
    """Lock and return the prospect being drafted. With no id, the rules pick the
    best available -- chosen HERE, inside the lock, so the clock and a manager racing
    it cannot both be handed the same player."""
    if prospect_id is None:
        rows = conn.execute(
            text("select id::text as id, ord, offense, defense, goalie_skill "
                 "from draft_prospects where season = :s and player_id is null"),
            {"s": season},
        ).mappings().all()
        if not rows:
            raise DraftError("the board is empty; there is nobody left to draft")
        prospect_id = best_available([dict(r) for r in rows])["id"]

    row = conn.execute(
        text("select id::text as id, season, ord, name, player_json, player_id "
             "from draft_prospects where id = cast(:id as uuid) for update"),
        {"id": prospect_id},
    ).mappings().first()
    if row is None:
        raise DraftError(f"no prospect {prospect_id!r} is on the board")
    if int(row["season"]) != season:
        raise DraftError(f"{row['name']} is not in the {season} draft class")
    if row["player_id"] is not None:
        raise DraftError(f"{row['name']} has already been drafted")
    return dict(row)


def _insert_player(conn, player, *, team_id: str | None, restricted: bool) -> str:
    """Write a generated Player into `players` and return its uuid.

    Not PostgresTeamRepository.save, which is team-shaped and would need a whole
    legal Team to write one row; this is the same column list and the same casts,
    for the one case the repository has no verb for -- a player who did not exist a
    moment ago. They land UNPLACED (no slot), exactly as a pool signing into a full
    roster does; try_rebuild_layout is the caller's next move."""
    cols = ["legacy_id", "team_id", *PLAYER_SCALAR_COLS, "retired", "updated_at"]
    params = {
        "legacy_id": player.id,
        "team_id": team_id,
        "retired": False,
        **{c: getattr(player, c) for c in PLAYER_SCALAR_COLS},
    }
    params["restricted_free_agent"] = restricted

    def placeholder(c: str) -> str:
        if c == "updated_at":
            return "now()"
        if c == "team_id":
            return "cast(:team_id as uuid)"
        if c == "position":
            return "cast(:position as player_position)"
        return f":{c}"

    sql = (f"insert into players ({', '.join(cols)}) "
           f"values ({', '.join(placeholder(c) for c in cols)}) returning id")
    return str(conn.execute(text(sql), params).scalar_one())


def _release_undrafted(conn, season: int) -> int:
    """Every prospect still on the board becomes an ordinary unrestricted free agent
    when the draft closes. They are real players who were in the class and nobody
    took -- the alternative is that they evaporate, which is both unkind and makes
    the pool depend on how many picks got traded. No contract and no rights: nobody
    ever held them, so there is no Bird-rights team and nothing to expire."""
    rows = conn.execute(
        text("select id::text as id, player_json from draft_prospects "
             "where season = :s and player_id is null order by ord"),
        {"s": season},
    ).mappings().all()
    for r in rows:
        player = _player_from_dict(dict(r["player_json"]))
        player_uuid = _insert_player(conn, player, team_id=None, restricted=False)
        conn.execute(
            text("update draft_prospects set player_id = cast(:p as uuid) "
                 "where id = cast(:id as uuid)"),
            {"p": player_uuid, "id": r["id"]},
        )
    return len(rows)


# ---------------------------------------------------------------------------
# The turn clock.
# ---------------------------------------------------------------------------
# Same lazy clock as free agency, and for the same reason: there is no scheduler in
# this deployment, so the clock advances whenever anybody reads the state, which the
# draft room polls. A draft is strictly ordered -- one manager who walks away halts
# all 64 picks -- so the clock is what makes it finish without the commissioner
# chasing anybody. What expiry does is exactly what a commissioner forcing a pick
# would do, and the pick is flagged `auto_pick` so the record says which of the two
# it was: only one of them is a person.
def sweep_expired_turns(engine: Engine, season: int, *,
                        limit_hours: int = DRAFT_TURN_LIMIT_HOURS,
                        rules: RosterRules = DEFAULT_RULES) -> list[dict]:
    """Auto-pick for the turn on the clock if it has run out. Returns what was done
    (a list, so the caller's shape does not change if this ever sweeps more than one).

    AT MOST ONE PICK PER READ, deliberately: an auto-pick starts the next team's
    clock at now(), so the team behind an absentee gets their full turn rather than
    inheriting somebody else's lateness. A draft abandoned over a weekend therefore
    catches up one pick per read rather than all at once -- which is the right answer
    anyway, since the point of the clock is to keep the room moving for the people
    who are in it, not to run the draft without them.

    Races are expected and ignored: two managers polling at once both try, and the
    one that loses the draft_state lock finds the turn already moved on."""
    with engine.connect() as conn:
        expired = conn.execute(
            text("select turn_started_at is not null and turn_started_at < "
                 "  now() - make_interval(hours => :h) "
                 "from draft_state where season = :s and status = 'open'"),
            {"h": limit_hours, "s": season},
        ).scalar()
    if not expired:
        return []
    try:
        result = make_pick(engine, season, None, auto=True, rules=rules)
    except (DraftError, DraftRulesError):
        return []                    # already moved on, or nothing left to pick
    return [{"overall": result["overall"], "team": result["team"],
             "team_name": result["team_name"],
             "player_name": result["player_name"]}]


# ---------------------------------------------------------------------------
# The read the room polls.
# ---------------------------------------------------------------------------
def draft_state(engine: Engine, season: int, *,
                turn_limit_hours: int = DRAFT_TURN_LIMIT_HOURS,
                rules: RosterRules = DEFAULT_RULES) -> dict:
    """The one document the draft room renders: the order, the picks made, what is
    left on the board, whose turn it is and how long they have left.

    Sweeps the clock BEFORE it reads, so the poll that renders the room is also the
    thing that keeps it moving. `swept` reports what the read itself did, so the page
    can say so rather than silently showing a pick nobody made."""
    swept = sweep_expired_turns(engine, season, limit_hours=turn_limit_hours, rules=rules)

    with engine.connect() as conn:
        st = _state(conn, season)
        if st is None:
            return {"season": season, "status": None, "order": [], "board": [],
                    "lottery": None, "swept": swept}

        order = conn.execute(
            text("select dp.id::text as id, dp.round, dp.pick_number as overall, "
                 "dp.used, dp.auto_pick, dp.picked_at, "
                 "dp.protection_top_n, dp.protection_outcome, "
                 "ht.slug as team, ht.name as team_name, "
                 "ot.slug as original, ot.name as original_name, "
                 "p.legacy_id as player_id, p.name as player_name, "
                 "p.position::text as player_position, "
                 "p.contract_term as term, p.contract_value as value "
                 "from draft_picks dp "
                 "join teams ht on ht.id = dp.holder_team_id "
                 "join teams ot on ot.id = dp.original_team_id "
                 "left join players p on p.id = dp.player_id "
                 "where dp.season = :s "
                 "order by dp.pick_number nulls last, dp.round"),
            {"s": season},
        ).mappings().all()

        clock = None
        if st["status"] == STATUS_OPEN and st["current_overall"] is not None:
            clock = conn.execute(
                text("select dp.pick_number as overall, dp.round, "
                     "ht.slug as team, ht.name as team_name, "
                     "ot.name as original_name, ds.turn_started_at, "
                     "ds.turn_started_at + make_interval(hours => :h) as turn_deadline, "
                     "floor(extract(epoch from (ds.turn_started_at "
                     "  + make_interval(hours => :h) - now())))::bigint as turn_seconds_left "
                     "from draft_state ds "
                     "join draft_picks dp on dp.season = ds.season "
                     "  and dp.pick_number = ds.current_overall "
                     "join teams ht on ht.id = dp.holder_team_id "
                     "join teams ot on ot.id = dp.original_team_id "
                     "where ds.season = :s"),
                {"s": season, "h": turn_limit_hours},
            ).mappings().first()

        counts = conn.execute(
            text("select count(*) as picks, "
                 "count(*) filter (where used) as made, "
                 "count(*) filter (where auto_pick) as auto "
                 "from draft_picks where season = :s"),
            {"s": season},
        ).mappings().one()

    return {
        "season": season,
        "status": st["status"],
        "current_overall": st["current_overall"],
        "picks": int(counts["picks"]),
        "picks_made": int(counts["made"]),
        "auto_picks": int(counts["auto"]),
        "on_the_clock": dict(clock) if clock else None,
        "turn_limit_hours": turn_limit_hours,
        "order": [dict(r) for r in order],
        "board": prospects(engine, season),
        "lottery": lottery(engine, season),
        "swept": swept,
    }
