import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api";

// Mirrors GET /hall-of-fame (api/hall_of_fame.py): every inductee, with the career
// line split regular-season / playoff (aggregated server-side from
// player_game_lines -- see handball/hall_of_fame.py).
export interface CareerLine {
  games: number;
  goals: number;
  shots: number;
  saves: number;
  goals_allowed: number;
  performance: number;
}
export interface Inductee {
  legacy_id: string;
  name: string;
  position: string;
  inducted_season: number;
  citation: string | null;
  inducted_at: string;
  regular_season: CareerLine;
  playoff: CareerLine;
}
export interface HallOfFameState {
  inductees: Inductee[];
}

/** Every inductee, newest class first. Re-fetches whenever `signal` changes, so a
 * panel that just inducted/rescinded someone can force a refresh by bumping a
 * counter it owns. */
export function useHallOfFame(signal?: unknown) {
  const [hof, setHof] = useState<HallOfFameState | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await apiFetch<HallOfFameState>("/hall-of-fame", { method: "GET" });
      setHof(next);
    } catch {
      setHof(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load, signal]);

  return { hof, loading, refresh: load };
}

/** Inductees grouped by class year, newest class first -- what both HallOfFame.tsx
 * and HallOfFamePanel's list want. */
export function byClassYear(inductees: Inductee[]): [number, Inductee[]][] {
  const groups = new Map<number, Inductee[]>();
  for (const i of inductees) {
    const g = groups.get(i.inducted_season);
    if (g) g.push(i); else groups.set(i.inducted_season, [i]);
  }
  return [...groups.entries()].sort((a, b) => b[0] - a[0]);
}

export async function induct(playerId: string, season: number, citation: string): Promise<Inductee> {
  return apiFetch<Inductee>("/hall-of-fame", {
    method: "POST",
    body: JSON.stringify({ player_id: playerId, season, citation: citation || null }),
  });
}

export async function rescind(legacyId: string): Promise<void> {
  await apiFetch(`/hall-of-fame/${encodeURIComponent(legacyId)}`, { method: "DELETE" });
}
