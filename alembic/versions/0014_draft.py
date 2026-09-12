"""the live draft: a lottery, a prospect class, and a room with a clock

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-12

The draft has existed as a column on `draft_picks` (an order, seeded at rollover) and
as an in-memory `postseason.DraftService` that handed out flat rookie deals. Neither
knows who is on the board, whose turn it is, or what a pick is worth. This is the
schema the real thing needs, and nothing else -- the rules live in handball/draft.py.

  - draft_picks gains the OUTCOME of a pick (`player_id`, `picked_at`, `auto_pick`)
    and the CONDITION on one (`protection_top_n`, `protection_outcome`). A protection
    is stored on the pick rather than derived from the trade that created it, because
    a pick can be traded again and the protection travels with it; `protection_outcome`
    records how the condition finally resolved ('conveyed' | 'reverted') so a pick that
    reverted still reads as a pick that was once traded, which is what a trade history
    needs to say. `auto_pick` separates a selection a manager made from one the clock
    made for them -- the same distinction fa_actions draws for a forfeit, and for the
    same reason: only one of them is a person.

  - trade_assets gains `protection_top_n`, where a protection is AGREED. It is only
    meaningful on a pick asset; the check that it is a round-1 pick is a rules-layer
    job (it needs the pick row), not something a column constraint can see.

  - draft_lotteries is the draw, one row per season. `standings_order` is written at
    rollover -- the 16 non-playoff teams worst-first -- and is what makes the draw
    REPRODUCIBLE: the odds depend on an ordering of teams that the rollover then
    destroys when it zeroes the records. `seed` is stored with the `results` so a
    contested draw can be replayed and shown to be the draw that happened.

  - draft_state is the room's cursor: one row per season, `current_overall` counting
    1..64 and `turn_started_at` anchoring the auto-pick clock. It is separate from
    season_state for the same reason fa_periods is: season_state models one background
    RUN, not a league phase, and the draft is a phase that spans days.

  - draft_prospects is the class. A prospect is GENERATED ONCE, at upload, and the
    whole generated player is kept in `player_json` -- the visible ratings are lifted
    into columns so the board can sort on them, but the row the pick turns into a
    Player is the json. Generating at pick time instead would mean the board showed
    ratings that the signed player did not have. `player_id` is null until drafted and
    then points at the created player, so an undrafted prospect (who becomes an
    ordinary free agent when the draft closes) is distinguishable from a drafted one.
    unique(season, ord) keeps the uploaded file's order stable.

RLS: enabled on all three new tables, and NOTHING is granted to clients. Every read
here goes through the API (`GET /draft/state`), which is also where the lazy turn
sweep lives -- a board served straight from Supabase would be a board whose clock
never ticks. That is the same call 0011 made for fa_offers, for a different reason.

Downgrade drops the three tables and the six added columns; picks already made lose
their `player_id` link but the players themselves stay, since a drafted player is a
player like any other.
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


_SCHEMA = r"""
alter table draft_picks
  add column player_id uuid references players(id) on delete set null,
  add column picked_at timestamptz,
  add column auto_pick boolean not null default false,
  add column protection_top_n int,
  add column protection_outcome text
    check (protection_outcome is null or protection_outcome in ('conveyed','reverted'));

alter table trade_assets
  add column protection_top_n int;

create table draft_lotteries (
  season int primary key,
  standings_order jsonb not null,     -- the 16 non-playoff team ids, worst first
  results jsonb,                      -- ordered [{team_id, slot}], written at the draw
  seed int,
  drawn_at timestamptz
);

create table draft_state (
  season int primary key,
  status text not null default 'pending'
    check (status in ('pending','lottery_drawn','open','complete')),
  current_overall int,
  turn_started_at timestamptz,
  updated_at timestamptz not null default now()
);

create table draft_prospects (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  ord int not null,
  name text not null,
  position player_position not null,
  age int,
  offense float8,
  defense float8,
  goalie_skill float8,
  player_json jsonb not null,
  player_id uuid references players(id) on delete set null,
  unique (season, ord)
);
create index ix_draft_prospects_season on draft_prospects (season);

alter table draft_lotteries enable row level security;
alter table draft_state     enable row level security;
alter table draft_prospects enable row level security;
"""

_SCHEMA_DOWN = r"""
drop table if exists draft_prospects;
drop table if exists draft_state;
drop table if exists draft_lotteries;

alter table trade_assets drop column if exists protection_top_n;

alter table draft_picks
  drop column if exists protection_outcome,
  drop column if exists protection_top_n,
  drop column if exists auto_pick,
  drop column if exists picked_at,
  drop column if exists player_id;
"""

_NEW_TABLES = "draft_lotteries, draft_state, draft_prospects"

_SUPABASE = f"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;              -- not Supabase (no auth roles); RLS is on, nothing else to do
  end if;
  -- Supabase default privileges auto-grant SELECT on every new table to anon and
  -- authenticated, so deny (same as 0002/0011). No grant back: the draft room is
  -- served by the API, which sweeps the turn clock before it reads.
  execute $q$ revoke all on {_NEW_TABLES} from anon $q$;
  execute $q$ revoke all on {_NEW_TABLES} from authenticated $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA)
    op.get_bind().exec_driver_sql(_SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA_DOWN)
