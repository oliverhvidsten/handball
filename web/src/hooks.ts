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
