"""postseason: the bracket, playoff-tagged games, and the round cursor

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-01

The postseason, which until now existed only in handball/postseason.py's in-memory
PlayoffService and had nowhere durable to land. Three pieces:

  - playoff_series : the bracket itself. One row per matchup, created a round at a
    time: round 1 from the final standings, each later round from the previous
    round's winners. The format is ONE GAME per matchup (see handball/playoffs.py),
    so the deciding game is a single nullable FK rather than a child table -- a
    best-of-N would replace `game_id` with a series_games join and leave the rest of
    this schema alone. `high_seed`/`low_seed` store the seed NUMBERS (1..8) as well
    as the team ids, because a bracket page needs to print "1 vs 8" and re-deriving
    a seed after the fact means re-running the standings sort.

  - is_playoff on games + player_game_lines : playoff games ARE recorded (box
    scores, playoff stat lines) but must not leak into season aggregates. The flag
    is denormalized onto player_game_lines rather than joined from games so the
    leaderboard views stay single-table scans; the two are written together in one
    transaction by PostgresRecordSink, so they cannot disagree. Both leaderboard
    views are recreated to filter regular-season lines only, which is what keeps the
    MVP race and the Leaders page unchanged by the postseason. Team W-L is never
    touched at all -- the runner plays throwaway team copies and never saves them.

  - season_state.playoff_rounds_run + run_kind : the postseason cursor, alongside
    periods_run. run_kind tags what the ONE background-run slot is currently doing
    ('period' | 'playoff'), because reset_run rolls back by week range and would
    happily delete regular-season games if it mistook a failed playoff round for a
    failed period.

RLS/grants follow 0007: playoff_series is public-to-members read (like games and
schedule_games), so the bracket page can read it straight from Supabase.
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


_SCHEMA = r"""
alter table games add column is_playoff boolean not null default false;
alter table games add column playoff_round int;
alter table player_game_lines add column is_playoff boolean not null default false;

create table playoff_series (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  round int not null,                    -- 1..4 (quarterfinal -> final)
  conference text,                       -- null for the Final (cross-conference)
  label text not null,                   -- 'Eastern Quarterfinals', 'Final', ...
  high_seed_team_id uuid not null references teams(id),
  low_seed_team_id  uuid not null references teams(id),
  high_seed int not null,                -- seed NUMBER within the conference, 1..8
  low_seed  int not null,
  game_id uuid references games(id) on delete set null,
  winner_team_id uuid references teams(id),
  created_at timestamptz not null default now(),
  -- a team appears at most once per round, on one side or the other. Two indexes
  -- rather than one, because which side a team lands on is not knowable up front.
  unique (season, round, high_seed_team_id),
  unique (season, round, low_seed_team_id)
);
create index ix_playoff_series_season_round on playoff_series (season, round);

alter table season_state add column playoff_rounds_run int not null default 0;
alter table season_state add column run_kind text not null default 'period';
"""

_SCHEMA_DOWN = r"""
alter table season_state drop column if exists run_kind;
alter table season_state drop column if exists playoff_rounds_run;
drop table if exists playoff_series;
alter table player_game_lines drop column if exists is_playoff;
alter table games drop column if exists playoff_round;
alter table games drop column if exists is_playoff;
"""


# -- the two leaderboard views ----------------------------------------------
# player_leaderboard is built ON player_season_stats, so it must be dropped first
# and recreated after. Recreating a view drops its grants (and on Supabase may
# re-trigger the default-privilege auto-grant to anon), so both are re-granted
# precisely afterwards -- same dance as 0003/0004.
def _recreate_views(*, exclude_playoffs: bool) -> None:
    where = "where is_playoff = false" if exclude_playoffs else ""
    op.get_bind().exec_driver_sql(
        f"""
        drop view if exists player_leaderboard;
        drop view if exists player_season_stats;

        create view player_season_stats as
          select player_id,
                 season,
                 max(team_id::text)::uuid as team_id,   -- no max(uuid) aggregate in PG
                 count(*)            as games,
                 sum(goals)          as goals,
                 sum(shots)          as shots,
                 sum(saves)          as saves,
                 sum(goals_allowed)  as goals_allowed
          from player_game_lines
          {where}
          group by player_id, season;

        create view player_leaderboard as
          select p.legacy_id, p.name, p.position,
                 t.slug as team_slug, t.name as team_name,
                 s.season, s.games, s.goals, s.shots, s.saves, s.goals_allowed
          from player_season_stats s
          join players p on p.id = s.player_id
          left join teams t on t.id = s.team_id;
        """
    )
    op.get_bind().exec_driver_sql(
        r"""
        do $do$
        begin
          if not exists (select 1 from pg_roles where rolname = 'authenticated') then
            return;
          end if;
          execute $q$ revoke all on player_season_stats, player_leaderboard from anon $q$;
          execute $q$ revoke all on player_season_stats, player_leaderboard from authenticated $q$;
          execute $q$ grant select on player_season_stats, player_leaderboard to authenticated $q$;
        end $do$;
        """
    )


_SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;              -- not Supabase (no auth roles); nothing beyond the schema
  end if;
  execute $q$ alter table playoff_series enable row level security $q$;
  execute $q$ revoke all on playoff_series from anon $q$;
  execute $q$ revoke all on playoff_series from authenticated $q$;
  execute $q$ grant select on playoff_series to authenticated $q$;
  execute $q$ create policy playoff_series_read on playoff_series
              for select to authenticated using (true) $q$;
end
$do$;
"""

_DROP_SUPABASE = r"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ drop policy if exists playoff_series_read on playoff_series $q$;
end
$do$;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA)
    _recreate_views(exclude_playoffs=True)
    op.get_bind().exec_driver_sql(_SUPABASE)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DROP_SUPABASE)
    # Views first: they depend on player_game_lines.is_playoff, which is about to go.
    _recreate_views(exclude_playoffs=False)
    op.get_bind().exec_driver_sql(_SCHEMA_DOWN)
