"""the Hall of Fame

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-12

One table. An induction is a commissioner's decision about a RETIRED player, and the
only facts it carries that cannot be recomputed are which class the player went in
with and what the citation said -- the career line underneath it is aggregated from
`player_game_lines` at read time, so the Hall cannot drift from the record books.

`player_id` is unique: a player is inducted once. `inducted_season` is the class year
(the season whose offseason inducted them) and is stored rather than derived from
`inducted_at`, because a commissioner catching up on a backlog inducts three classes
in an afternoon and the classes still have to be right.

Rescinding is a DELETE. There is no 'rescinded' status, because a Hall of Fame with
an un-inducted wing in it is a thing nobody wants to render, and the honest reason to
rescind is that the induction was a mistake.

RLS is enabled and nothing is granted to clients: the API serves `GET /hall-of-fame`
with the career aggregates attached, and a client reading the bare table would get the
citations without the careers.
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


_SCHEMA = r"""
create table hall_of_fame (
  id bigserial primary key,
  player_id uuid not null unique references players(id) on delete cascade,
  inducted_season int not null,
  citation text,
  inducted_at timestamptz not null default now()
);
create index ix_hall_of_fame_season on hall_of_fame (inducted_season);

alter table hall_of_fame enable row level security;
"""

_SCHEMA_DOWN = "drop table if exists hall_of_fame;"

_SUPABASE = """
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;              -- not Supabase (no auth roles); RLS is on, nothing else to do
  end if;
  -- Deny first (Supabase auto-grants SELECT on new tables); served through the API.
  execute $q$ revoke all on hall_of_fame from anon $q$;
  execute $q$ revoke all on hall_of_fame from authenticated $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA)
    op.get_bind().exec_driver_sql(_SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA_DOWN)
