"""coach age + pool role + all-coaches view

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-29

Two small additions to the coaches model driven by the initial roster import:

  - `coaches.age int` (nullable): each coach's age. A few roster entries have
    non-numeric ages ("Unknown", "[Age]"); those store NULL.
  - `coaches.pool_role coach_role` (nullable): the role list a coach was drafted
    from (HC/OC/DC). For an assigned coach this equals their current tenure role;
    for an UNASSIGNED coach (free-agent pool -- a coach row with no open tenure)
    it is the only role indicator, so a free agent stays identifiable as e.g. a
    free-agent Head Coach.

  - `coach_public` view: one row per coach (assigned or free agent), joining the
    current open tenure (NULL for free agents). This is what the Coaches index
    reads so the free-agent pool is visible alongside assigned coaches.

Supabase grants guarded as in 0005; the new view is granted to authenticated.
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


SCHEMA = r"""
alter table coaches add column age int;
alter table coaches add column pool_role coach_role;

create view coach_public as
  select c.legacy_id as coach_legacy_id, c.name as coach_name,
         c.age, c.pool_role,
         tc.role as cur_role,                -- NB: "current_role" is a reserved SQL keyword
         tc.team_slug as current_team_slug,
         tc.team_name as current_team_name
  from coaches c
  left join team_coaches tc on tc.coach_legacy_id = c.legacy_id;
"""

SCHEMA_DOWN = r"""
drop view if exists coach_public;
alter table coaches drop column if exists pool_role;
alter table coaches drop column if exists age;
"""

SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ revoke all on coach_public from anon $q$;
  execute $q$ grant select on coach_public to authenticated $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SCHEMA)
    op.get_bind().exec_driver_sql(SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(SCHEMA_DOWN)
