"""multi-team ownership + read-unlocks for the new UI pages

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-19

Two things at once:

Phase A -- multi-team ownership. A manager OWNS many teams (the design's
TeamSwitcher / "My Teams" model), so ownership moves from the single
`managers.team_id` to `teams.owner_id`:
  - add teams.owner_id -> managers(user_id); drop the now-meaningless
    managers.team_id.
  - add trades.internal (both teams owned by the proposer -- skips counterparty
    acceptance, still commissioner-approved).
  - RLS: replace the trades_involved policy (was managers.team_id based) with one
    keyed on teams.owner_id.

Phase B -- read-unlocks for the new pages (player detail, draft, box scores,
leaders). All *public* league info; the hidden scouting columns on `players`
stay backend-only (still reached only through the safe views).
  - grant authenticated SELECT + using(true) on injuries, awards, draft_picks,
    player_game_lines (RLS already enabled on them in 0002 -> deny-all today).
  - add a name-aware player_leaderboard security-definer view so Leaders/Player
    detail show names, not uuids.

Supabase-specific bits (FK to auth.users via managers, role grants/policies) are
guarded behind the authenticated-role check, so this is a portable no-op beyond
the schema/view changes on a vanilla local Postgres.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# Portable schema (runs everywhere): ownership column, internal flag, leaderboard view.
SCHEMA = r"""
alter table teams add column owner_id uuid references managers(user_id) on delete set null;
create index ix_teams_owner on teams (owner_id);

alter table trades add column internal boolean not null default false;

alter table managers drop column if exists team_id;

create view player_leaderboard as
  select p.legacy_id, p.name, p.position,
         t.slug as team_slug, t.name as team_name,
         s.season, s.games, s.goals, s.shots, s.saves, s.goals_allowed
  from player_season_stats s
  join players p on p.id = s.player_id
  left join teams t on t.id = s.team_id;
"""

SCHEMA_DOWN = r"""
drop view if exists player_leaderboard;
alter table managers add column team_id uuid references teams(id);
alter table trades drop column if exists internal;
drop index if exists ix_teams_owner;
alter table teams drop column if exists owner_id;
"""

# Supabase-only, runs BEFORE the schema change: the 0002 trades_involved policy
# references managers.team_id, so it must be dropped before that column can go.
# (No-op locally, where the policy was never created.)
SUPABASE_PRE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ drop policy if exists trades_involved on trades $q$;
end
$do$;
"""

# Supabase-only: rewrite trades RLS to ownership-by-owner, unlock the new reads.
SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;

  -- trades: visible if the caller owns either side, or is commissioner
  execute $q$ drop policy if exists trades_involved on trades $q$;
  execute $q$ create policy trades_involved on trades for select to authenticated using (
      exists (select 1 from teams t
              where t.id in (trades.from_team_id, trades.to_team_id)
              and t.owner_id = auth.uid())
      or exists (select 1 from managers m where m.user_id = auth.uid() and m.role = 'commissioner')
  ) $q$;

  -- Phase B read-unlocks (public league data; hidden player columns stay locked)
  execute $q$ grant select on injuries to authenticated $q$;
  execute $q$ create policy injuries_read on injuries for select to authenticated using (true) $q$;

  execute $q$ grant select on awards to authenticated $q$;
  execute $q$ create policy awards_read on awards for select to authenticated using (true) $q$;

  execute $q$ grant select on draft_picks to authenticated $q$;
  execute $q$ create policy draft_picks_read on draft_picks for select to authenticated using (true) $q$;

  execute $q$ grant select on player_game_lines to authenticated $q$;
  execute $q$ create policy pgl_read on player_game_lines for select to authenticated using (true) $q$;

  -- safe leaderboard projection (security-definer view reads locked players)
  execute $q$ revoke all on player_leaderboard from anon $q$;
  execute $q$ grant select on player_leaderboard to authenticated $q$;
end
$do$;
"""

SUPABASE_DOWN = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ drop policy if exists injuries_read on injuries $q$;
  execute $q$ drop policy if exists awards_read on awards $q$;
  execute $q$ drop policy if exists draft_picks_read on draft_picks $q$;
  execute $q$ drop policy if exists pgl_read on player_game_lines $q$;
  execute $q$ revoke select on injuries, awards, draft_picks, player_game_lines from authenticated $q$;

  -- restore the 0002 (single-team) trades policy
  execute $q$ drop policy if exists trades_involved on trades $q$;
  execute $q$ create policy trades_involved on trades for select to authenticated using (
      exists (select 1 from managers m where m.user_id = auth.uid()
              and (m.team_id = trades.from_team_id or m.team_id = trades.to_team_id))
      or exists (select 1 from managers m where m.user_id = auth.uid() and m.role = 'commissioner')
  ) $q$;
end
$do$;
"""


def upgrade() -> None:
    # drop the team_id-dependent policy first, THEN change schema, then re-policy.
    op.get_bind().exec_driver_sql(SUPABASE_PRE)
    op.get_bind().exec_driver_sql(SCHEMA)
    op.get_bind().exec_driver_sql(SUPABASE)


def downgrade() -> None:
    # restore single-team RLS BEFORE dropping the leaderboard view / re-adding team_id
    op.get_bind().exec_driver_sql(SUPABASE_DOWN)
    op.get_bind().exec_driver_sql(SCHEMA_DOWN)
