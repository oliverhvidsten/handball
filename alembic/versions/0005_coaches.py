"""coaches + career history (tenure ranges)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-29

Tracks coaches as first-class entities with career history. Every team has a
Head Coach / Offensive Coordinator / Defensive Coordinator; coaches do NOT affect
gameplay, but their careers are tracked as TENURE RANGES: one `coach_tenures` row
per continuous stint (team + role + start_season + end_season), where a NULL
end_season marks the CURRENT post. A coach's career spans teams, so `coaches` is a
top-level table, not a child of `teams`.

The pre-existing `teams.coaches text[]` column stays as a denormalized advisory
cache (kept in sync by the seed script); the AUTHORITATIVE source on the read path
is these tables and the two views below. The frontend reads `team_coaches` /
`coach_career`, never `teams.coaches`.

Two partial unique indexes encode the core invariant ("an open tenure == current"):
at most one open tenure per (team, role), and at most one open tenure per coach.
They also make the seed script's idempotent re-runs collision-safe.

Supabase-specific bits (role grants/policies) are guarded behind the
authenticated-role check, so on a vanilla local Postgres this is a portable no-op
beyond the schema/view changes. RLS is enabled on the two new tables HERE because
the 0002 fixed table list does not cover them.
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


# Portable schema (runs everywhere): enum, tables, indexes, views.
SCHEMA = r"""
create type coach_role as enum ('HC','OC','DC');

create table coaches (
  id uuid primary key default gen_random_uuid(),
  legacy_id text unique not null,        -- stable slug, e.g. "jane-doe"
  name text not null,
  created_at timestamptz not null default now()
);

create table coach_tenures (
  id bigserial primary key,
  coach_id uuid not null references coaches(id) on delete cascade,
  team_id  uuid not null references teams(id)   on delete cascade,
  role coach_role not null,
  start_season int not null,
  end_season int,                         -- NULL == current
  ord int not null default 0,
  check (end_season is null or end_season >= start_season)
);
create index ix_coach_tenures_coach on coach_tenures (coach_id, ord);
create index ix_coach_tenures_team  on coach_tenures (team_id);
-- the invariants: at most one OPEN tenure per team+role, and per coach.
create unique index ux_coach_tenures_open_team_role
  on coach_tenures (team_id, role) where end_season is null;
create unique index ux_coach_tenures_open_coach
  on coach_tenures (coach_id) where end_season is null;

create view team_coaches as
  select t.slug as team_slug, t.name as team_name,
         ct.role, c.legacy_id as coach_legacy_id, c.name as coach_name, ct.start_season
  from coach_tenures ct
  join teams t on t.id = ct.team_id
  join coaches c on c.id = ct.coach_id
  where ct.end_season is null;

create view coach_career as
  select c.legacy_id as coach_legacy_id, c.name as coach_name,
         t.slug as team_slug, t.name as team_name,
         ct.role, ct.start_season, ct.end_season, ct.ord
  from coach_tenures ct
  join coaches c on c.id = ct.coach_id
  join teams t on t.id = ct.team_id;
"""

SCHEMA_DOWN = r"""
drop view if exists coach_career;
drop view if exists team_coaches;
drop table if exists coach_tenures;
drop table if exists coaches;
drop type if exists coach_role;
"""

# Supabase-only: RLS on the new base tables (deny-all to clients; backend /
# service_role bypasses), and SELECT grants on the safe VIEWS only.
SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ alter table coaches enable row level security $q$;
  execute $q$ alter table coach_tenures enable row level security $q$;
  execute $q$ revoke all on team_coaches from anon $q$;
  execute $q$ revoke all on coach_career from anon $q$;
  execute $q$ grant select on team_coaches to authenticated $q$;
  execute $q$ grant select on coach_career to authenticated $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SCHEMA)
    op.get_bind().exec_driver_sql(SUPABASE)


def downgrade() -> None:
    # Views depend on the tables which depend on the type, so drop in that order.
    op.get_bind().exec_driver_sql(SCHEMA_DOWN)
