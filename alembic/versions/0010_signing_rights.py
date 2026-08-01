"""free-agent signing: Bird rights on the team a contract expired off

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-01

Supports free-agent signing / re-signing (handball/signing_service.py):

  - players.rights_team_id : the team that holds this free agent's Bird rights --
    i.e. the team the player's LAST contract expired off. A free agent is still just
    "non-retired with team_id is null" (no separate pool table); this column is the
    one fact a signing needs that the row otherwise loses at expiry, because
    offseason._process_free_agency clears team_id. salary_cap treats an own-player
    re-signing as bounded only by the hard cap (Bird rights), so without it every
    re-signing would look like an outside signing and be capped at cap room + MLE.

    Set at expiry (offseason._process_free_agency), cleared when the player signs
    anywhere (signing_service.sign_free_agent) or retires (offseason.retire_players).
    ON DELETE SET NULL matches players.team_id: rights are a convenience, never a
    reason to block a team delete.

    NOT backfilled: for free agents already in the pool there is no record of the
    team their contract ran out on, so they start with no rights holder (every team
    bids on them as an outside signing). Contracts expiring from the next rollover
    on carry rights correctly.

  - player_public exposes rights_team_id so the website can tag "your rights" in
    the free-agent list and pick the right cap ceiling to offer, reading the view
    directly like every other list page.

Recreating player_public drops its grants (and may re-trigger Supabase's
default-privilege auto-grant to anon), so the Supabase-guarded block re-applies the
precise grant: authenticated only, never anon -- same dance as 0003/0008.
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# player_public column lists (mirror 0008, + rights_team_id).
_COLS_NEW = ("id, legacy_id, team_id, rights_team_id, name, position, slot_group, slot_position, "
             "slot_order, age, offense, defense, goalie_skill, is_injured, contract_term, "
             "contract_value, years_remaining, retired")
_COLS_OLD = ("id, legacy_id, team_id, name, position, slot_group, slot_position, slot_order, "
             "age, offense, defense, goalie_skill, is_injured, contract_term, contract_value, "
             "years_remaining, retired")

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
        "alter table players add column rights_team_id uuid "
        "  references teams(id) on delete set null; "
        # The free-agent list is filtered/joined by rights holder on every page load.
        "create index ix_players_rights_team on players (rights_team_id) "
        "  where rights_team_id is not null;"
    )
    _recreate_player_public(_COLS_NEW)


def downgrade() -> None:
    _recreate_player_public(_COLS_OLD)
    op.get_bind().exec_driver_sql(
        "drop index if exists ix_players_rights_team; "
        "alter table players drop column rights_team_id;"
    )
