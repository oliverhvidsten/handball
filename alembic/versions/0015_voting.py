"""award and All-Star voting: ballots, tallies, and the exhibition game

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-12

Awards were computed: `offseason._compute_awards` read the leaderboard and named an
MVP. The rulebook says the managers vote, so the league needs somewhere to put a
ballot, somewhere to put the count, and a phase flag saying whether the polls are
open. Same shape as free agency: a status row per phase, an append-once record per
participant, and a derived result the commissioner commits.

  - ballots is ONE table for both kinds of vote (`kind` = 'award' | 'allstar'), keyed
    unique on (season, kind, category, voter). A manager gets one ballot per award and
    one per conference, and re-submitting REPLACES it -- an upsert, unlike fa_offers,
    because a ballot is a statement of preference and not a promise anyone can rely
    on, so there is no seniority to lose and no audit trail worth keeping. The ranked
    list itself is jsonb: a 5-slot award ballot and a positional All-Star ballot are
    different shapes, and normalising them into a ballot_entries table would buy
    nothing -- nothing ever queries INSIDE a ballot except the tally, which reads all
    of them anyway.

  - award_tallies is the count, written by the tally and then read forever: it is the
    evidence behind a name in `awards`. `entity_kind` exists because Coach of the Year
    is voted on coaches, so the tally cannot key on players alone. `points` and
    `first_place_votes` are both stored because the tiebreak needs the second one, and
    a results page that shows only the winner is not a results page.

  - awards itself gains `coach_id` and loses NOT NULL on `player_id`, with a check
    that exactly one is set -- the same one-of-two shape trade_assets already uses for
    (player_id, draft_pick_id). The stat titles (Top Scorer, Top Goalie) keep writing
    player rows exactly as they do today.

  - voting_status is the phase, per (season, kind). It is what makes voting OPEN
    automatically -- the state read flips 'closed' to 'open' once enough periods have
    run -- and 'tallied' is what /season/advance waits for.

  - all_star_games holds the exhibition and nothing else touches it. The game is
    deliberately NOT written to `games`/`player_game_lines`: those feed the standings,
    the leaderboards and the MVP race, and an exhibition that counted toward any of
    them would be a bug in every one of them. The price is that its box score has no
    home to be joined from, so it is carried on the row as jsonb.

RLS is enabled on all four new tables and nothing is granted to clients. Ballots are
the obvious case -- a vote in progress is sealed for the same reason a sealed offer is
-- and the rest follow the API-read rule the league has settled on for anything with
a phase attached. `awards` keeps the public grant 0004 gave it.

Downgrade is lossy in one honest place: restoring NOT NULL on awards.player_id means
deleting the coach awards, which have nowhere to go in the old shape.
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


_SCHEMA = r"""
create table ballots (
  id bigserial primary key,
  season int not null,
  kind text not null check (kind in ('award','allstar')),
  category text not null,            -- award name, or conference name
  voter_user_id uuid not null,       -- auth.users id, as managers.user_id
  payload jsonb not null,
  submitted_at timestamptz not null default now(),
  unique (season, kind, category, voter_user_id)
);
create index ix_ballots_season_kind on ballots (season, kind);

create table award_tallies (
  season int not null,
  award text not null,
  entity_kind text not null check (entity_kind in ('player','coach')),
  entity_id uuid not null,
  points int not null,
  first_place_votes int not null default 0,
  rank int,
  primary key (season, award, entity_id)
);

alter table awards alter column player_id drop not null;
alter table awards add column coach_id uuid references coaches(id) on delete cascade;
alter table awards add constraint awards_one_recipient
  check ((player_id is null) <> (coach_id is null));

create table voting_status (
  season int not null,
  kind text not null,
  status text not null default 'closed' check (status in ('closed','open','tallied')),
  closed_at timestamptz,
  primary key (season, kind)
);

create table all_star_games (
  id uuid primary key default gen_random_uuid(),
  season int not null unique,
  played_at timestamptz not null default now(),
  home_conference text not null,
  away_conference text not null,
  home_score int not null,
  away_score int not null,
  went_to_overtime boolean not null default false,
  scoring_log text,
  home_roster jsonb not null,
  away_roster jsonb not null,
  box_score jsonb not null
);

alter table ballots        enable row level security;
alter table award_tallies  enable row level security;
alter table voting_status  enable row level security;
alter table all_star_games enable row level security;
"""

# Restoring NOT NULL means the coach awards cannot be represented; see the docstring.
_SCHEMA_DOWN = r"""
drop table if exists all_star_games;
drop table if exists voting_status;
drop table if exists award_tallies;
drop table if exists ballots;

alter table awards drop constraint if exists awards_one_recipient;
delete from awards where player_id is null;
alter table awards drop column if exists coach_id;
alter table awards alter column player_id set not null;
"""

_NEW_TABLES = "ballots, award_tallies, voting_status, all_star_games"

_SUPABASE = f"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;              -- not Supabase (no auth roles); RLS is on, nothing else to do
  end if;
  -- Deny first (Supabase auto-grants SELECT on new tables), and grant nothing back:
  -- an open ballot is sealed, and the results are served through the API.
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
