# Rules alignment plan

Bring the league in line with the rulebook. Decisions already made with the commissioner
(do not reopen them):

| Decision | Answer |
|---|---|
| Draft format | Live, turn-based by managers, with an auto-pick clock (same lazy-sweep pattern as free agency) |
| Lottery | Weighted draw for #1, remove the winner, redraw for #2, ... through all 16 slots |
| Award points | 10-7-5-3-1 for placements 1-5 |
| All-Star ballot | Positional: per conference, 5 Forwards, 5 Midfielders, 5 Defense, 2 Goalies (=17). Top 3/3/3/1 by votes start, the rest are bench |
| Extensions | Tack on after the current year: current salary stays this season, new term/value start next season; total remaining <= 5; hard cap checked on projected next-season payroll |
| Prospects | Commissioner uploads a names file (one per line, or CSV with `Name` and optional `Position`), same format `draft_simulator.load_prospect_names` reads |
| Stat awards | Keep Top Scorer and Top Goalie as auto-computed stat titles; MVP and Rookie of the Year become VOTED (no longer auto-computed) |
| Not changing | Same-owner trades stay allowed and auto-accepted. No trade salary-matching window. No home-court advantage in the sim. Coaches stay inert |

Assumptions I made (flag to the commissioner if they look wrong):
- A protected pick that lands inside its protection **reverts to the original team and the obligation ends** (no rollover to a later year). Only round-1 picks can carry protections.
- Round 2 of the draft: the 16 non-playoff teams pick 33-48 in reverse record order, the 16 playoff teams pick 49-64 in reverse record order. That is the only reading of "no better than the 17th pick of the second round".
- Coach of the Year ballots may name any coach holding an open tenure (HC, OC or DC).
- Eleventh Man of the Year ballots may name any player sitting in a BENCH slot at ballot time.
- Undrafted prospects become ordinary unrestricted free agents when the draft closes.
- Award voting opens when period 5 finishes and must be tallied before `/season/advance`. All-Star voting opens when period 3 finishes; the game must be played before period 4 can run.
- The trade deadline: no trade may be proposed or accepted once period 4 has run, until `/season/advance` opens the next league year. Already-accepted trades may still be approved (period 5 already requires the queue to be clear).

## How the work is split

Everything is built on the existing stack: Alembic schema, `handball/*` pure rules + SQL
modules, FastAPI routes, React pages. Read `TODO.md` "Done" for the conventions the codebase
already uses (pure rules module + SQL module, lock ordering, lazy sweeps, commissioner gating,
`season_readiness`).

**Phase 0 (serial, one agent): schema + scaffolding.** All migrations, all new constants, the
shared API helpers, empty routers, empty pages and panels. After this lands, feature agents never
need to add a migration or touch `api/main.py`, `App.tsx` routing, or `simulation_vars.py`.

**Phase 1 (parallel, four agents, separate worktrees + separate databases):**

| Agent | Scope | Owns these files |
|---|---|---|
| draft | Lottery, draft order (incl. protection resolution), prospect upload, live draft room, slotted rookie contracts, FA gated on draft completion | `handball/draft.py`, `handball/draft_rules.py`, `api/draft.py`, `web/src/pages/Draft.tsx`, `web/src/components/commissioner/DraftPanel.tsx`, `web/src/lib/draft.ts`, `tests/test_draft*.py` |
| voting | Award ballots + tally, All-Star ballots + tally + exhibition game, results pages | `handball/voting.py`, `handball/voting_rules.py`, `handball/all_star.py`, `api/voting.py`, `web/src/pages/Vote.tsx`, `web/src/pages/Awards.tsx`, `web/src/components/commissioner/VotingPanel.tsx`, `web/src/lib/voting.ts`, `tests/test_voting*.py`, `tests/test_all_star*.py` |
| contracts | Contract extensions (rules, SQL, rollover application, cap report), trade deadline | `handball/extensions.py`, `api/contracts.py`, `web/src/lib/contracts.ts`, `tests/test_extensions*.py`, `tests/test_trade_deadline*.py`; small edits to `handball/trade_service.py`, `handball/offseason.py`, `handball/signing_service.py` (`team_cap_report`), `handball/contract_admin.py` (audit excludes extended players), `web/src/pages/Roster.tsx`, `web/src/pages/Trades.tsx` |
| hof | Hall of Fame; draft-pick protections attached at trade time | `handball/hall_of_fame.py`, `api/hall_of_fame.py`, `web/src/pages/HallOfFame.tsx`, `web/src/components/commissioner/HallOfFamePanel.tsx`, `web/src/lib/hallOfFame.ts`, `tests/test_hall_of_fame*.py`; small edits to `handball/trade_service.py` (protection on pick assets), `web/src/pages/Trades.tsx`, `web/src/pages/PlayerDetail.tsx` |

