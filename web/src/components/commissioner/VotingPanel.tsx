/**
 * Commissioner controls for voting: the status of each ballot kind, how many managers
 * have voted, the Tally button, and Play for the All-Star exhibition.
 *
 * Renders nothing yet — Phase 1's `voting` agent owns this file. It is mounted after
 * the "Run the league" section of Commissioner.tsx already.
 */
export interface VotingPanelProps {
  season: number;
  onToast: (msg: string) => void;
}

export default function VotingPanel(_props: VotingPanelProps) {
  return null;
}
