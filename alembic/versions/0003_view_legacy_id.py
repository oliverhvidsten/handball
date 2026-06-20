"""expose legacy_id in player_public

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-18

The API/domain identify a player by `legacy_id` (the stable PlayerId, e.g.
"boston-evan-clarkson") -- that's what apply_arrangement and trade_service expect.
The original player_public view exposed only the surrogate uuid `id`, so the
frontend had no way to name a player to the write API. Add `legacy_id` to the
view. Recreating the view drops its grants (and may re-trigger Supabase's
default-privilege auto-grant to anon), so the Supabase-guarded block re-applies
the precise grant: authenticated only, never anon.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_COLS_NEW = ("id, legacy_id, team_id, name, position, slot_group, slot_position, slot_order, "
             "age, offense, defense, goalie_skill, is_injured, contract_term, contract_value, "
             "years_remaining")
_COLS_OLD = ("id, team_id, name, position, slot_group, slot_position, slot_order, "
             "age, offense, defense, goalie_skill, is_injured, contract_term, contract_value, "
             "years_remaining")


def _recreate(cols: str) -> None:
    op.get_bind().exec_driver_sql(
        f"drop view if exists player_public; "
        f"create view player_public as select {cols} from players;"
    )
    # Re-apply the precise grant on Supabase (drop removed it; recreate may have
    # re-granted anon via default privileges).
    op.get_bind().exec_driver_sql(
        r"""
        do $do$
        begin
          if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute $q$ revoke all on player_public from anon $q$;
            execute $q$ revoke all on player_public from authenticated $q$;
            execute $q$ grant select on player_public to authenticated $q$;
          end if;
        end $do$;
        """
    )


def upgrade() -> None:
    _recreate(_COLS_NEW)


def downgrade() -> None:
    _recreate(_COLS_OLD)
