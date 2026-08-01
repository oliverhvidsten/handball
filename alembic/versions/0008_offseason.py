"""offseason rollover: retirement, draft order, free agency

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-29

Supports "advance season" -- the offseason rollover from season N to N+1:
  - players.retired / retired_season : a commissioner-curated retirement flag. A
    retired player is removed from rosters (team_id + slots cleared) but the ROW is
    kept so career stats and FKs survive. A free agent, by contrast, is simply a
    non-retired player with team_id IS NULL (no separate table needed).
  - draft_picks.pick_number : overall pick order. The table had round + teams but no
    ordering column, so the Draft page couldn't show the seeded worst->best order.
  - player_public exposes `retired` so the frontend can tag retirees / filter the FA
    pool (it already exposes team_id).

Recreating player_public drops its grants (and may re-trigger Supabase's
default-privilege auto-grant to anon), so the Supabase-guarded block re-applies the
precise grant: authenticated only, never anon -- same dance as 0003.
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# player_public column lists (mirror 0003, + retired).
_COLS_NEW = ("id, legacy_id, team_id, name, position, slot_group, slot_position, slot_order, "
             "age, offense, defense, goalie_skill, is_injured, contract_term, contract_value, "
             "years_remaining, retired")
_COLS_OLD = ("id, legacy_id, team_id, name, position, slot_group, slot_position, slot_order, "
             "age, offense, defense, goalie_skill, is_injured, contract_term, contract_value, "
             "years_remaining")

_GRANT_PLAYER_PUBLIC = r"""
do $do$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    execute $q$ revoke all on player_public from anon $q$;
    execute $q$ revoke all on player_public from authenticated $q$;
    execute $q$ grant select on player_public to authenticated $q$;
  end if;
end $do$;
"""


def _recreate_player_public(cols: str) -> None:
    op.get_bind().exec_driver_sql(
        f"drop view if exists player_public; "
        f"create view player_public as select {cols} from players;"
    )
    op.get_bind().exec_driver_sql(_GRANT_PLAYER_PUBLIC)


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        "alter table players add column retired boolean not null default false; "
        "alter table players add column retired_season int; "
        "alter table draft_picks add column pick_number int;"
    )
    _recreate_player_public(_COLS_NEW)


def downgrade() -> None:
    _recreate_player_public(_COLS_OLD)
    op.get_bind().exec_driver_sql(
        "alter table draft_picks drop column pick_number; "
        "alter table players drop column retired_season; "
        "alter table players drop column retired;"
    )
