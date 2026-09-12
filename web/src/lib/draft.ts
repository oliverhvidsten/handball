import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "./api";

// Mirrors GET /draft/state (api/draft.py). One document drives the whole room: the
// order with every pick made, the board of prospects still available, whose turn it
// is, and how long they have left. The read is also the CLOCK -- it sweeps an
// expired turn before it answers -- which is why the page polls it rather than
// reading draft_picks from Supabase the way it used to.

export interface DraftPick {
  id: string;
  round: number;
  overall: number | null;        // null before the lottery numbers the first round
  used: boolean;
  auto_pick: boolean;
  picked_at: string | null;
  protection_top_n: number | null;
  protection_outcome: "conveyed" | "reverted" | null;
  team: string;                  // slug of the team that HOLDS the pick
  team_name: string;
  original: string;              // slug of the team whose slot it is
  original_name: string;
  player_id: string | null;
  player_name: string | null;
  player_position: string | null;
  term: number | null;
  value: number | null;
}

export interface DraftProspect {
  id: string;
  ord: number;                   // the uploaded file's order; the auto-pick tiebreak
  name: string;
  position: string;
  age: number | null;
  offense: number;
  defense: number;
  goalie_skill: number;
  rating: number;                // offense + defense + goalie_skill, as the rules score it
  player_id: string | null;
}

export interface DraftClock {
  overall: number;
  round: number;
  team: string;
  team_name: string;
  original_name: string;
  turn_started_at: string | null;
  // Computed server-side (simulation_vars.DRAFT_TURN_LIMIT_HOURS), so the countdown
  // doesn't drift with the viewer's clock.
  turn_deadline: string | null;
  turn_seconds_left: number | null;
}

export interface LotterySlot {
  slot: number;
  team_id: string;
  team: string;
  team_name: string;
}

export interface DraftLottery {
  season: number;
  seed: number | null;
  drawn_at: string | null;
  pool_size: number;
  results: LotterySlot[];
}

/** A turn the state read itself expired (the poll is the clock). */
export interface DraftSweep {
  overall: number;
  team: string;
  team_name: string;
  player_name: string;
}

export type DraftStatus = "pending" | "lottery_drawn" | "open" | "complete";

export interface DraftState {
  season: number;
  status: DraftStatus | null;    // null == this league has no draft for the season
  current_overall: number | null;
  picks: number;
  picks_made: number;
  auto_picks: number;
  on_the_clock: DraftClock | null;
  turn_limit_hours: number;
  order: DraftPick[];
  board: DraftProspect[];
  lottery: DraftLottery | null;
  swept: DraftSweep[];
  is_commissioner: boolean;
  your_teams: string[];          // team UUIDs this manager owns
}

export const DRAFT_PHASE: Record<DraftStatus, string> = {
  pending: "Waiting on the lottery",
  lottery_drawn: "Lottery drawn — the room has not opened",
  open: "On the clock",
  complete: "Complete",
};

/** Whole hours/minutes left on a turn, or null when nobody is on the clock. */
export function formatTimeLeft(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  if (seconds <= 0) return "overdue";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m left` : `${m}m left`;
}

export function problemsOf(e: unknown): string[] {
  if (e instanceof ApiError) return e.problems ?? [e.message];
  return [e instanceof Error ? e.message : "action failed"];
}

/** The rookie scale, mirrored client-side so the board can show what a pick pays
 *  before it is made. Must match simulation_vars.ROOKIE_SCALE. */
const ROOKIE_SCALE: [number, number, number, number][] = [
  [1, 10, 5, 5],
  [11, 20, 5, 4],
  [21, 32, 5, 3],
  [33, 48, 2, 2],
  [49, 64, 2, 1],
];

export function rookieDeal(overall: number | null): { term: number; value: number } | null {
  if (overall == null) return null;
  const band = ROOKIE_SCALE.find(([first, last]) => overall >= first && overall <= last);
  return band ? { term: band[2], value: band[3] } : null;
}

export type SortKey = "rating" | "offense" | "defense" | "goalie_skill" | "age" | "ord";

/** The board, sorted. `ord` is the uploaded order (and the auto-pick tiebreak); every
 *  other key sorts high-to-low with `ord` breaking ties, so the order is stable. */
export function sortBoard(board: DraftProspect[], key: SortKey): DraftProspect[] {
  const sorted = [...board];
  if (key === "ord") return sorted.sort((a, b) => a.ord - b.ord);
  return sorted.sort((a, b) => (b[key] ?? 0) - (a[key] ?? 0) || a.ord - b.ord);
}

const LIVE_MS = 10_000;      // a turn is hours long; this only has to feel live
const QUIET_MS = 30_000;     // nothing has changed in a while: back off

/**
 * Poll the draft state while the room is open, and not otherwise -- a finished (or
 * unopened) draft costs exactly one request per mount. Polling pauses while the tab
 * is hidden and refetches on refocus. The same shape as useFreeAgencyState, and for
 * the same reason: this poll is what advances the turn clock.
 */
export function useDraftState() {
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [quiet, setQuiet] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await apiFetch<DraftState>("/draft/state", { method: "GET" });
      setDraft((prev) => {
        setQuiet(prev?.current_overall === next.current_overall
          && prev?.picks_made === next.picks_made);
        return next;
      });
      setError(null);
    } catch (e) {
      setError(problemsOf(e)[0]);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const live = draft?.status === "open";
  useEffect(() => {
    if (!live) return;
    const tick = () => { if (document.visibilityState === "visible") void load(); };
    const id = window.setInterval(tick, quiet ? QUIET_MS : LIVE_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [live, quiet, load]);

  return { draft, refresh: load, error };
}
