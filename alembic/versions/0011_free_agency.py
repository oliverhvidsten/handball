"""offseason free agency: sealed offer rounds, RFA match windows, sequential bidding

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-01

The offseason market (handball/free_agency_rules.py + handball/free_agency.py). Each
offseason the commissioner opens a PERIOD; a period runs one or more ROUNDS; each round
opens with a sealed offer window and then resolves into per-player AUCTIONS:

    period (one per season)
      └── round 1..N        offers -> resolution -> complete
            └── auction     one per (round, player)
                  ├── offers        every team's live bid on that player
                  └── seats         the bidding rotation, worst initial offer first

Six tables, and no change to any existing one except the two noted at the bottom.

  - fa_periods / fa_rounds : the phase state. It cannot live on season_state, which
    models one background RUN, not a league phase. A partial unique index keeps at
    most one period open league-wide and one live round per period, so "is free
    agency happening?" -- which season_readiness and signing_service both ask -- is
    one cheap indexed lookup rather than an aggregate.

  - fa_auctions : one board per (round, player), created lazily when the FIRST offer
    on that player arrives. Creating it then rather than at round close is what lets
    fa_offers hang off a single parent, so "one live offer per team per player" is a
    partial unique index instead of a service-enforced hope. `restricted` and
    `rights_team_id` are SNAPSHOT onto the row at birth: the free-agent list the
    managers were reading said "restricted, rights: Foxes", and the deal must not
    change under them mid-round. The LEADING offer is deliberately NOT stored -- it
    is derived from the live offers on read, so it cannot go stale when an offer is
    voided mid-auction. `turn_team_id` and `no_raise_streak` ARE stored, being
    genuinely stateful.

  - fa_offers : append-only. An edit, a raise and a match all mark the old row
    'superseded' and insert a new one (supersedes_id chains them). Three things fall
    out of that: an honest audit trail, a correct seniority rule (re-offering loses
    your place in line), and a hot path that never locks an old row. The bigserial
    `id` IS the submission-order tiebreak in the league's contract ranking -- a
    sequence cannot produce ties the way a timestamp can.

  - fa_auction_seats : the bidding rotation, frozen at round close. It cannot be
    re-derived from the offers, because the offers change every time somebody raises.
    unique (auction_id, turn_order) makes a well-formed rotation a DB guarantee.

  - fa_actions : append-only audit. fa_offers already records every amount, so this
    exists for what it does NOT record: commissioner interventions (force-forfeit,
    award, reopen) and phase transitions, with a reason attached. In a league with a
    human referee that is the most important sentence the system can produce.
    `action` is text rather than an enum on purpose -- the log is descriptive, not a
    state machine, and adding a logged action should not need a migration.

RLS. Everything is enabled; only fa_offers is withheld from `authenticated`. During an
offer round the offers ARE the sealed information, and a policy whose visibility turns
on a parent row's status is exactly the kind of conditional that leaks a whole market
when it is subtly wrong -- so offers are served through the API, which redacts per
caller, the same way season_state is. The boards themselves (periods, rounds, auctions,
seats, actions) are public by construction: an auction only exists once its round has
closed, so there is nothing sealed left in it, and everyone watching the bidding is the
point.

Two changes to existing objects:

  - player_public gains `restricted_free_agent`, so the free-agent list can badge who
    carries a match right. Recreating the view drops its grants, so the Supabase-guarded
    block re-applies them -- same dance as 0003/0008/0010.

  - A one-time backfill: `restricted_free_agent` and `rookie_contract` are cleared for
    every player with years_in_league > 0. Both flags default True at creation and are
    only cleared by a non-rookie Player.update_contract, so every player imported before
    contracts were modelled still looks like a first-contract rookie -- which would hand
    their team a match right on essentially the whole league in the first offer round.
    Nobody currently in the league has been through a modelled rookie deal, so the first
    genuine restricted free agents are the players drafted from here on.

Downgrade drops the new structures and restores the previous view definition; like
0007/0008/0009 it does not attempt to un-backfill rows (there is no way to tell a
cleared flag from one that was already false).
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


_TYPES = """
create type fa_period_status  as enum ('open','closed');
create type fa_round_status   as enum ('offers','resolution','complete');
create type fa_auction_status as enum ('collecting','matching','bidding',
                                       'awaiting_award','resolved','void');
create type fa_auction_outcome as enum ('rfa_kept','rfa_matched','sole_offer','bid_won',
                                        'commissioner_award','no_offers','all_forfeited',
                                        'player_ineligible');
create type fa_offer_status   as enum ('open','withdrawn','superseded','forfeited',
                                       'lost','won','void');
