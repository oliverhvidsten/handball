import { EmptyState } from "../ds";

/**
 * The ballot box. Phase 1's `voting` agent turns this into the page that renders
 * whichever ballot is currently open — six ranked award pickers, or the positional
 * All-Star ballot for your conference — along with the ballot you already submitted.
 */
export default function Vote() {
  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Vote</h2>
      <EmptyState
        title="No ballot is open"
        message="All-Star voting opens at the break, and award voting opens once the regular season is complete. Your ballot will appear here."
      />
    </section>
  );
}
