import { EmptyState } from "../ds";

/**
 * The Hall: inductees grouped by class year, with their citation and career line.
 * Phase 1's `hof` agent fills it in.
 */
export default function HallOfFame() {
  return (
    <section>
      <h2 style={{ marginBottom: 16 }}>Hall of Fame</h2>
      <EmptyState
        title="Nobody has been inducted yet"
        message="The commissioner inducts retired players; each class appears here with its citations and career totals."
      />
    </section>
  );
}
