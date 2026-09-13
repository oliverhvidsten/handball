import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "./api";

// Mirrors GET /free-agency/state (api/main.py). One document drives the whole page:
// the phase, every live board, and -- per team this manager owns -- their cap room,
// their own (still sealed) offers, and the boards waiting on them.

export interface FAOffer {
  id: number;
  term: number;
  value: number;
  auction_id: number;
  auction_status: string;
  player_id: string;
  player_name: string;
  own_player: boolean;
}
export interface FASeat {
  turn_order: number;
  state: "active" | "forfeited";
  team: string;
  team_name: string;
}
export interface FABoardOffer {
  id: number;
  team: string;
  team_name: string;
  term: number;
  value: number;
}
export interface FAAuction {
  id: number;
  status: "collecting" | "matching" | "bidding" | "awaiting_award";
  restricted: boolean;
  no_raise_streak: number;
  turn_team_id: string | null;
  turn_team: string | null;            // slug of the team on the clock
  turn_team_name: string | null;
  rights_team_id: string | null;
  rights_team: string | null;          // slug of the Bird-rights holder
  waiting_since: string | null;
  // The turn clock (simulation_vars.FA_TURN_LIMIT_HOURS). Computed server-side, so
  // the countdown doesn't drift with the viewer's clock. Null on a board that isn't
  // waiting on a team -- 'awaiting_award' waits on the commissioner and never expires.
  turn_deadline: string | null;
  turn_seconds_left: number | null;
  player_id: string;
  player_name: string;
  position: string;
  offers: FABoardOffer[];
  seats: FASeat[];
}
export interface FAActionRequired {
  auction_id: number;
  player_id: string;
  player_name: string;
  kind: "bid" | "rfa_match";
  waiting_since: string | null;
}
export interface FACap {
  payroll: number;
  cap_room: number;
  roster_size: number;
  max_roster: number;
  roster_spots: number;
  max_outside_offer: number;
  max_own_offer: number;
  limits: { max_contract_years: number; max_contract_value: number; min_contract_value: number };
}
export interface FATeam {
  id: string;
  slug: string;
  name: string;
  cap: FACap;
  offers: FAOffer[];
  action_required: FAActionRequired[];
}
export interface FASigning {
  player_id: string;
  player_name: string;
  team: string;
  term: number;
  value: number;
  outcome: string;
  round_number: number;
}
export interface FAState {
  period: { id: string; season: number; status: string } | null;
  // Whether the pool takes minimum ($0) signings right now. Shut while the market is
  // open, and in the offseason until this season's market has closed -- expired
  // players are offered real contracts there before anyone can pick them up for free.
  pool?: { open: boolean; phase: "open" | "market_open" | "awaiting_market"; reason: string | null };
  round?: { id: string; round_number: number; status: string; offers_count: number | null } | null;
  auctions?: FAAuction[];
  signings?: FASigning[];
  teams: FATeam[];
  your_turn_count: number;
  is_commissioner: boolean;
  turn_limit_hours?: number;
  // Turns the read itself expired (the poll is the clock -- see GET /free-agency/state).
  swept?: { auction_id: number; player: string; team: string; team_name: string; was: string }[];
}

// GET /free-agency/history -- the public record of every board that has FINISHED,
// losing offers included. Sealed rounds are not in it: a board only appears once it
// has resolved or been voided.
export interface FAHistoryOffer {
  id: number;
  team: string;
  team_name: string;
  term: number;
  value: number;
  status: "open" | "withdrawn" | "superseded" | "forfeited" | "lost" | "won" | "void";
  origin: string;
  is_rfa_match: boolean;
}
export interface FAHistoryBoard {
  id: number;
  round_number: number;
  status: "resolved" | "void";
  outcome: string | null;
  restricted: boolean;
  signed_term: number | null;
  signed_value: number | null;
  player_id: string;
  player_name: string;
  position: string;
  winning_team: string | null;
  winning_team_name: string | null;
  rights_team_name: string | null;
  offers: FAHistoryOffer[];
}
export interface FAHistory {
  period: { id: string; season: number; status: string } | null;
  boards: FAHistoryBoard[];
}

/** Whole hours/minutes left on a turn, or null when the board has no clock. */
export function formatTimeLeft(seconds: number | null): string | null {
  if (seconds == null) return null;
  if (seconds <= 0) return "overdue";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m left` : `${m}m left`;
}

/**
 * The public record of finished boards. Fetched once per mount and again whenever
 * `signal` changes -- pass the live board count, so the record refreshes exactly when
 * a board leaves the live list rather than on a timer of its own.
 */
export function useFreeAgencyHistory(signal: unknown) {
  const [history, setHistory] = useState<FAHistory | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await apiFetch<FAHistory>("/free-agency/history", { method: "GET" });
        if (!cancelled) setHistory(next);
      } catch {
        if (!cancelled) setHistory(null);
      }
    })();
    return () => { cancelled = true; };
  }, [signal]);
  return history;
}

export function problemsOf(e: unknown): string[] {
  if (e instanceof ApiError) return e.problems ?? [e.message];
  return [e instanceof Error ? e.message : "action failed"];
}

/** The league's contract ranking, mirrored client-side so the UI can disable a bid
 *  before the server has to reject it: higher salary wins, then longer term. The
 *  submission tiebreak is server-side only (it needs offer ids). */
export function beatsLeader(term: number, value: number, lead: { term: number; value: number }): boolean {
  return value > lead.value || (value === lead.value && term > lead.term);
}

export function leadingOffer(a: FAAuction): FABoardOffer | null {
  return a.offers.length ? a.offers[0] : null;   // the API already sorts best-first
}

const LIVE_MS = 10_000;      // turns take hours; this only has to feel live
const QUIET_MS = 30_000;     // nothing has changed in a while: back off

/**
 * Poll the free-agency state while a period is open, and not otherwise -- with no
 * market running this costs exactly one request per mount. Polling pauses while the
 * tab is hidden and refetches immediately on refocus, which is what actually makes
 * the page feel live without hammering a free-tier API for days on end.
 */
export function useFreeAgencyState() {
  const [fa, setFa] = useState<FAState | null>(null);
  const [quiet, setQuiet] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await apiFetch<FAState>("/free-agency/state", { method: "GET" });
      setFa((prev) => {
        setQuiet(JSON.stringify(prev?.auctions) === JSON.stringify(next.auctions));
        return next;
      });
    } catch {
      setFa(null);      // not signed in / API asleep: the page falls back to the pool
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const live = fa?.period != null;
  useEffect(() => {
    if (!live) return;
    const tick = () => { if (document.visibilityState === "visible") void load(); };
    const id = window.setInterval(tick, quiet ? QUIET_MS : LIVE_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, [live, quiet, load]);

  return { fa, refresh: load };
}
