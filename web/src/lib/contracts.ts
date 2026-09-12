import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "./api";

// The contracts API (api/contracts.py) over handball/extensions.py. Two dates and one
// binding action: is the extension window open, has the trade deadline passed, and who
// on my team may be extended for how much.

/** GET /contracts/windows -- where the league calendar sits on both dates. */
export interface ContractWindows {
  season: number | null;
  periods_run: number;
  extension_window_open: boolean;
  trade_deadline_passed: boolean;
  extension_window_after_period: number;
  trade_deadline_after_period: number;
}

/** One player who may be extended, with the ceiling on the deal. `max_value` is the
 *  hard cap against NEXT season's projected payroll -- the soft cap does not apply to
 *  your own player (Bird rights) -- and `max_term` is the league's five-year maximum
 *  less the years still to run. */
export interface EligiblePlayer {
  player_id: string;
  name: string;
  position: string;
  contract_term: number;
  contract_value: number;
  years_remaining: number;
  max_term: number;
  max_value: number;
}

export interface ExtendedPlayer {
  player_id: string;
  name: string;
  position: string;
  ext_term: number;
  ext_value: number;
  ext_signed_season: number;
}

/** GET /contracts/extensions/eligible?team= */
export interface ExtensionReport {
  team: string;
  team_name: string;
  season: number;
  extension_window_open: boolean;
  projected_next_payroll: number;
  hard_cap: number;
  max_contract_years: number;
  max_contract_value: number;
  players: EligiblePlayer[];
  extended: ExtendedPlayer[];
}

export function fetchWindows(): Promise<ContractWindows> {
  return apiFetch<ContractWindows>("/contracts/windows", { method: "GET" });
}

export function fetchEligible(team: string): Promise<ExtensionReport> {
  return apiFetch<ExtensionReport>(
    `/contracts/extensions/eligible?team=${encodeURIComponent(team)}`,
    { method: "GET" }
  );
}

/** Sign an extension. BINDING -- there is no cancel endpoint, and the rollover will
 *  put the player on the deal. */
export function offerExtension(
  team: string,
  playerId: string,
  term: number,
  value: number
): Promise<{ player_name: string; term: number; value: number; starts_season: number }> {
  return apiFetch("/contracts/extensions", {
    method: "POST",
    body: JSON.stringify({ team, player_id: playerId, term, value }),
  });
}

/** The calendar, fetched once per mount. Null while loading, and also when the call
 *  fails (not signed in, API asleep): a page that can't read the windows should render
 *  its normal self rather than a wrong banner. */
export function useContractWindows(): ContractWindows | null {
  const [w, setW] = useState<ContractWindows | null>(null);
  useEffect(() => {
    let cancelled = false;
    void fetchWindows()
      .then((next) => { if (!cancelled) setW(next); })
      .catch(() => { if (!cancelled) setW(null); });
    return () => { cancelled = true; };
  }, []);
  return w;
}

/** The extension board for one team, with a refresh for after a signing. Only fetched
 *  when `enabled` (the viewer owns the team) -- the endpoint is ownership-gated, and a
 *  403 is not something to show a visitor reading another team's roster. */
export function useExtensions(team: string, enabled: boolean) {
  const [report, setReport] = useState<ExtensionReport | null>(null);

  const load = useCallback(async () => {
    if (!team || !enabled) { setReport(null); return; }
    try {
      setReport(await fetchEligible(team));
    } catch {
      setReport(null);
    }
  }, [team, enabled]);

  useEffect(() => { void load(); }, [load]);
  return { report, refresh: load };
}

export function extensionError(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : "extension failed";
}

/** "Ext: 3y/$18M from next season" -- the one-line badge an extended player wears. */
export function extensionLabel(term: number, value: number): string {
  return `Ext: ${term}y/$${value}M from next season`;
}
