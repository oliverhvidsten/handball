import React from "react";
import { Tag } from "../forms/Tag.jsx";

/**
 * NHA DraftOrderRow — one pick in the draft-order list.
 * pick: { overall, round, inRound, team, abbr, viaTeam?, player?, current? }
 */
export function DraftOrderRow({ pick, style = {} }) {
  const p = pick || {};
  const made = !!p.player;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "9px 12px",
        background: p.current ? "var(--green-50)" : "var(--surface-card)",
        border: `1px solid ${p.current ? "var(--green-300)" : "var(--line)"}`,
        borderRadius: "var(--radius-sm)",
        ...style,
      }}
    >
      <span style={{
        flex: "none", width: 38, height: 38, borderRadius: "var(--radius-sm)",
        display: "inline-flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        background: p.current ? "var(--green-600)" : "var(--surface-3)", color: p.current ? "#fff" : "var(--text-soft)",
      }}>
        <span style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)", fontSize: "var(--text-md)", lineHeight: 1 }}>{p.overall}</span>
      </span>

      <div style={{ flex: "none", width: 48, fontFamily: "var(--font-mono)", fontSize: "var(--text-xs)", color: "var(--muted)" }}>
        {p.round}.{String(p.inRound).padStart(2, "0")}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-md)", color: "var(--text-heading)" }}>
            {p.team}
          </span>
          {p.viaTeam && (
            <span style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}>via {p.viaTeam}</span>
          )}
          {p.current && <Tag tone="green" solid size="sm">On the clock</Tag>}
        </div>
        {made && (
          <div style={{ marginTop: 2, fontSize: "var(--text-sm)", color: "var(--text-soft)" }}>
            <span style={{ fontWeight: 600 }}>{p.player.name}</span>{" "}
            <span style={{ color: "var(--muted)" }}>· {p.player.position}</span>
          </div>
        )}
      </div>

      {!made && !p.current && (
        <span style={{ flex: "none", fontSize: "var(--text-xs)", color: "var(--muted)" }}>—</span>
      )}
    </div>
  );
}
