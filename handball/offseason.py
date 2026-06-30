"""
Name: offseason.py
Description: The season-end rollover ("advance season") -- the mechanical transition
    from season N to N+1, over the Postgres engine (mirrors schedule_repository.py).
    The postseason/playoffs are NOT here; this is only the offseason: awards,
    draft-order seeding, player aging, free agency, record reset.

    advance_season() runs the whole rollover in ONE transaction, so a failure rolls
    everything back and a retry is clean. That atomicity matters because aging is the
    one non-idempotent step (each run ages a player a year); all-or-nothing avoids
    double-aging on a partial failure. The job does no game simulation, so it's fast
    enough to run synchronously inside the request.

    Player progression reuses the tested domain logic: each player row is rebuilt
    into a domain.Player (via repository._player_from_dict), advance_year()'d, and its
    scalar columns written back. That player-level path (rather than the team-centric
    PostgresTeamRepository.save) is what lets FREE AGENTS -- players with no team --
    age too.

    Ordering matters: awards + draft seeding read the finished season's stats/standings
    and run before records are reset.
Author: offseason rollover
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.pg_repository import PLAYER_SCALAR_COLS
from handball.repository import _player_from_dict
from handball.simulation_vars import DRAFT_ROUNDS, RETIREMENT_CANDIDATE_AGE

# Award labels (plain text, written straight to awards.award; the frontend reads
# (season, award) directly -- see PlayerDetail.tsx).
AWARD_MVP = "League MVP"
AWARD_TOP_SCORER = "Top Scorer"
AWARD_TOP_GOALIE = "Top Goalie"
AWARD_ROOKIE = "Rookie of the Year"


# -- retirement (commissioner-curated; separate from the atomic rollover) ----
def retirement_candidates(engine: Engine) -> list[dict]:
    """Active players older than RETIREMENT_CANDIDATE_AGE -- the pool the
    commissioner chooses retirees from. Free agents (no team) are included."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select p.legacy_id, p.name, p.age, p.position, "
                "t.slug as team_slug, t.name as team_name "
                "from players p left join teams t on t.id = p.team_id "
                "where p.retired = false and p.age > :age "
                "order by p.age desc, p.name"
            ),
            {"age": RETIREMENT_CANDIDATE_AGE},
        ).mappings().all()
    return [dict(r) for r in rows]


def retire_players(engine: Engine, legacy_ids: list[str], season: int) -> int:
    """Flag the given players retired and remove them from rosters. The row is kept
    (career stats + FKs survive); only roster membership/slots are cleared. Returns
    how many players were newly retired."""
    if not legacy_ids:
        return 0
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "update players set retired = true, retired_season = :s, "
                "team_id = null, slot_group = null, slot_position = null, "
                "slot_order = null, updated_at = now() "
                "where legacy_id = any(:ids) and retired = false returning legacy_id"
            ),
            {"s": season, "ids": list(legacy_ids)},
        ).all()
    return len(rows)


# -- the rollover (atomic) ---------------------------------------------------
def advance_season(engine: Engine, season: int, ranked_team_ids: list[str]) -> dict:
    """Roll the league from `season` to `season + 1` in a single transaction:
    assign awards, seed next season's draft order (from the pre-reset standings in
    `ranked_team_ids`, best->worst), age every non-retired player, move expired
    contracts to free agency, zero team records, and open the new season. Returns a
    summary of counts."""
    new_season = season + 1
    with engine.begin() as conn:
        awards = _compute_awards(conn, season)
        picks = _seed_draft_order(conn, ranked_team_ids, new_season)
        aged = _age_all_players(conn)
        freed = _process_free_agency(conn)
        teams_reset = _reset_team_records(conn)
        conn.execute(
            text("insert into season_state (season, injury_seed) values (:s, :seed) "
                 "on conflict (season) do nothing"),
            {"s": new_season, "seed": new_season},
        )
    return {
        "new_season": new_season,
        "awards": awards,
        "draft_picks": picks,
        "players_aged": aged,
        "new_free_agents": freed,
        "teams_reset": teams_reset,
    }


