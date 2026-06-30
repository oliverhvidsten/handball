// Coaching role helpers. The DB stores the compact enum codes (coach_role:
// 'HC' | 'OC' | 'DC'); the UI shows full labels. Kept in one place so the
// Coaches index, coach detail, and the Roster strip stay consistent.

export type CoachRole = "HC" | "OC" | "DC";

export const ROLE_ORDER: CoachRole[] = ["HC", "OC", "DC"];

export const ROLE_LABEL: Record<string, string> = {
  HC: "Head Coach",
  OC: "Offensive Coordinator",
  DC: "Defensive Coordinator",
};

// Tag tones (see ds Tag): HC stands out, coordinators are cooler.
export const ROLE_TONE: Record<string, string> = {
  HC: "green",
  OC: "blue",
  DC: "amber",
};

/** Human season range for a tenure: "2025–present" / "2024–2026" / "2025". */
export function seasonRange(start: number, end: number | null): string {
  if (end == null) return `${start}–present`;
  return start === end ? `${start}` : `${start}–${end}`;
}