create type fa_seat_state     as enum ('active','forfeited');
"""

_SCHEMA = """
create table fa_periods (
  id uuid primary key default gen_random_uuid(),
  season int not null unique,
  status fa_period_status not null default 'open',
  opened_at timestamptz not null default now(),
  opened_by uuid references managers(user_id),
  closed_at timestamptz,
  closed_by uuid references managers(user_id),
  check ((status = 'closed') = (closed_at is not null))
);
-- at most one open period in the whole league (constant-expression partial index)
create unique index ux_fa_periods_one_open on fa_periods ((1)) where status = 'open';

create table fa_rounds (
  id uuid primary key default gen_random_uuid(),
  period_id uuid not null references fa_periods(id) on delete cascade,
  round_number int not null check (round_number >= 1),
  status fa_round_status not null default 'offers',
  offers_count int,                 -- live offers at close; 0 == the round that drew nothing
  auctions_count int,
  opened_at timestamptz not null default now(),
  closed_at timestamptz,            -- when the OFFER window shut (offers -> resolution)
  completed_at timestamptz,
  unique (period_id, round_number)
);
create index ix_fa_rounds_period on fa_rounds (period_id);
create unique index ux_fa_rounds_one_live on fa_rounds (period_id) where status <> 'complete';

create table fa_auctions (
  id bigserial primary key,
  round_id uuid not null references fa_rounds(id) on delete cascade,
  player_id uuid not null references players(id) on delete cascade,
  status fa_auction_status not null default 'collecting',
  outcome fa_auction_outcome,

  -- snapshot at birth, so eligibility cannot drift under a live board
  restricted boolean not null default false,
  rights_team_id uuid references teams(id) on delete set null,

  match_offer_id bigint,            -- in 'matching': the offer the rights team may match
  turn_team_id uuid references teams(id) on delete set null,
  no_raise_streak int not null default 0 check (no_raise_streak >= 0),
  waiting_since timestamptz,        -- when the current turn / match window opened

  winning_team_id uuid references teams(id) on delete set null,
  winning_offer_id bigint,
  signed_term int check (signed_term is null or signed_term >= 1),
  signed_value int check (signed_value is null or signed_value >= 0),
  award_reason text,

  created_at timestamptz not null default now(),
  resolved_at timestamptz,

  unique (round_id, player_id),
  check ((status in ('resolved','void')) = (resolved_at is not null))
);
create index ix_fa_auctions_round  on fa_auctions (round_id);
create index ix_fa_auctions_player on fa_auctions (player_id);
-- "whose turn is it, for my teams?" -- run on every page load
create index ix_fa_auctions_turn   on fa_auctions (turn_team_id) where turn_team_id is not null;
-- a player may sit in at most ONE live auction league-wide; two boards could each
-- resolve into a contract for the same player
create unique index ux_fa_auctions_live_player on fa_auctions (player_id)
  where status in ('collecting','matching','bidding','awaiting_award');

create table fa_offers (
  id bigserial primary key,         -- ALSO the submission-order tiebreak in the ranking
  auction_id bigint not null references fa_auctions(id) on delete cascade,
  team_id uuid not null references teams(id) on delete cascade,
  term int not null check (term >= 1),
  value int not null check (value >= 0),
  status fa_offer_status not null default 'open',
  origin text,                      -- null = sealed submission; else 'match' | 'raise'
  is_rfa_match boolean not null default false,
  supersedes_id bigint references fa_offers(id) on delete set null,
  submitted_at timestamptz not null default now(),
  submitted_by uuid references managers(user_id),
  resolved_at timestamptz
);
create index ix_fa_offers_auction on fa_offers (auction_id);
-- the hottest read in the system: "all of MY live offers", on every exposure check
create index ix_fa_offers_team on fa_offers (team_id) where status = 'open';
-- a team speaks with ONE voice per player
create unique index ux_fa_offers_open on fa_offers (auction_id, team_id) where status = 'open';

-- circular reference: the auction points at offers that always exist by then
alter table fa_auctions
  add constraint fk_fa_auctions_match_offer
      foreign key (match_offer_id) references fa_offers(id) on delete set null,
  add constraint fk_fa_auctions_winning_offer
      foreign key (winning_offer_id) references fa_offers(id) on delete set null;

create table fa_auction_seats (
  id bigserial primary key,
  auction_id bigint not null references fa_auctions(id) on delete cascade,
  team_id uuid not null references teams(id) on delete cascade,
  turn_order int not null check (turn_order >= 0),   -- 0 == worst initial offer, acts first
  state fa_seat_state not null default 'active',
  forfeit_reason text,                               -- voluntary | commissioner | illegal
  forfeited_at timestamptz,
  unique (auction_id, team_id),
  unique (auction_id, turn_order),
  check ((state = 'forfeited') = (forfeited_at is not null))
);
create index ix_fa_seats_team on fa_auction_seats (team_id);

