import { useEffect, useState } from "react";
import { supabase } from "./lib/supabase";
import { useAuth } from "./auth";

/** Team abbreviation from a name: initials, capped at 3 (matches the kit). */
export function abbrev(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  return words.map((w) => w[0]).join("").slice(0, 3).toUpperCase();
}

/**
 * Count of trades awaiting THIS manager's response: status 'proposed' and the
 * receiving team is one this manager owns. (RLS already scopes visible trades to
 * the manager's teams / commissioner, so this is a safe client-side count.)
 */
export function usePendingTradeCount(): number {
  const { teams, session } = useAuth();
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!session || teams.length === 0) {
      setCount(0);
      return;
    }
    const ids = teams.map((t) => t.id);
    supabase
      .from("trades")
      .select("id", { count: "exact", head: true })
      .eq("status", "proposed")
      .in("to_team_id", ids)
      .then(({ count }) => setCount(count ?? 0));
  }, [session, teams]);

  return count;
}

/**
 * Count of free-agency boards waiting on THIS manager: an auction where one of their
 * teams is on the clock, or a restricted match window where one of their teams holds
 * the rights. A manager may own several teams, so it is a single `.in()` over all of
 * them.
 *
 * A head-only count against a table that is EMPTY outside an offseason, so the poll
 * costs almost nothing when no market is running -- but bidding turns take hours and
 * arrive while you are looking at another page, which is exactly the case the trade
 * badge (fetch-on-mount only) doesn't cover. Polling pauses while the tab is hidden
 * and refetches on refocus.
 */
const TURN_POLL_MS = 60_000;

export function useYourTurnCount(): number {
  const { teams, session } = useAuth();
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!session || teams.length === 0) {
      setCount(0);
      return;
    }
    const ids = teams.map((t) => t.id);
    const read = () => {
      if (document.visibilityState !== "visible") return;
      void Promise.all([
        supabase.from("fa_auctions").select("id", { count: "exact", head: true })
          .eq("status", "bidding").in("turn_team_id", ids),
        supabase.from("fa_auctions").select("id", { count: "exact", head: true })
          .eq("status", "matching").in("rights_team_id", ids),
      ]).then(([bids, matches]) => setCount((bids.count ?? 0) + (matches.count ?? 0)));
    };
    read();
    const id = window.setInterval(read, TURN_POLL_MS);
    document.addEventListener("visibilitychange", read);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", read);
    };
  }, [session, teams]);

  return count;
}
