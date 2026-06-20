import React from "react";
import { StatusPill } from "../data/StatusPill.jsx";
import { Button } from "../forms/Button.jsx";

/**
 * NHA TradeRow — one trade in the Trades list.
 * trade: { fromTeam, toTeam, status, playersOut?:[], playersIn?:[], date?, internal? }
 * actions: [{ label, variant?, onClick }]
 */
export function TradeRow({ trade, actions = [], style = {} }) {
  const t = trade || {};
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "12px 14px",
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-md)",
        ...style,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-md)", color: "var(--text-heading)" }}>
            {t.fromTeam}
          </span>
          <span style={{ color: "var(--muted)", fontSize: "var(--text-base)" }}>→</span>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-md)", color: "var(--text-heading)" }}>
            {t.toTeam}
          </span>
          <StatusPill status={t.status} style={{ marginLeft: 2 }} />
          {t.internal && (
            <span style={{ fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)", letterSpacing: "var(--tracking-wide)",
              textTransform: "uppercase", color: "var(--purple-text)", background: "var(--purple-soft)", padding: "1px 6px", borderRadius: "var(--radius-xs)" }}>
              Internal
            </span>
          )}
        </div>
        {(t.playersOut || t.playersIn) && (
          <div style={{ display: "flex", gap: 16, marginTop: 5, fontSize: "var(--text-sm)", color: "var(--muted)", flexWrap: "wrap" }}>
            {t.playersOut && <span><span style={{ color: "var(--text-soft)", fontWeight: 600 }}>Out:</span> {t.playersOut.join(", ") || "—"}</span>}
            {t.playersIn && <span><span style={{ color: "var(--text-soft)", fontWeight: 600 }}>In:</span> {t.playersIn.join(", ") || "—"}</span>}
          </div>
        )}
        {t.date && <div style={{ marginTop: 4, fontSize: "var(--text-xs)", color: "var(--muted)" }}>{t.date}</div>}
      </div>

      {actions.length > 0 && (
        <div style={{ display: "flex", gap: 7, flex: "none" }}>
          {actions.map((a, i) => (
            <Button key={i} size="sm" variant={a.variant || "secondary"} onClick={a.onClick}>{a.label}</Button>
          ))}
        </div>
      )}
    </div>
  );
}
