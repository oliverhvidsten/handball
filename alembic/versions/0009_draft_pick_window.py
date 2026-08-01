"""draft_picks unique constraint + 10-year rolling window backfill

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-01

Backfills draft_picks so the Team Roster page always has real rows to read
(and trade) for the next 10 draft classes, not just the season seeded by the
last offseason rollover:

  - unique (season, round, original_team_id) : lets _seed_draft_order become
    an idempotent ON CONFLICT upsert (see handball/offseason.py) instead of
    its old DELETE+INSERT, which silently wiped any trade already made on a
    future pick once that season's real order got seeded.
  - backfill : for every existing team and both draft rounds, insert a
    placeholder pick (holder = original = team, pick_number null, used
    false) for every season from active_season+1 through active_season+10,
    wherever a row doesn't already exist. "Active season" is derived the
    same way api/main.py:_active_season() does: season_state max, else
    games max, else 2026.

Downgrade drops the constraint only; it does not attempt to un-backfill rows
-- there's no reliable way to tell a placeholder apart from a real pick after
the fact (pick_number is null for un-realized rounds either way), so
downgrade leaves data alone, consistent with 0007/0008's data-safe
downgrades.
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_ACTIVE_SEASON_SQL = (
    "coalesce((select max(season) from season_state), (select max(season) from games), 2026)"
)

_ADD_CONSTRAINT = (
    "alter table draft_picks "
    "add constraint ux_draft_picks_season_round_team unique (season, round, original_team_id);"
)

_DROP_CONSTRAINT = "alter table draft_picks drop constraint if exists ux_draft_picks_season_round_team;"

# 10-year rolling window: for every team x round x (active_season+1 .. active_season+10),
# insert a placeholder pick if one isn't already there. Draft rounds are fixed at 2
# (handball/simulation_vars.py:DRAFT_ROUNDS), inlined here rather than read from a table.
_BACKFILL = f"""
insert into draft_picks (season, round, original_team_id, holder_team_id, pick_number, used)
select yr.season, rnd.round, t.id, t.id, null, false
from teams t
cross join generate_series(1, 2) as rnd(round)
cross join generate_series(({_ACTIVE_SEASON_SQL}) + 1, ({_ACTIVE_SEASON_SQL}) + 10) as yr(season)
on conflict (season, round, original_team_id) do nothing;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_ADD_CONSTRAINT)
    op.get_bind().exec_driver_sql(_BACKFILL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DROP_CONSTRAINT)