Files two agents both touch (`trade_service.py`, `Trades.tsx`, `offseason.py`): keep edits
minimal and localized, add new functions rather than reshaping existing ones. The overseer
resolves merges.

Rules for every agent:
- Do NOT add Alembic migrations. If the schema is wrong or missing something, stop and report it.
- Do NOT edit `api/main.py`, `web/src/App.tsx`, `handball/simulation_vars.py`, or `tests/test_api.py`. Your routes go in your router file; your tests go in your own test files (copy the `_clean_db` truncation fixture pattern from `tests/test_api.py`).
- Use only your own database. First thing: write `.env` in your worktree root containing `HANDBALL_DB_URL=postgresql+psycopg://postgres:dev@localhost:5432/<your db>`. `handball/db.py` loads it. Verify with `python3 -m alembic current` before running tests.
- Run the FULL suite (`python3 -m pytest -q`) and `cd web && npm run build` before you finish. Both must be clean.
- Commit on your worktree branch when done, with a message in the style of the existing log. Do not push.
- Update `TODO.md` "Done" with a paragraph in the existing style describing what you built and the decisions in it.

## Phase 0 spec

### `api/deps.py`
Move out of `api/main.py`, keeping `main.py` importing them so nothing else changes:
`engine`, `active_season()`, `require_commissioner()`, `require_owns()`, `require_owns_strict()`,
`team_uuid()`, `queue_clear()`. Create `api/draft.py`, `api/voting.py`, `api/contracts.py`,
`api/hall_of_fame.py`, each exporting an empty `router = APIRouter()`, and `include_router` all
four in `main.py`.

### `handball/simulation_vars.py` additions
```
DRAFT_ROUNDS = 2                      # already present
DRAFT_TURN_LIMIT_HOURS = 24
LOTTERY_WEIGHTS = (25, 20, 15, 10, 5, 5, 5, 5, 2, 2, 1, 1, 1, 1, 1, 1)   # worst team first
ROOKIE_SCALE = (                      # (first overall pick, last overall pick, years, $M/yr)
    (1, 10, 5, 5), (11, 20, 5, 4), (21, 32, 5, 3), (33, 48, 2, 2), (49, 64, 2, 1),
)
AWARDS = ("Most Valuable Player", "Rookie of the Year", "Defensive Player of the Year",
          "Eleventh Man of the Year", "Most Improved Player", "Coach of the Year")
AWARD_BALLOT_SIZE = 5
AWARD_POINTS = (10, 7, 5, 3, 1)
AWARD_VOTING_OPENS_AFTER_PERIOD = 5
ALL_STAR_BALLOT = {"Forward": 5, "Midfielder": 5, "Defense": 5, "Goalie": 2}
ALL_STAR_AFTER_PERIOD = 3
EXTENSION_WINDOW_AFTER_PERIOD = 1
EXTENSION_ELIGIBLE_YEARS_REMAINING = 1
TRADE_DEADLINE_AFTER_PERIOD = 4
```
Also move `ROOKIE_CONTRACT_YEARS/SALARY` out of `postseason.py`? No: leave them; the draft agent
retires the in-memory `DraftService` path's flat deal in favor of `ROOKIE_SCALE`.

### Migrations (follow the 0011/0012 pattern: raw SQL, working downgrade, Supabase-only RLS enable guarded by the role check, deny-all to clients since all new reads go through the API)

