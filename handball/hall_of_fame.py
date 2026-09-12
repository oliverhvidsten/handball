"""
Name: hall_of_fame.py
Description: The Hall of Fame -- a commissioner's curated list of retired players,
    over alembic 0017's `hall_of_fame` table. The table stores only the two facts
    that can't be recomputed (the class year and the citation); the career line
    underneath is aggregated from `player_game_lines` at READ time, split into
    regular-season and playoff lines the same way `offseason._top_player` and
    `player_season_stats` already split them -- a Hall entry that mixed the two
    would credit a career with games it played in a different competition.

    `induct` requires the player to already be RETIRED (handball/offseason.py's
    `retire_players`): the Hall is a look back at a finished career, not a mid-career
    honor -- that's what `awards` is for. Rescinding is a hard DELETE (see 0017's
    docstring for why there is no 'rescinded' status).

    `eligible_retirees` backs the commissioner's induction picker. `player_public`
    (what the rest of the frontend reads retired players through) doesn't carry
    `retired_season`, so "this season's retirees first" has to be answered from
    here rather than a client-side Supabase query -- the one place this module
    reaches past the Hall table itself, into `players`.
Author: hof agent, phase 1
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

# The zero career line a player with no game log (a rare but legal induction --
# nothing stops a commissioner honoring a player who never played a game for this
# franchise's records) gets instead of a missing key.
_EMPTY_LINE = {"games": 0, "goals": 0, "shots": 0, "saves": 0, "goals_allowed": 0, "performance": 0.0}


class HallOfFameError(RuntimeError):
    pass


def induct(engine: Engine, legacy_id: str, season: int, citation: str | None = None) -> dict:
    """Induct a retired player into the class of `season`. Raises HallOfFameError if
    the player doesn't exist, isn't retired, or is already inducted -- every one of
    these is a 409 at the API (see api/hall_of_fame.py), not a silent no-op."""
    with engine.begin() as conn:
        player = conn.execute(
            text("select id, name, retired from players where legacy_id = :l"),
            {"l": legacy_id},
        ).mappings().first()
        if player is None:
            raise HallOfFameError(f"no player {legacy_id!r}")
        if not player["retired"]:
            raise HallOfFameError(f"{player['name']} must be retired before induction")
        already = conn.execute(
            text("select 1 from hall_of_fame where player_id = :p"), {"p": player["id"]}
        ).first()
        if already:
            raise HallOfFameError(f"{player['name']} is already in the Hall of Fame")
        conn.execute(
            text("insert into hall_of_fame (player_id, inducted_season, citation) "
                 "values (:p, :s, :c)"),
            {"p": player["id"], "s": season, "c": citation},
        )
    return {"legacy_id": legacy_id, "name": player["name"], "inducted_season": season, "citation": citation}


def rescind(engine: Engine, legacy_id: str) -> None:
    """Remove a player from the Hall (a hard delete -- see 0017's docstring for why
    there is no 'rescinded' status). Raises HallOfFameError if they weren't in it."""
    with engine.begin() as conn:
        row = conn.execute(
            text("delete from hall_of_fame using players "
                 "where hall_of_fame.player_id = players.id and players.legacy_id = :l "
                 "returning hall_of_fame.id"),
            {"l": legacy_id},
        ).first()
        if row is None:
            raise HallOfFameError(f"{legacy_id!r} is not in the Hall of Fame")


def inductees(engine: Engine) -> list[dict]:
    """Every inductee, newest class first, with the career line split by
    regular-season and playoff `player_game_lines` -- aggregated fresh on every
    read so the Hall can never drift from the record books underneath it."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select h.inducted_season, h.citation, h.inducted_at, "
                "p.id as player_uuid, p.legacy_id, p.name, p.position "
                "from hall_of_fame h join players p on p.id = h.player_id "
                "order by h.inducted_season desc, p.name"
            )
        ).mappings().all()
        if not rows:
            return []
        ids = [r["player_uuid"] for r in rows]
        lines = conn.execute(
            text(
                "select player_id, is_playoff, count(*) as games, "
                "coalesce(sum(goals), 0) as goals, coalesce(sum(shots), 0) as shots, "
                "coalesce(sum(saves), 0) as saves, "
                "coalesce(sum(goals_allowed), 0) as goals_allowed, "
                "coalesce(sum(performance), 0) as performance "
                "from player_game_lines where player_id = any(:ids) "
                "group by player_id, is_playoff"
            ),
            {"ids": ids},
        ).mappings().all()

    career: dict[str, dict[str, dict]] = {}
    for line in lines:
        key = "playoff" if line["is_playoff"] else "regular_season"
        career.setdefault(str(line["player_id"]), {})[key] = {
            "games": line["games"],
            "goals": line["goals"],
            "shots": line["shots"],
            "saves": line["saves"],
            "goals_allowed": line["goals_allowed"],
            "performance": float(line["performance"]),
        }

    out = []
    for r in rows:
        by_kind = career.get(str(r["player_uuid"]), {})
        out.append({
            "legacy_id": r["legacy_id"],
            "name": r["name"],
            "position": r["position"],
            "inducted_season": r["inducted_season"],
            "citation": r["citation"],
            "inducted_at": r["inducted_at"].isoformat() if r["inducted_at"] else None,
            "regular_season": by_kind.get("regular_season", dict(_EMPTY_LINE)),
            "playoff": by_kind.get("playoff", dict(_EMPTY_LINE)),
        })
    return out


def eligible_retirees(engine: Engine, season: int, query: str | None = None) -> list[dict]:
    """Retired players not already in the Hall, for the commissioner's induction
    picker: this season's retirees (retired_season == season) sort first, then
    every other retiree, each group alphabetical. `query` (optional) narrows by a
    case-insensitive name search over the WHOLE retired pool -- the picker's other
    use, a search over every retiree regardless of when they retired."""
    sql = (
        "select legacy_id, name, position, age, retired_season "
        "from players p "
        "where retired = true "
        "and not exists (select 1 from hall_of_fame h where h.player_id = p.id) "
    )
    params: dict = {"season": season}
    if query:
        sql += "and name ilike :q "
        params["q"] = f"%{query}%"
    sql += "order by (retired_season = :season) desc, name"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]
