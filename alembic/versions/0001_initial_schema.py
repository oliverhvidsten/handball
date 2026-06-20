"""initial fully-relational schema

Revision ID: 0001
Revises:
Create Date: 2026-06-18

Fully-relational model for the league: team/player state (with roster slots),
normalized injuries/awards (replacing the JSON logs), games + per-player-per-game
stat lines (replacing current_season_log), draft picks, managers, and trades. Two
read views express derived data: player_season_stats (leaderboards) and
player_public (the hidden/public boundary mirror of Player.public_view()).

Notes / deviations from the design sketch, justified for a plain Postgres dev DB:
  - games / player_game_lines are plain tables with a `season` column + index, not
    list-partitioned. Partitioning is an archival optimization, not a perf need at
    this scale, and it would force `season` into every PK/FK. The `season` column
    keeps a later partitioning migration non-breaking.
  - managers.user_id is a bare uuid here. On Supabase it maps to auth.users(id);
    that FK is added in the Supabase/auth migration (Phase 7), since auth.users
    does not exist in a vanilla Postgres.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA = r"""
create type player_position as enum ('Forward','Midfielder','Defense','Goalie');
create type roster_group  as enum ('starters','bench','reserves');
create type trade_status  as enum ('proposed','accepted','rejected','cancelled','approved','committed');

create table teams (
  id     uuid primary key default gen_random_uuid(),
  slug   text unique not null,
  name   text not null,
  coaches text[] not null default '{}',
  wins int not null default 0,
  losses int not null default 0,
  ties int not null default 0,
  created_at timestamptz not null default now()
);

create table players (
  id        uuid primary key default gen_random_uuid(),
  legacy_id text unique,
  team_id   uuid references teams(id) on delete set null,
  name      text not null,
  position  player_position not null,

  slot_group    roster_group,
  slot_position player_position,
  slot_order    int,

  age int, years_in_league int, height int, weight int,

  offense double precision, defense double precision, goalie_skill double precision,

  max_offense double precision, max_defense double precision, max_goalie_skill double precision,
  variance double precision, peak_age int, decline_age int, decline_rate double precision,
  injury_risk double precision, is_injured boolean not null default false,

  contract_term int, contract_value int, years_remaining int, amount_paid int,
  rookie_contract boolean, restricted_free_agent boolean,

  updated_at timestamptz not null default now(),

  unique (team_id, slot_group, slot_position, slot_order),
  check ((slot_group is null) = (slot_position is null)
     and (slot_group is null) = (slot_order is null))
);
create index ix_players_team on players (team_id);

create table injuries (
  id bigserial primary key,
  player_id uuid not null references players(id) on delete cascade,
  year int, injury_type text, duration int, games_remaining int, is_current boolean,
  ord int not null default 0
);
create index ix_injuries_player on injuries (player_id, ord);

create table awards (
  id bigserial primary key,
  player_id uuid not null references players(id) on delete cascade,
  season int, award text,
  ord int not null default 0
);
create index ix_awards_player on awards (player_id, ord);

create table games (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  week int,
  home_team_id uuid references teams(id),
  away_team_id uuid references teams(id),
  home_score int, away_score int,
  went_to_overtime boolean not null default false,
  scoring_log text,
  played_at timestamptz not null default now()
);
create index ix_games_season on games (season);

create table player_game_lines (
  game_id   uuid not null references games(id) on delete cascade,
  player_id uuid not null references players(id),
  team_id   uuid references teams(id),
  season    int not null,
  goals int not null default 0,
  shots int not null default 0,
  saves int not null default 0,
  goals_allowed int not null default 0,
  performance double precision,
  primary key (game_id, player_id)
);
create index ix_pgl_season_goals on player_game_lines (season, goals desc);
create index ix_pgl_player on player_game_lines (player_id);

create table draft_picks (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  round int not null,
  original_team_id uuid references teams(id),
  holder_team_id   uuid references teams(id),
  used boolean not null default false
);

create table managers (
  user_id uuid primary key,                 -- Supabase auth.users(id); FK added in Phase 7
  team_id uuid references teams(id),
  role text not null default 'manager',
  display_name text
);

create table trades (
  id uuid primary key default gen_random_uuid(),
  from_team_id uuid not null references teams(id),
  to_team_id   uuid not null references teams(id),
  status trade_status not null default 'proposed',
  proposed_by uuid references managers(user_id),
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  check (from_team_id <> to_team_id)
);

create table trade_assets (
  id bigserial primary key,
  trade_id uuid not null references trades(id) on delete cascade,
  direction text not null check (direction in ('to_from','to_to')),
  player_id uuid references players(id),
  draft_pick_id uuid references draft_picks(id),
  check ((player_id is null) <> (draft_pick_id is null))
);
create index ix_trade_assets_trade on trade_assets (trade_id);

create view player_season_stats as
  select player_id,
         season,
         max(team_id::text)::uuid as team_id,   -- no max(uuid) aggregate in PG
         count(*)            as games,
         sum(goals)          as goals,
         sum(shots)          as shots,
         sum(saves)          as saves,
         sum(goals_allowed)  as goals_allowed
  from player_game_lines
  group by player_id, season;

create view player_public as
  select id, team_id, name, position,
         slot_group, slot_position, slot_order,
         age, offense, defense, goalie_skill,
         is_injured, contract_term, contract_value, years_remaining
  from players;
"""

DROP = r"""
drop view if exists player_public;
drop view if exists player_season_stats;
drop table if exists trade_assets;
drop table if exists trades;
drop table if exists managers;
drop table if exists draft_picks;
drop table if exists player_game_lines;
drop table if exists games;
drop table if exists awards;
drop table if exists injuries;
drop table if exists players;
drop table if exists teams;
drop type if exists trade_status;
drop type if exists roster_group;
drop type if exists player_position;
"""


def upgrade() -> None:
    # exec_driver_sql uses the simple-query protocol, which (unlike the extended
    # protocol SQLAlchemy/psycopg3 use for text()) allows many statements at once.
    op.get_bind().exec_driver_sql(SCHEMA)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DROP)