# -- awards ------------------------------------------------------------------
def _compute_awards(conn, season: int) -> dict[str, str | None]:
    """Assign the four season awards from the finished season's stat lines. Idempotent
    within the season (clears it first). Returns {award_label: legacy_id|None}."""
    conn.execute(text("delete from awards where season = :s"), {"s": season})
    winners = {
        AWARD_MVP: _top_player(
            conn, season,
            "group by pgl.player_id order by sum(pgl.performance) desc nulls last"),
        AWARD_TOP_SCORER: _top_player(
            conn, season, "group by pgl.player_id order by sum(pgl.goals) desc"),
        AWARD_TOP_GOALIE: _top_player(
            conn, season,
            "and p.position = 'Goalie' group by pgl.player_id order by sum(pgl.saves) desc"),
        AWARD_ROOKIE: _top_player(
            conn, season,
            "and p.years_in_league = 0 "
            "group by pgl.player_id order by sum(pgl.performance) desc nulls last"),
    }
    assigned: dict[str, str | None] = {}
    for label, puid in winners.items():
        if puid is None:
            assigned[label] = None
            continue
        legacy = conn.execute(
            text("insert into awards (player_id, season, award) values (:p, :s, :a) "
                 "returning (select legacy_id from players where id = :p)"),
            {"p": puid, "s": season, "a": label},
        ).scalar_one()
        assigned[label] = legacy
    return assigned


def _top_player(conn, season: int, tail_sql: str):
    """The single players.id leading a season-scoped aggregate, or None when there
    are no qualifying lines. `tail_sql` supplies any extra WHERE + the group/order."""
    row = conn.execute(
        text("select pgl.player_id from player_game_lines pgl "
             "join players p on p.id = pgl.player_id "
             "where pgl.season = :s " + tail_sql + " limit 1"),
        {"s": season},
    ).first()
    return row[0] if row else None


# -- draft-pick-order seeding ------------------------------------------------
def _seed_draft_order(conn, ranked_team_ids: list[str], new_season: int) -> int:
    """Seed next season's draft pick order. `ranked_team_ids` is best->worst; the
    draft runs worst->best, so reverse it. Each round repeats that order; pick_number
    is the overall (1..teams*rounds) order. Picks start untraded (holder==original).
    Idempotent for the season. Returns picks seeded."""
    order = list(reversed(ranked_team_ids))  # worst picks first
    conn.execute(text("delete from draft_picks where season = :s"), {"s": new_season})
    slug_to_id = {slug: tid for slug, tid in conn.execute(text("select slug, id from teams")).all()}
    rows, overall = [], 0
    for rnd in range(1, DRAFT_ROUNDS + 1):
        for slug in order:
            tid = slug_to_id.get(slug)
            if tid is None:
                continue
            overall += 1
            rows.append({"s": new_season, "r": rnd, "tid": str(tid), "n": overall})
    if rows:
        conn.execute(
            text("insert into draft_picks "
                 "(season, round, original_team_id, holder_team_id, pick_number) "
                 "values (:s, :r, cast(:tid as uuid), cast(:tid as uuid), :n)"),
            rows,
        )
    return len(rows)


# -- player aging ------------------------------------------------------------
# Columns advance_year() mutates and we persist back (the rest are unchanged by a
# normal year; injury-impact changes happen during the season, not here).
_AGED_COLS = ("offense", "defense", "goalie_skill", "age", "years_in_league", "years_remaining")


def _age_all_players(conn) -> int:
    """Roll every non-retired player (teamed AND free agent) one year forward via
    domain.Player.advance_year() -- growth/plateau/decline by age, +1 age/tenure,
    contract tick-down. Returns players aged."""
    cols = ", ".join(PLAYER_SCALAR_COLS)
    rows = conn.execute(
        text(f"select id, legacy_id, {cols} from players where retired = false")
    ).mappings().all()
    updates = []
    for r in rows:
        player = _player_from_dict({"id": r["legacy_id"], **{c: r[c] for c in PLAYER_SCALAR_COLS}})
        player.advance_year()
        updates.append({"uuid": str(r["id"]), **{c: getattr(player, c) for c in _AGED_COLS}})
    if updates:
        set_sql = ", ".join(f"{c} = :{c}" for c in _AGED_COLS)
        conn.execute(
            text(f"update players set {set_sql}, updated_at = now() "
                 "where id = cast(:uuid as uuid)"),
            updates,
        )
    return len(updates)


# -- free agency (pool only) -------------------------------------------------
def _process_free_agency(conn) -> int:
    """Players whose contracts have run out (years_remaining <= 0) leave their team
    and become free agents (team_id + slots cleared). Run AFTER aging, which ticks
    years_remaining down. Returns players moved to the pool."""
    rows = conn.execute(
        text("update players set team_id = null, slot_group = null, "
             "slot_position = null, slot_order = null, updated_at = now() "
             "where retired = false and team_id is not null and years_remaining <= 0 "
             "returning legacy_id")
    ).all()
    return len(rows)


# -- records -----------------------------------------------------------------
def _reset_team_records(conn) -> int:
    """Zero every team's W-L-T for the new season (Standings reads these directly).
    Games are retained for history. Returns teams reset."""
    rows = conn.execute(text("update teams set wins = 0, losses = 0, ties = 0 returning id")).all()
    return len(rows)
