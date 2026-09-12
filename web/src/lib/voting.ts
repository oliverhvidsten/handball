// Voting: the shapes the API serves and the small amount of presentation logic the
// Vote page, the Awards page and the commissioner panel all share.
//
// GET /voting/state is ONE document, served per caller: it carries the phase, the
// eligible candidates, and YOUR OWN saved ballots. Nobody's ballot but your own is
// ever in it, and the aggregate (how many managers have voted) is commissioner-only
// and arrives as null for everyone else. Results become public at /voting/results
// once the commissioner tallies.
//
// Everything here goes through lib/api.ts rather than Supabase: ballots and tallies
// are API-read by design (alembic 0015 grants clients nothing on them).

import { apiFetch } from "./api";

export type VoteKind = "award" | "allstar";
export type VoteStatus = "closed" | "open" | "tallied";

/** One name a ballot may carry: a player for five of the awards, a coach for the
 *  sixth. `own_team` is computed server-side against teams.owner_id -- the picker
 *  greys these out rather than letting a manager discover the rule by rejection. */
export interface Candidate {
  id: string;                 // players.id or coaches.id (uuid) -- what a ballot stores
  legacy_id: string | null;
  name: string;
  kind: "player" | "coach";
  position?: string | null;
  role?: string | null;       // coaches only: HC / OC / DC
  team_id: string;
  team_slug: string;
  team_name: string;
  own_team: boolean;
}

export interface AllStarLine {
  legacy_id: string;
  name: string;
  position: string;
  slot: "starter" | "bench";
  votes: number;
  goals: number;
  shots: number;
  saves: number;
  goals_allowed: number;
  performance: number;
}

export interface AllStarSide {
  conference: string;
  score: number;
  players: AllStarLine[];
}

export interface AllStarPick {
  player_id: string;
  legacy_id: string;
  name: string;
  position: string;
  slot: "starter" | "bench";
  votes: number;
}

export interface AllStarGame {
  season: number;
  played_at: string | null;
  home_conference: string;
  away_conference: string;
  home_score: number;
  away_score: number;
  went_to_overtime: boolean;
  scoring_log: string | null;
  home_roster: { conference: string; players: AllStarPick[] };
  away_roster: { conference: string; players: AllStarPick[] };
  box_score: { home: AllStarSide; away: AllStarSide };
}

export interface VotingState {
  season: number;
  status: Record<string, VoteStatus>;
  awards: string[];
  award_ballot_size: number;
  award_points: number[];
  all_star_ballot: Record<string, number>;
  conferences: string[];
  candidates: {
    awards: Record<string, Candidate[]>;
    all_star: Record<string, Record<string, Candidate[]>>;
  };
  my_ballots: {
    award: Record<string, string[]>;
    allstar: Record<string, Record<string, string[]>>;
  };
  ballot_counts: Record<string, Record<string, number>> | null;
  all_star_game: AllStarGame | null;
}

/** One line of a counted award: the evidence behind the name. */
export interface TallyRow {
  entity_id: string;
  entity_kind: "player" | "coach";
  name: string;
  legacy_id: string | null;
  position: string | null;
  team_slug: string | null;
  team_name: string | null;
  points: number;
  first_place_votes: number;
  rank: number | null;
}

export interface AwardResult {
  award: string;
  winner: TallyRow | null;
  tally: TallyRow[];
}

export interface VotingResults {
  season: number;
  seasons: number[];
  awards: AwardResult[];
  all_star_game: AllStarGame | null;
}

export function fetchVotingState(): Promise<VotingState> {
  return apiFetch<VotingState>("/voting/state", { method: "GET" });
}

export function fetchResults(season?: number): Promise<VotingResults> {
  const path = season == null ? "/voting/results" : `/voting/results?season=${season}`;
  return apiFetch<VotingResults>(path, { method: "GET" });
}

export function submitAwardBallot(award: string, rankedIds: string[]) {
  return apiFetch("/voting/awards/ballot", {
    method: "POST",
    body: JSON.stringify({ award, ranked_ids: rankedIds }),
  });
}

export function submitAllStarBallot(conference: string, ballot: Record<string, string[]>) {
  return apiFetch("/voting/all-star/ballot", {
    method: "POST",
    body: JSON.stringify({ conference, ballot }),
  });
}

// -- presentation ------------------------------------------------------------

/** Ballot positions are shown as places, not indexes: a voter ranks 1st..5th. */
export function placeLabel(index: number): string {
  const n = index + 1;
  if (n === 1) return "1st";
  if (n === 2) return "2nd";
  if (n === 3) return "3rd";
  return `${n}th`;
}

export const STATUS_LABEL: Record<string, string> = {
  closed: "Not open",
  open: "Open",
  tallied: "Counted",
};

// ds Tag tones.
export const STATUS_TONE: Record<string, string> = {
  closed: "neutral",
  open: "green",
  tallied: "blue",
};

/** The order positions are shown in everywhere -- matches ALL_STAR_BALLOT. */
export const POSITION_ORDER = ["Forward", "Midfielder", "Defense", "Goalie"];

export function positions(ballot: Record<string, number>): string[] {
  return POSITION_ORDER.filter((p) => p in ballot);
}

/** How a candidate is labelled in a picker: enough to tell two players apart
 *  without needing the roster open beside you. */
export function candidateLabel(c: Candidate): string {
  const where = c.team_name || c.team_slug;
  if (c.kind === "coach") return `${c.name} — ${where}${c.role ? ` (${c.role})` : ""}`;
  return `${c.name} — ${where}${c.position ? ` (${c.position})` : ""}`;
}

/** A ballot is complete when every slot the rules require is filled. Award ballots
 *  are allowed to be short (an unnamed place simply scores nothing), so this is only
 *  ever used to tell the voter what they have, never to block them. */
export function filled(ids: (string | null)[]): string[] {
  return ids.filter((i): i is string => !!i);
}
