"""row-level security + auth wiring (Supabase)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18

Locks the database down for the "managers-only read" model:
  - RLS is enabled on every table (portable). Our Python backend connects as the
    table-owner/superuser role, which bypasses RLS, so the sim/migration/tests are
    unaffected; the API uses the service_role key, which also bypasses RLS. RLS
    therefore only governs the direct client (anon/authenticated) read path.
  - The Supabase-specific bits (FK to auth.users, grants + policies for the
    `authenticated`/`anon` roles) are guarded behind a role-existence check, so on
    a vanilla local Postgres (no Supabase roles) this migration is a no-op beyond
    enabling RLS.

Read model (authenticated only; anon gets nothing):
  - teams, games, player_public (safe cols), player_season_stats  -> any authenticated user
  - players/injuries/awards/draft_picks/player_game_lines (raw)    -> backend only
  - managers                                                       -> own row
  - trades/trade_assets                                            -> involved managers + commissioner
All writes go through the API (service_role), so no client write policies exist.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_RLS_TABLES = [
    "teams", "players", "injuries", "awards", "games", "player_game_lines",
    "draft_picks", "managers", "trades", "trade_assets",
]

ENABLE = "\n".join(f"alter table {t} enable row level security;" for t in _RLS_TABLES)

# player_public / player_season_stats stay SECURITY DEFINER views (the default):
# owned by the migrating role, they read the base tables past RLS and expose only
# safe columns, so we grant on the VIEW and never on the raw players table.
SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;   -- not Supabase (no auth roles); RLS is enabled, nothing else to do
  end if;

  if exists (select 1 from information_schema.schemata where schema_name = 'auth') then
    execute $q$ alter table managers
      add constraint managers_user_id_fkey
      foreign key (user_id) references auth.users(id) on delete cascade $q$;
  end if;

  -- Supabase's default privileges auto-grant SELECT on every new public table/view
  -- to BOTH anon and authenticated. Start from deny and grant back precisely, or
  -- anon (and security-definer views, which bypass RLS) would leak data.
  execute $q$ revoke all on all tables in schema public from anon $q$;
  execute $q$ revoke all on all tables in schema public from authenticated $q$;

  -- public-to-members league data
  execute $q$ grant select on teams to authenticated $q$;
  execute $q$ create policy teams_read on teams for select to authenticated using (true) $q$;

  execute $q$ grant select on games to authenticated $q$;
  execute $q$ create policy games_read on games for select to authenticated using (true) $q$;

  -- safe projections (security-definer views read the locked base tables)
  execute $q$ grant select on player_public to authenticated $q$;
  execute $q$ grant select on player_season_stats to authenticated $q$;

  -- a manager sees only their own manager row
  execute $q$ grant select on managers to authenticated $q$;
  execute $q$ create policy managers_self on managers for select to authenticated
            using (user_id = auth.uid()) $q$;

  -- trades visible to either involved manager, or any commissioner
  execute $q$ grant select on trades to authenticated $q$;
  execute $q$ create policy trades_involved on trades for select to authenticated using (
      exists (select 1 from managers m where m.user_id = auth.uid()
              and (m.team_id = trades.from_team_id or m.team_id = trades.to_team_id))
      or exists (select 1 from managers m where m.user_id = auth.uid() and m.role = 'commissioner')
  ) $q$;

  -- trade_assets follow their parent trade's visibility (the subquery is itself
  -- filtered by the trades policy above)
  execute $q$ grant select on trade_assets to authenticated $q$;
  execute $q$ create policy trade_assets_visible on trade_assets for select to authenticated
            using (exists (select 1 from trades t where t.id = trade_assets.trade_id)) $q$;
end
$do$;
"""

DROP_SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ drop policy if exists teams_read on teams $q$;
  execute $q$ drop policy if exists games_read on games $q$;
  execute $q$ drop policy if exists managers_self on managers $q$;
  execute $q$ drop policy if exists trades_involved on trades $q$;
  execute $q$ drop policy if exists trade_assets_visible on trade_assets $q$;
  execute $q$ revoke select on teams, games, player_public, player_season_stats,
                 managers, trades, trade_assets from authenticated $q$;
  execute $q$ alter table managers drop constraint if exists managers_user_id_fkey $q$;
end
$do$;
"""

DISABLE = "\n".join(f"alter table {t} disable row level security;" for t in _RLS_TABLES)


def upgrade() -> None:
    op.get_bind().exec_driver_sql(ENABLE)
    op.get_bind().exec_driver_sql(SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DROP_SUPABASE)
    op.get_bind().exec_driver_sql(DISABLE)