create table fa_actions (
  id bigserial primary key,
  period_id uuid not null references fa_periods(id) on delete cascade,
  round_id uuid references fa_rounds(id) on delete cascade,
  auction_id bigint references fa_auctions(id) on delete cascade,
  team_id uuid references teams(id) on delete set null,
  actor_user_id uuid references managers(user_id) on delete set null,
  by_commissioner boolean not null default false,
  action text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index ix_fa_actions_period  on fa_actions (period_id, id);
create index ix_fa_actions_auction on fa_actions (auction_id, id);

alter table fa_periods       enable row level security;
alter table fa_rounds        enable row level security;
alter table fa_auctions      enable row level security;
alter table fa_offers        enable row level security;
alter table fa_auction_seats enable row level security;
alter table fa_actions       enable row level security;
"""

# The first offer round would otherwise treat the entire imported league as
# first-contract players. See the module docstring.
_BACKFILL = """
update players set restricted_free_agent = false, rookie_contract = false
where years_in_league > 0;
"""

_DROP = """
drop table if exists fa_actions;
drop table if exists fa_auction_seats;
alter table if exists fa_auctions
  drop constraint if exists fk_fa_auctions_match_offer,
  drop constraint if exists fk_fa_auctions_winning_offer;
drop table if exists fa_offers;
drop table if exists fa_auctions;
drop table if exists fa_rounds;
drop table if exists fa_periods;
drop type if exists fa_seat_state;
drop type if exists fa_offer_status;
drop type if exists fa_auction_outcome;
drop type if exists fa_auction_status;
drop type if exists fa_round_status;
drop type if exists fa_period_status;
"""

# The boards are public; the sealed offers are not granted at all and are served
# through the API (see the module docstring).
_PUBLIC_FA_TABLES = "fa_periods, fa_rounds, fa_auctions, fa_auction_seats, fa_actions"

_SUPABASE = f"""
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;              -- not Supabase (no auth roles); RLS is on, nothing else to do
  end if;

  -- Supabase default privileges auto-grant SELECT on every new table to anon and
  -- authenticated, so deny first and grant back precisely (same as 0002).
  execute $q$ revoke all on {_PUBLIC_FA_TABLES}, fa_offers from anon $q$;
  execute $q$ revoke all on {_PUBLIC_FA_TABLES}, fa_offers from authenticated $q$;

  execute $q$ grant select on {_PUBLIC_FA_TABLES} to authenticated $q$;
  execute $q$ create policy fa_periods_read on fa_periods
              for select to authenticated using (true) $q$;
  execute $q$ create policy fa_rounds_read on fa_rounds
              for select to authenticated using (true) $q$;
  execute $q$ create policy fa_auctions_read on fa_auctions
              for select to authenticated using (true) $q$;
  execute $q$ create policy fa_seats_read on fa_auction_seats
              for select to authenticated using (true) $q$;
  execute $q$ create policy fa_actions_read on fa_actions
              for select to authenticated using (true) $q$;
  -- fa_offers: no grant, no policy. Sealed; read through the API only.
end
$do$;
"""

_DROP_SUPABASE = """
do $do$
begin
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    return;
  end if;
  execute $q$ drop policy if exists fa_periods_read on fa_periods $q$;
  execute $q$ drop policy if exists fa_rounds_read on fa_rounds $q$;
  execute $q$ drop policy if exists fa_auctions_read on fa_auctions $q$;
  execute $q$ drop policy if exists fa_seats_read on fa_auction_seats $q$;
  execute $q$ drop policy if exists fa_actions_read on fa_actions $q$;
end
$do$;
"""

# player_public column lists (mirror 0010, + restricted_free_agent).
_COLS_NEW = ("id, legacy_id, team_id, rights_team_id, name, position, slot_group, slot_position, "
             "slot_order, age, offense, defense, goalie_skill, is_injured, contract_term, "
             "contract_value, years_remaining, restricted_free_agent, retired")
_COLS_OLD = ("id, legacy_id, team_id, rights_team_id, name, position, slot_group, slot_position, "
             "slot_order, age, offense, defense, goalie_skill, is_injured, contract_term, "
             "contract_value, years_remaining, retired")

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
    op.get_bind().exec_driver_sql(_TYPES)
    op.get_bind().exec_driver_sql(_SCHEMA)
    op.get_bind().exec_driver_sql(_BACKFILL)
    op.get_bind().exec_driver_sql(_SUPABASE)
    _recreate_player_public(_COLS_NEW)


def downgrade() -> None:
    _recreate_player_public(_COLS_OLD)
    op.get_bind().exec_driver_sql(_DROP_SUPABASE)
    op.get_bind().exec_driver_sql(_DROP)
