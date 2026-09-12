/**
 * Commissioner controls for the Hall of Fame: pick a retired player (this season's
 * retirees first, then a search over all of them), write a citation, induct — and
 * rescind from the list of existing inductees.
 *
 * Renders nothing yet — Phase 1's `hof` agent owns this file. It is mounted in the
 * Offseason section of Commissioner.tsx already.
 */
export interface HallOfFamePanelProps {
  season: number;
  onToast: (msg: string) => void;
}

export default function HallOfFamePanel(_props: HallOfFamePanelProps) {
  return null;
}
