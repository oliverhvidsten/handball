"""team identity a manager can edit: nickname, abbreviation, logo

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-12

`teams.slug` is the domain TeamId and `teams.name` is the city; both are keys --
league_structure, the schedule, the rivals file, the Teams page's division layout
and pg_repository all look teams up by them -- so neither is made editable. What a
manager may change is ADDITIVE and display-only:

  - nickname      the team name after the city ("Aces" in "Las Vegas Aces"); null
                  means the city stands alone, exactly as today
  - abbr          the 2-4 character mark shown in the switcher and on team tiles;
                  null means the app keeps deriving one from the city, as it does
                  now. Unique league-wide (case-insensitively) because the team
                  switcher keys on it
  - logo_version  bumped on every logo change so the browser's cached image is
                  invalidated by URL, not by header games

The image itself lives in `team_logos`, one row per team, re-encoded server-side to
a bounded PNG (handball/team_settings.py) -- never the manager's bytes verbatim --
and served by `GET /teams/{slug}/logo`, which is unauthenticated because an <img>
tag cannot send a bearer token and a logo is not a secret. Clients never read the
table directly.
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


_SCHEMA = r"""
alter table teams
  add column nickname text,
  add column abbr text,
  add column logo_version int not null default 0;
create unique index ux_teams_abbr on teams (upper(abbr)) where abbr is not null;

create table team_logos (
  team_id uuid primary key references teams(id) on delete cascade,
  mime text not null,
  bytes bytea not null,
  updated_at timestamptz not null default now()
);
alter table team_logos enable row level security;
"""

_SCHEMA_DOWN = r"""
drop table if exists team_logos;
drop index if exists ux_teams_abbr;
alter table teams
  drop column if exists logo_version,
  drop column if exists abbr,
  drop column if exists nickname;
"""

_SUPABASE = """
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;              -- not Supabase (no auth roles); RLS is on, nothing else to do
  end if;
  -- Deny first (Supabase auto-grants SELECT on new tables); served through the API.
  execute $q$ revoke all on team_logos from anon $q$;
  execute $q$ revoke all on team_logos from authenticated $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA)
    op.get_bind().exec_driver_sql(_SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA_DOWN)
