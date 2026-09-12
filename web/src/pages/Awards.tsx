import { EmptyState } from "../ds";

/**
 * Results: the voted awards by season with their full tallies, plus the All-Star
 * game and its box score. Phase 1's `voting` agent fills it in.
 */
export default function Awards() {
  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Awards</h2>
      <EmptyState
        title="No awards yet"
        message="Award winners and the vote behind them appear here once the commissioner tallies the ballots."
      />
    </section>
  );
}
