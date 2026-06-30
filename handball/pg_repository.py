"""
Name: pg_repository.py
Description: Postgres-backed TeamRepository -- the relational twin of
    JsonTeamRepository. Same Protocol, same domain objects; the on-disk shape is
    rows instead of one JSON blob per team.

    Mapping to the domain (see repository.team_to_dict / team_from_dict, reused
    here for Player/InjuryReport reconstruction):
      - domain TeamId      -> teams.slug          (stored verbatim, e.g. "Phoenix")
      - domain PlayerId    -> players.legacy_id   (e.g. "phoenix-evan-clarkson")
      - team.record (W,L,T)-> teams.wins/losses/ties
      - arrangement slots  -> players.slot_group / slot_position / slot_order
      - injury_log         -> injuries rows (active_injury is DERIVED: at most one
                              injury is "current" at a time, so it equals
                              any(is_current))
      - awards_won         -> awards rows (each element JSON-encoded, faithful for
                              the currently-empty/opaque award shape)
      - current_season_log -> NOT rehydrated. Per-game stats live in
                              player_game_lines (written by PostgresRecordSink) and
                              are read via the player_season_stats view; a loaded
                              Player starts with an empty in-progress log.

    save() persists the team's CURRENT roster. Moving a player between teams is a
    transactional concern owned by trade_service, not by save().
Author: relational backend
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from handball.db import get_engine
from handball.domain import Team
from handball.league_views import TeamId
from handball.repository import SCHEMA_VERSION, RepositoryError, team_from_dict

# Player attributes that map 1:1 to a players column (column name == attribute).
PLAYER_SCALAR_COLS = [
    "name", "position",
    "age", "years_in_league", "height", "weight",
    "offense", "defense", "goalie_skill",
    "max_offense", "max_defense", "max_goalie_skill",
    "variance", "peak_age", "decline_age", "decline_rate",
    "is_injured", "injury_risk",
    "contract_term", "contract_value", "years_remaining", "amount_paid",
    "rookie_contract", "restricted_free_agent",
]

# Columns whose parameter needs an explicit cast (enums / arrays / uuid).
_ENUM_COLS = {"position", "slot_position", "slot_group"}


class PostgresTeamRepository:
    """One Team == one teams row + its players rows (+ injuries/awards)."""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()

    # -- reads -------------------------------------------------------------
    def load(self, team_id: TeamId) -> Team:
        with self.engine.connect() as conn:
            return self._load(conn, team_id)

    def all_team_ids(self) -> list[TeamId]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("select slug from teams order by slug")).all()
        return [r[0] for r in rows]

    def load_all(self) -> list[Team]:
        with self.engine.connect() as conn:
            ids = [r[0] for r in conn.execute(text("select slug from teams order by slug")).all()]
            return [self._load(conn, tid) for tid in ids]

    def _load(self, conn, team_id: TeamId) -> Team:
        trow = conn.execute(
            text("select id, slug, name, coaches, wins, losses, ties from teams where slug = :s"),
            {"s": team_id},
        ).mappings().first()
        if trow is None:
            raise KeyError(f"no team {team_id!r} in database")

        prows = conn.execute(
            text(
                "select id, legacy_id, position, slot_group, slot_position, slot_order, "
                + ", ".join(PLAYER_SCALAR_COLS)
                + " from players where team_id = :tid "
                "order by slot_group, slot_position, slot_order"
            ),
            {"tid": trow["id"]},
        ).mappings().all()

        uuids = [p["id"] for p in prows]
        injuries = self._child_map(conn, "injuries",
                                   "year, injury_type, duration, games_remaining, is_current",
                                   uuids)
        # awards are league-assigned data (written by the offseason), NOT part of the
        # roster snapshot -- they're read by the frontend straight from the awards
        # table. Keeping them off the domain object means a roster save() can't wipe
        # them (see _replace_children).

        # Build the team_from_dict-shaped snapshot, reusing the existing
        # Player/InjuryReport reconstruction + validate-on-load.
        players_dict: dict = {}
        starters: dict[str, list[str]] = {}
        bench: dict[str, list[str]] = {}
        reserves: list[tuple[int, str]] = []
        for p in prows:
            pid = p["legacy_id"]
            inj_rows = injuries.get(p["id"], [])
            injuries_list = [
                [r["year"], r["injury_type"], r["duration"], r["games_remaining"], r["is_current"]]
                for r in inj_rows
            ]
            players_dict[pid] = {
                "id": pid,
                **{c: p[c] for c in PLAYER_SCALAR_COLS},
                "injury_log": {
                    "active_injury": any(r["is_current"] for r in inj_rows),
                    "injuries": injuries_list,
                },
                "awards_won": [],  # decoupled from the repo; see awards note above
                # current_season_log intentionally omitted -> Player default (empty)
            }
            group = p["slot_group"]
            if group == "starters":
                starters.setdefault(p["slot_position"], []).append(pid)
            elif group == "bench":
                bench.setdefault(p["slot_position"], []).append(pid)
            elif group == "reserves":
                reserves.append((p["slot_order"], pid))

        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "id": trow["slug"],
            "name": trow["name"],
            "coaches": list(trow["coaches"]),
            "record": [trow["wins"], trow["losses"], trow["ties"]],
            "arrangement": {
                "starters": starters,
                "bench": bench,
                "reserves": [pid for _, pid in sorted(reserves)],
            },
            "players": players_dict,
        }
        try:
            return team_from_dict(snapshot)
        except RepositoryError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            raise RepositoryError(f"team {team_id!r}: failed to assemble from rows: {e}") from e

    @staticmethod
    def _child_map(conn, table: str, cols: str, player_uuids: list) -> dict:
        """player uuid -> ordered child rows (injuries/awards)."""
        if not player_uuids:
            return {}
        rows = conn.execute(
            text(f"select player_id, {cols} from {table} "
                 "where player_id = any(:ids) order by player_id, ord"),
            {"ids": player_uuids},
        ).mappings().all()
        out: dict = {}
        for r in rows:
            out.setdefault(r["player_id"], []).append(r)
        return out

    # -- writes ------------------------------------------------------------
    def save(self, team: Team) -> None:
        with self.engine.begin() as conn:
            tid = conn.execute(
                text(
                    "insert into teams (slug, name, coaches, wins, losses, ties) "
                    "values (:slug, :name, :coaches, :w, :l, :t) "
                    "on conflict (slug) do update set "
                    "name = excluded.name, coaches = excluded.coaches, "
                    "wins = excluded.wins, losses = excluded.losses, ties = excluded.ties "
                    "returning id"
                ),
                {"slug": team.id, "name": team.name, "coaches": list(team.coaches),
                 "w": team.record[0], "l": team.record[1], "t": team.record[2]},
            ).scalar_one()

            # Clear this team's slots first so per-row slot upserts can't collide
            # on the (team_id, slot_group, slot_position, slot_order) unique index
            # while players are being reordered. NULL slots are mutually distinct.
            conn.execute(
                text("update players set slot_group = null, slot_position = null, "
                     "slot_order = null where team_id = :tid"),
                {"tid": tid},
            )

            for slot_group, slot_position, slot_order, player in _iter_slots(team):
                puid = self._upsert_player(conn, tid, slot_group, slot_position, slot_order, player)
                self._replace_children(conn, puid, player)

    def _upsert_player(self, conn, tid, slot_group, slot_position, slot_order, player) -> object:
        cols = ["legacy_id", "team_id", "slot_group", "slot_position", "slot_order",
                *PLAYER_SCALAR_COLS, "updated_at"]
        params = {
            "legacy_id": player.id,
            "team_id": tid,
            "slot_group": slot_group,
            "slot_position": slot_position,
            "slot_order": slot_order,
            **{c: getattr(player, c) for c in PLAYER_SCALAR_COLS},
        }

        def placeholder(c: str) -> str:
            if c == "updated_at":
                return "now()"
            if c == "team_id":
                return "cast(:team_id as uuid)"
            if c == "slot_group":
                return "cast(:slot_group as roster_group)"
            if c in ("position", "slot_position"):
                return f"cast(:{c} as player_position)"
            return f":{c}"

        insert_cols = ", ".join(cols)
        insert_vals = ", ".join(placeholder(c) for c in cols)
        update_set = ", ".join(
            f"{c} = excluded.{c}" for c in cols if c != "legacy_id"
        )
        sql = (
            f"insert into players ({insert_cols}) values ({insert_vals}) "
            f"on conflict (legacy_id) do update set {update_set} returning id"
        )
        return conn.execute(text(sql), params).scalar_one()

    @staticmethod
    def _replace_children(conn, puid, player) -> None:
        # Injuries belong to the roster snapshot and round-trip through save(); awards
        # do NOT -- they're league-assigned and managed by the offseason, so a routine
        # roster save() must not touch (and wipe) them.
        conn.execute(text("delete from injuries where player_id = :p"), {"p": puid})
        for ord_, rec in enumerate(player.injury_log.injuries):
            year, itype, duration, remaining, current = rec
            conn.execute(
                text("insert into injuries (player_id, year, injury_type, duration, "
                     "games_remaining, is_current, ord) "
                     "values (:p, :y, :it, :d, :gr, :cur, :o)"),
                {"p": puid, "y": year, "it": itype, "d": duration, "gr": remaining,
                 "cur": bool(current), "o": ord_},
            )


def _iter_slots(team: Team):
    """Yield (slot_group, slot_position, slot_order, player) for the whole roster.
    Reserves carry slot_position = the player's own position (truthful and keeps
    the all-or-nothing slot CHECK satisfied); their order is the reserves index."""
    for pos, plist in team.starters.items():
        for i, p in enumerate(plist):
            yield "starters", pos, i, p
    for pos, plist in team.bench.items():
        for i, p in enumerate(plist):
            yield "bench", pos, i, p
    for i, p in enumerate(team.reserves):
        yield "reserves", p.position, i, p
