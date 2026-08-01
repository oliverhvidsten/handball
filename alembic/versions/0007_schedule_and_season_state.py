"""persisted schedule + season-run cursor

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-29

Makes "run the season" possible from the website. Two pieces of durable state the
batch simulator assumes but never had a home:

  - schedule_games : the fixture list. Until now LeagueOperations.generate_schedule()
    built a fresh OR-Tools schedule in memory each time the API process started, so
    period 2 would be played against a DIFFERENT random schedule than period 1.
    Persisting the generated fixtures fixes the schedule once per season; the runner
    and the website's "Upcoming" tab both read from here.
  - season_state : one row per season -- how far the sim has advanced (periods_run),
    plus the RNG seeds so a re-run is reproducible. This is the "current period"
    cursor the Commissioner page needs to know what to run next.

A unique index on games(season, week, home, away) guards against a replayed or
partially-failed period silently double-recording games.

Supabase-specific bits (RLS + grants) are guarded behind the authenticated-role
check, so on a vanilla local Postgres this is a portable no-op beyond the schema.
RLS is enabled on the two new tables HERE because the 0002 fixed table list does not
cover them. schedule_games is public-to-members read (like games/teams); season_state
is backend-only -- the website reads it through the API, never directly.
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


SCHEMA = r"""
create table schedule_games (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  week int not null,
  home_team_id uuid not null references teams(id),
  away_team_id uuid not null references teams(id),
  matchup_type text,
  unique (season, week, home_team_id, away_team_id)
);
create index ix_schedule_games_season_week on schedule_games (season, week);

create table season_state (
  season int primary key,
  periods_run int not null default 0,
  schedule_seed int,
  injury_seed int,
  schedule_generated boolean not null default false,
  -- a period takes ~minutes against a remote DB, past HTTP timeouts, so the run is
  -- a background job; the website polls these to show progress / surface errors.
  run_status text not null default 'idle',   -- idle | running | done | error
  run_period int,                            -- the period currently/last run
  run_error text,
  updated_at timestamptz not null default now()
);

-- guard: a replayed/partial period can't silently duplicate a played game.
create unique index ux_games_season_week_pair
  on games (season, week, home_team_id, away_team_id);
"""

SCHEMA_DOWN = r"""
drop index if exists ux_games_season_week_pair;
drop table if exists season_state;
drop table if exists schedule_games;
"""

# Supabase-only: enable RLS on the new base tables (deny-all to clients; the backend
# service_role bypasses RLS), and grant read on the public fixture list only.
SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ alter table schedule_games enable row level security $q$;
  execute $q$ alter table season_state  enable row level security $q$;
  execute $q$ grant select on schedule_games to authenticated $q$;
  execute $q$ create policy schedule_games_read on schedule_games
            for select to authenticated using (true) $q$;
end
$do$;
"""

DROP_SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ drop policy if exists schedule_games_read on schedule_games $q$;
  execute $q$ revoke select on schedule_games from authenticated $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SCHEMA)
    op.get_bind().exec_driver_sql(SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DROP_SUPABASE)
    op.get_bind().exec_driver_sql(SCHEMA_DOWN)
