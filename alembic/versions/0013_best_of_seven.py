"""playoff rounds become best-of-seven series

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-01

0012 decided each matchup with ONE game, and said in as many words that a best-of-N
would replace `playoff_series.game_id` with a series join and leave the rest of the
schema alone. This is that change.

  - games.playoff_series_id / series_game : a playoff game now belongs to a series
    and knows its place in it (1..7). ON DELETE CASCADE, so dropping a series takes
    its games (and their player_game_lines) with it -- clear_playoffs and a rolled-
    back round both rely on that.

  - playoff_series.high_wins / low_wins : the series score, incremented as each game
    lands. Derivable from the games, but stored because it is what makes a run
    RESUMABLE: a round is not one transaction (each game is minutes of simulation),
    so a worker that dies mid-series must be able to pick the series up at 2-1
    rather than replaying it from 0-0.

  - playoff_series.game_id is dropped, superseded by the above.

The unique index on games(season, week, home, away) does not bind here: playoff games
carry a NULL week, and NULLs are distinct under a unique index -- which matters now
that the same pair plays up to seven times.

No data migration: 0012 shipped days ago and no bracket has been seeded on it. The
downgrade is honest about being lossy for a multi-game series.
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


_SCHEMA = r"""
alter table games
  add column playoff_series_id uuid references playoff_series(id) on delete cascade,
  add column series_game int;
create index ix_games_playoff_series on games (playoff_series_id);

alter table playoff_series
  add column high_wins int not null default 0,
  add column low_wins  int not null default 0;

alter table playoff_series drop column game_id;
"""

_SCHEMA_DOWN = r"""
alter table playoff_series add column game_id uuid references games(id) on delete set null;
-- Lossy by nature: a series has many games and this column holds one. Take the
-- decider (the highest-numbered game), which is the closest single-game analogue.
update playoff_series ps set game_id = (
  select g.id from games g where g.playoff_series_id = ps.id
  order by g.series_game desc nulls last limit 1
);
alter table playoff_series drop column low_wins, drop column high_wins;
drop index if exists ix_games_playoff_series;
alter table games drop column series_game, drop column playoff_series_id;
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_SCHEMA_DOWN)
