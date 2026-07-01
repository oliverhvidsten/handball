import React from "react";

/**
 * NHA DraftPickBoard — 10 year-boxes of draft-pick ownership, for a Team
 * Roster page. `years`: number[] (the years to render, including ones with
 * no picks). `picks`: flat array of { id, season, round, originalTeam }
 * already filtered to the picks a single team holds -- originalTeam is
 * whichever team's draft slot the pick is (the holding team's own name for
 * an untraded pick, or the other team's name if acquired). Rounds within a
 * year are sorted ascending here (round 1 before round 2) regardless of
 * query order.
 */
export function DraftPickBoard({ years = [], picks = [], style = {} }) {
  const byYear = new Map(years.map((y) => [y, []]));
  for (const p of picks) {
    if (byYear.has(p.season)) byYear.get(p.season).push(p);
  }
  for (const list of byYear.values()) list.sort((a, b) => a.round - b.round);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
        gap: 12,
        ...style,
      }}
    >
      {years.map((year) => {
        const list = byYear.get(year) || [];
        return (
          <div
            key={year}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 7,
              padding: "8px 10px",
              background: "var(--surface-card)",
              border: "1px solid var(--line)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <h4
              style={{
                fontFamily: "var(--font-body)",
                fontSize: "var(--text-2xs)",
                fontWeight: "var(--weight-bold)",
                letterSpacing: "var(--tracking-wide)",
                textTransform: "uppercase",
                color: "var(--muted)",
                margin: 0,
              }}
            >
              {year}
            </h4>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {list.length === 0 ? (
                <div
                  style={{
                    padding: "10px 8px",
                    textAlign: "center",
                    fontSize: "var(--text-xs)",
                    color: "var(--muted)",
                    border: "1px dashed var(--line-strong)",
                    borderRadius: "var(--radius-sm)",
                  }}
                >
                  No picks
                </div>
              ) : (
                list.map((p) => (
                  <div key={p.id} style={{ fontSize: "var(--text-sm)" }}>
                    <span style={{ fontWeight: "var(--weight-bold)", color: "var(--text-heading)" }}>
                      {p.originalTeam}
                    </span>{" "}
                    <span style={{ color: "var(--muted)" }}>Round {p.round}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
