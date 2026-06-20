import React from "react";

/**
 * NHA TeamCard — a team tile for the Teams directory grid.
 * team: { name, abbr, wins, losses, ties, rank?, streak? }
 */
export function TeamCard({ team, yours = false, onClick, style = {} }) {
  const t = team || {};
  const record = `${t.wins ?? 0}-${t.losses ?? 0}${t.ties != null ? `-${t.ties}` : ""}`;
  return (
    <button
      onClick={onClick}
      style={{
        position: "relative",
        textAlign: "left",
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "14px 14px",
        background: yours ? "var(--green-50)" : "var(--surface-card)",
        border: `1px solid ${yours ? "var(--green-300)" : "var(--line)"}`,
        borderRadius: "var(--radius-lg)",
        boxShadow: "var(--shadow-xs)",
        cursor: onClick ? "pointer" : "default",
        transition: "border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out)",
        width: "100%",
      }}
      onMouseEnter={(e) => { if (onClick) { e.currentTarget.style.boxShadow = "var(--shadow-md)"; e.currentTarget.style.transform = "translateY(-1px)"; } }}
      onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "var(--shadow-xs)"; e.currentTarget.style.transform = "none"; }}
    >
      <span style={{
        flex: "none", width: 42, height: 42, borderRadius: "var(--radius-md)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: yours ? "var(--green-600)" : "var(--ink-900)", color: "#fff",
        fontFamily: "var(--font-display)", fontWeight: "var(--weight-black)", fontSize: "var(--text-md)",
        letterSpacing: "0.02em",
      }}>
        {t.abbr}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-md)", color: "var(--text-heading)" }}>
            {t.name}
          </span>
          {yours && (
            <span style={{ fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)", letterSpacing: "var(--tracking-wide)",
              textTransform: "uppercase", color: "var(--green-700)", background: "var(--green-100)", padding: "1px 6px", borderRadius: "var(--radius-xs)" }}>
              Your team
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 10, marginTop: 3, fontSize: "var(--text-sm)", color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
          <span style={{ fontWeight: "var(--weight-semibold)", color: "var(--text-soft)" }}>{record}</span>
          {t.rank != null && <span>#{t.rank} in league</span>}
          {t.streak && <span style={{ color: t.streak[0] === "W" ? "var(--green-700)" : "var(--red-text)" }}>{t.streak}</span>}
        </div>
      </div>
    </button>
  );
}
