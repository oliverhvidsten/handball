import React from "react";

const KINDS = {
  offense: { label: "OFF", bg: "var(--stat-offense-bg)", fg: "var(--stat-offense-text)" },
  defense: { label: "DEF", bg: "var(--stat-defense-bg)", fg: "var(--stat-defense-text)" },
  goalie:  { label: "GK",  bg: "var(--stat-goalie-bg)",  fg: "var(--stat-goalie-text)" },
};

/**
 * NHA StatChip — a single player metric chip (Offense / Defense / Goalie).
 */
export function StatChip({ kind = "offense", value, label, style = {}, ...rest }) {
  const k = KINDS[kind] || KINDS.offense;
  return (
    <span
      title={kind}
      style={{
        display: "inline-flex",
        alignItems: "baseline",
        gap: 4,
        padding: "2px 7px",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-xs)",
        fontWeight: "var(--weight-bold)",
        color: k.fg,
        background: k.bg,
        borderRadius: "var(--radius-xs)",
        fontVariantNumeric: "tabular-nums",
        whiteSpace: "nowrap",
        ...style,
      }}
      {...rest}
    >
      <span style={{ opacity: 0.7, fontSize: "var(--text-2xs)", letterSpacing: "0.03em" }}>
        {label || k.label}
      </span>
      {value}
    </span>
  );
}
