/**
 * Commissioner controls for the draft: upload the prospect class, run the lottery
 * (and show the draw), open the room, and force a pick for the team on the clock.
 *
 * Renders nothing yet — Phase 1's `draft` agent owns this file. It is mounted in the
 * Offseason section of Commissioner.tsx already, so filling it in needs no edit there.
 */
export interface DraftPanelProps {
  season: number;
  onToast: (msg: string) => void;
}

export default function DraftPanel(_props: DraftPanelProps) {
  return null;
}
