import React from "react";

/**
 * NHA BoxScore — compact two-team game result with per-period scoring and
 * top scorers. home/away: { name, score, periods:[n,n,n], scorers:[{name, goals}] }
 */
export function BoxScore({ home, away, periods = ["P1", "P2", "P3"], style = {} }) {
  const winner = home.score === away.score ? null : home.score > away.score ? "home" : "away";
  const th = {
    padding: "6px 10px", fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)",
    letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", color: "var(--muted)",
    textAlign: "right", borderBottom: "1px solid var(--line)",
  };
  const td = { padding: "8px 10px", fontSize: "var(--text-sm)", textAlign: "right", fontVariantNumeric: "tabular-nums" };
  const TeamRow = ({ t, side }) => (
    <tr>
      <td style={{ ...td, textAlign: "left", fontWeight: "var(--weight-semibold)", color: "var(--text-body)" }}>
        {winner === side && <span style={{ color: "var(--green-600)", marginRight: 6 }}>▸</span>}
        {t.name}
      </td>
      {t.periods.map((p, i) => <td key={i} style={{ ...td, color: "var(--muted)" }}>{p}</td>)}
      <td style={{ ...td, fontWeight: "var(--weight-black)", fontFamily: "var(--font-display)", fontSize: "var(--text-lg)",
        color: winner === side ? "var(--green-700)" : "var(--text-body)" }}>
        {t.score}
      </td>
    </tr>
  );
  return (
    <div style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)", padding: 14, ...style }}>
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 10 }}>
        <thead>
          <tr>
            <th style={{ ...th, textAlign: "left" }}>Team</th>
            {periods.map((p) => <th key={p} style={th}>{p}</th>)}
            <th style={th}>F</th>
          </tr>
        </thead>
        <tbody>
          <TeamRow t={away} side="away" />
          <TeamRow t={home} side="home" />
        </tbody>
      </table>
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
        {[away, home].map((t) => (
          <div key={t.name}>
            <div style={{ fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)", letterSpacing: "var(--tracking-wide)",
              textTransform: "uppercase", color: "var(--muted)", marginBottom: 4 }}>{t.name} scorers</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {(t.scorers || []).map((s, i) => (
                <div key={i} style={{ fontSize: "var(--text-sm)", color: "var(--text-soft)" }}>
                  {s.name} <span style={{ color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>· {s.goals}G</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