**0014_draft**
- `draft_picks` + `player_id uuid null references players(id)`, `picked_at timestamptz`, `auto_pick boolean not null default false`, `protection_top_n int null`, `protection_outcome text null` (`conveyed` | `reverted`).
- `trade_assets` + `protection_top_n int null` (only meaningful on pick assets).
- `draft_lotteries (season int primary key, standings_order jsonb not null, results jsonb null, seed int null, drawn_at timestamptz null)`. `standings_order` = the 16 non-playoff team ids worst-first, written at rollover; `results` = ordered list of `{team_id, slot}` written when drawn.
- `draft_state (season int primary key, status text not null default 'pending', current_overall int null, turn_started_at timestamptz null, updated_at timestamptz not null default now())`. status: `pending` | `lottery_drawn` | `open` | `complete`.
- `draft_prospects (id uuid primary key default gen_random_uuid(), season int not null, ord int not null, name text not null, position player_position not null, age int, offense float8, defense float8, goalie_skill float8, player_json jsonb not null, player_id uuid null references players(id), unique(season, ord))`.

**0015_voting**
- `ballots (id bigserial primary key, season int not null, kind text not null, category text not null, voter_user_id uuid not null, payload jsonb not null, submitted_at timestamptz not null default now(), unique(season, kind, category, voter_user_id))`. kind: `award` (category = award name, payload = ordered list of up to 5 ids) | `allstar` (category = conference name, payload = `{position: [ids]}`).
- `award_tallies (season int, award text, entity_kind text, entity_id uuid, points int not null, first_place_votes int not null default 0, rank int, primary key(season, award, entity_id))`. entity_kind: `player` | `coach`.
- `awards`: make `player_id` nullable, add `coach_id uuid null references coaches(id)`, add check that exactly one of the two is set.
- `voting_status (season int, kind text, status text not null default 'closed', closed_at timestamptz, primary key(season, kind))`. status: `closed` | `open` | `tallied`.
- `all_star_games (id uuid primary key default gen_random_uuid(), season int not null unique, played_at timestamptz not null default now(), home_conference text not null, away_conference text not null, home_score int not null, away_score int not null, went_to_overtime boolean not null default false, scoring_log text, home_roster jsonb not null, away_roster jsonb not null, box_score jsonb not null)`.

