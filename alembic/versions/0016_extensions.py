"""contract extensions: a deal agreed this season that starts next season

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-12

An extension is not a contract yet. It is a promise, signed in the extension window
of season S by a player with one year left, which becomes the contract at the S -> S+1
rollover -- the current salary still counts against this season's cap, the new one
counts against next season's. So it cannot be written into `contract_term`/
`contract_value` (that would re-price the current season) and it cannot live in a
pending-agreements table either, because there is at most ONE per player, it binds
immediately, and it has to be readable everywhere a player's contract is read.

Three nullable columns on `players`, therefore, with null meaning "no extension":

  - ext_term, ext_value : the deal that starts next season.
  - ext_signed_season   : which extension window it was signed in -- the audit, and
    the thing that makes "no existing ext_term" an honest eligibility test rather
    than a flag somebody forgot to clear.

`offseason.advance_season` applies them (through Player.update_contract, so the
restricted/rookie flags clear the way any new contract clears them) and then clears
all three, so the columns are also the WORK QUEUE for the rollover.

player_public gains the three columns: the roster page badges an extended player, and
who is locked up is not secret -- it is the single most useful thing to know when
proposing a trade. Recreating the view drops its grants, so the Supabase-guarded block
re-applies them, the same dance as 0003/0008/0010/0011.

Downgrade drops the columns and restores 0011's view definition. Any unapplied
extension is lost, which is correct: the columns are the only record there was one.
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


_SCHEMA = r"""
alter table players
  add column ext_term int,
  add column ext_value int,
  add column ext_signed_season int;
"""

_SCHEMA_DOWN = r"""
alter table players
  drop column if exists ext_signed_season,
  drop column if exists ext_value,
  drop column if exists ext_term;
"""

# player_public column lists (mirror 0011, + the three extension columns).
_COLS_NEW = ("id, legacy_id, team_id, rights_team_id, name, position, slot_group, slot_position, "
             "slot_order, age, offense, defense, goalie_skill, is_injured, contract_term, "
             "contract_value, years_remaining, restricted_free_agent, retired, "
             "ext_term, ext_value, ext_signed_season")
_COLS_OLD = ("id, legacy_id, team_id, rights_team_id, name, position, slot_group, slot_position, "
             "slot_order, age, offense, defense, goalie_skill, is_injured, contract_term, "
             "contract_value, years_remaining, restricted_free_agent, retired")

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
    op.get_bind().exec_driver_sql(_SCHEMA)
    _recreate_player_public(_COLS_NEW)


def downgrade() -> None:
    # The view first: it selects the columns that are about to go.
    _recreate_player_public(_COLS_OLD)
    op.get_bind().exec_driver_sql(_SCHEMA_DOWN)
