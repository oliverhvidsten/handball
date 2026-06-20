import React from "react";

/**
 * NHA StatCard — a big labelled metric for dashboards (Season, Games Played, etc).
 */
export function StatCard({
  label,
  value,
  sub = null,
  delta = null,
  deltaTone = "green",
  accent = "var(--green-500)",
  icon = null,
  style = {},
}) {
  const deltaColors = {
    green: "var(--green-700)",
    red: "var(--red-text)",
    neutral: "var(--muted)",
  };
  return (
    <div
      style={{
        position: "relative",
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-lg)",
        padding: "16px 18px",
        boxShadow: "var(--shadow-sm)",
        overflow: "hidden",
        ...style,
      }}
    >
      <span style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 4, background: accent }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{
          fontFamily: "var(--font-body)", fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)",
          letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", color: "var(--muted)",
        }}>
          {label}
        </span>
        {icon}
      </div>
      <div style={{
        fontFamily: "var(--font-display)", fontSize: "var(--text-3xl)", fontWeight: "var(--weight-black)",
        lineHeight: 1, color: "var(--text-heading)", fontVariantNumeric: "tabular-nums",
      }}>
        {value}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 7 }}>
        {sub && <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>{sub}</span>}
        {delta != null && (
          <span style={{ fontSize: "var(--text-sm)", fontWeight: "var(--weight-semibold)", color: deltaColors[deltaTone] }}>
            {delta}
          </span>
        )}
      </div>
    </div>
  );
}