**0016_extensions**
- `players` + `ext_term int null`, `ext_value int null`, `ext_signed_season int null`.
- Recreate the `player_public` view with the three new columns appended (copy 0011's column list and its grant).

**0017_hall_of_fame**
- `hall_of_fame (id bigserial primary key, player_id uuid not null unique references players(id), inducted_season int not null, citation text, inducted_at timestamptz not null default now())`.

### Frontend scaffolding
- Routes + nav entries: `/vote` (Vote), `/awards` (Awards), `/hall-of-fame` (HallOfFame). `/draft` exists.
- Placeholder pages `Vote.tsx`, `Awards.tsx`, `HallOfFame.tsx` that render a heading and an `EmptyState`.
- `web/src/components/commissioner/DraftPanel.tsx`, `VotingPanel.tsx`, `HallOfFamePanel.tsx`: each exports a component taking `{ season: number; onToast: (msg: string) => void }` and rendering nothing yet. Mount `DraftPanel` and `HallOfFamePanel` in the Offseason section of `Commissioner.tsx`, `VotingPanel` after "Run the league".

### Tests
Add the new tables to `_TABLES` in `tests/test_api.py` (`ballots voting_status award_tallies all_star_games draft_lotteries draft_state draft_prospects hall_of_fame`). Full suite and `npm run build` clean. Migrate the dev DB to head.

## Phase 1 specs

### draft
**Order.** Change `offseason._seed_draft_order` so that at rollover (season S -> S+1) it writes, for season S+1's picks:
- Round 1, picks 17-32: the 16 playoff teams by playoff result, from `playoff_series` for season S: first-round losers 17-24 (worse record first), second-round losers 25-28, conference-final losers 29-30, runner-up 31, champion 32. Records = `ranked_team_ids` (best->worst, passed in before the reset).
- Round 1, picks 1-16: `pick_number` NULL; write `draft_lotteries.standings_order` = the 16 non-playoff teams worst-first; `draft_state` row `pending`.
- Round 2: non-playoff teams 33-48 worst-first, playoff teams 49-64 worst-first.
`POST /draft/lottery` (commissioner) draws all 16 slots sequentially with `LOTTERY_WEIGHTS` (weight i belongs to the i-th worst remaining team; renormalize after each removal), seeded and reproducible, assigns `pick_number` 1-16, stores `results`, then **resolves protections**: for every season-S+1 round-1 pick with `protection_top_n` set and `holder != original`, if `pick_number <= protection_top_n` set `holder_team_id = original_team_id`, `protection_outcome = 'reverted'`, else `'conveyed'`. Status -> `lottery_drawn`. `POST /draft/lottery` is refused if the draft for that season is `open` or `complete`.

**Prospects.** `POST /draft/prospects` (commissioner, multipart or JSON text body) parses the file with `draft_simulator.load_prospect_names`, generates each prospect once with `create_draft_player` (random position when absent), stores `to_dict()` in `player_json` and the visible ratings in the columns. Replaces any un-drafted prospect list for that season; refused once the draft is `open`. Prospects need at least 64 rows to open.

**Room.** `POST /draft/open` (commissioner, needs `lottery_drawn` + prospects) sets `open`, `current_overall = 1`, `turn_started_at = now()`. `POST /draft/pick {prospect_id}` by a manager who owns the holder of the current pick (`require_owns_strict`), or by the commissioner. Signing: build the `Player` from `player_json`, `update_contract(years, salary, rookie=True)` from `ROOKIE_SCALE` by overall pick, insert via `PostgresTeamRepository` conventions with `team_id = holder`, unplaced (`slot_group` null, the same as a pool signing into a full roster; `try_rebuild_layout` afterwards). Mark the pick `used`, `player_id`, `picked_at`; advance `current_overall`; after 64 -> `complete`, and materialize every undrafted prospect as a free agent (`team_id` null, `restricted_free_agent = false`, no contract). Turn clock: `GET /draft/state` sweeps first; a turn older than `DRAFT_TURN_LIMIT_HOURS` auto-picks the best available by overall (`auto_pick = true`). Locks: `draft_state` row FOR UPDATE for every pick. `GET /draft/state` returns order, made picks, prospects remaining with ratings, whose turn, `turn_seconds_left`.

**Gates.** `POST /free-agency/periods` refuses until the draft for the active season is `complete` (one-line call into `handball.draft.assert_complete` from `api/main.py` is the ONE main.py edit permitted, for this agent only). `season_readiness` gets a check: the draft must be complete before period 1.

**UI.** `Draft.tsx` becomes the draft room: order with team, holder, protections, pick made; prospect board sortable by rating with a Pick button on your turn; countdown. `DraftPanel`: upload prospects, run lottery (show results), open draft, force-pick for the team on the clock.

**Retire** the in-memory `postseason.DraftService` flat deal: make it take the scale so `LeagueOperations.run_draft` matches the rulebook too. Tests in `tests/test_postseason.py` will need updating.

### voting
**Awards.** `handball/voting_rules.py` (pure): validate a ballot (right length, no duplicates, no entity from a team the voter owns, eligibility per award as in the assumptions above), tally (points by placement, tie-break by first-place votes then by name). `handball/voting.py` (SQL): `submit_ballot` (upsert, one per voter per category; refused unless `voting_status` is `open`), `open_voting(kind)`, `tally(kind)` (writes `award_tallies`, writes winners to `awards` with `player_id` or `coach_id`, status `tallied`). Voting opens automatically when `periods_run == AWARD_VOTING_OPENS_AFTER_PERIOD` (the state read flips `closed` -> `open`); the commissioner tallies. `/season/advance` must refuse until award voting is `tallied` for the season (this agent's ONE permitted `api/main.py` edit, plus removing MVP/ROY from `offseason._compute_awards` while keeping Top Scorer / Top Goalie).

**All-Star.** Ballot per voter per conference: `ALL_STAR_BALLOT` counts by position, players must be rostered on that conference's teams (`league_structure`), none from teams the voter owns. Opens when `periods_run == ALL_STAR_AFTER_PERIOD`. `POST /voting/all-star/play` (commissioner): tally per position (top 3/3/3/1 start, next 2/2/2/1 bench), build each conference's `Team` with `roster_layout.canonical_team` over `Player`s loaded through the repository, play ONE game with `orchestration.GameSimulatorAdapter(allow_tie=False)`, store it in `all_star_games` only (never `games` or `player_game_lines`; box score from the result's player lines into `box_score` jsonb). Home conference alternates by season parity. `POST /periods/run` for period 4 refuses until the All-Star game exists for the season (add a `season_readiness`-style helper `all_star.assert_played` and call it; this is the second permitted `main.py` edit for this agent).

**UI.** `Vote.tsx`: shows whichever ballot is open (awards: six ranked pickers with search, excluding own teams; all-star: per-conference positional pickers), with the voter's saved ballot. `Awards.tsx`: voted results by season with full tallies, plus the All-Star game result and box score. `VotingPanel`: status per kind, ballot counts, Tally / Play buttons.

### contracts
**Extensions.** `handball/extensions.py`: `offer_extension(engine, team_slug, player_legacy_id, term, value)` for the team's own manager. Window: active season `periods_run == EXTENSION_WINDOW_AFTER_PERIOD` and `run_status != 'running'`. Eligible: rostered on that team, `years_remaining == EXTENSION_ELIGIBLE_YEARS_REMAINING`, no existing `ext_term`. Validate: `1 <= term <= MAX_CONTRACT_YEARS - years_remaining`, `salary_cap.validate_contract(term, value)`, and projected next-season payroll `<= HARD_CAP` where projected = sum of `contract_value` for the team's players with `years_remaining >= 2` + sum of `ext_value` for players with an extension (including this one). Bird rights: the soft cap does not apply. Lock team then player. Write `ext_term`, `ext_value`, `ext_signed_season`. Binding: no cancel endpoint.
**Rollover.** In `offseason.advance_season`, after `_age_all_players` and before `_process_free_agency`, `_apply_extensions`: every non-retired player with `years_remaining <= 0` and `ext_term` set goes onto the new deal through the domain path (`Player.update_contract(ext_term, ext_value, rookie=False)`, via the repository so `restricted_free_agent`/`rookie_contract` clear the way any new contract does), then clear the three ext columns. `contract_admin`'s "expiring next rollover" count must exclude extended players. `signing_service.team_cap_report` gains `projected_next_payroll` and `extension_window_open`.
**API.** `POST /contracts/extensions`, `GET /contracts/extensions/eligible?team=` (players who can be extended, with the max term/value available).
**Trade deadline.** `trade_service.propose_trade` and `accept_trade` raise `TradeError("the trade deadline has passed")` when the active season's `periods_run >= TRADE_DEADLINE_AFTER_PERIOD`. (`approve` stays allowed.) `GET /season/state` already exists; add `trade_deadline_passed` to `team_cap_report` or expose through your router as `GET /contracts/windows` returning `{extension_window_open, trade_deadline_passed}` for the UI.
**UI.** `Roster.tsx` (own team, window open): an Extend action on eligible players with term/value inputs and the ceiling shown; extended players show "Ext: Ny/$M from next season". `Trades.tsx`: banner + disabled form when the deadline has passed.

### hof
**Hall of Fame.** `handball/hall_of_fame.py`: `induct(engine, legacy_id, season, citation)` (player must be `retired`), `rescind(engine, legacy_id)`, `inductees(engine)` with career totals aggregated from `player_game_lines` (regular season and playoffs separately). `POST /hall-of-fame`, `DELETE /hall-of-fame/{legacy_id}` (commissioner), `GET /hall-of-fame` (any manager). `HallOfFame.tsx`: inductees grouped by class year with citation and career line. `PlayerDetail.tsx`: a Hall of Fame badge. `HallOfFamePanel`: pick from retired players (this season's retirees first, then a search over all retirees), citation field, induct; list with rescind.
**Pick protections.** `trade_service.propose_trade`: `picks_out`/`picks_in` entries may be either a pick id (unchanged) or `{"pick_id", "protection_top_n"}`; validate 1-32, round-1 pick only, store on `trade_assets.protection_top_n`; on `approve_trade`, copy to `draft_picks.protection_top_n` (and `protection_outcome = null`). The `TradeBody` model in `api/main.py` must accept both shapes: that is this agent's ONE permitted `main.py` edit. `Trades.tsx`: a protection input beside each pick asset; show protections on existing picks. Resolution at lottery time is the draft agent's job; do not implement it.

## Merge order
hof, contracts, voting, draft (smallest blast radius first). Overseer re-runs the full suite and
`npm run build` after each merge, then `graphify update .`, then applies 0014-0017 to production
per DEPLOY.md before deploying code.
