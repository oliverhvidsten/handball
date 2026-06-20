import React from "react";

/**
 * NHA TeamSwitcher — active-team context control for managers who own
 * multiple teams. Sits in the TopNav (dark chrome).
 *
 * teams: [{ abbr, name, wins, losses, ties, alerts?, rank? }]
 * activeAbbr: the currently selected team's abbr
 */
export function TeamSwitcher({ teams = [], activeAbbr, onChange, style = {} }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  const active = teams.find((t) => t.abbr === activeAbbr) || teams[0] || {};
  const rec = (t) => `${t.wins ?? 0}-${t.losses ?? 0}${t.ties != null ? `-${t.ties}` : ""}`;

  return (
    <div ref={ref} style={{ position: "relative", ...style }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          display: "inline-flex", alignItems: "center", gap: 8, height: 36, padding: "0 10px",
          background: open ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.06)",
          border: "1px solid rgba(255,255,255,0.14)", borderRadius: "var(--radius-sm)",
          cursor: "pointer", color: "#fff", fontFamily: "var(--font-body)",
        }}
      >
        <span style={{
          width: 24, height: 24, borderRadius: "var(--radius-xs)", background: "var(--green-500)", color: "var(--ink-950)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 10,
        }}>{active.abbr}</span>
        <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", lineHeight: 1.1 }}>
          <span style={{ fontSize: "var(--text-sm)", fontWeight: 700 }}>{active.name}</span>
          <span style={{ fontSize: "var(--text-2xs)", color: "rgba(255,255,255,0.55)", fontVariantNumeric: "tabular-nums" }}>{rec(active)}</span>
        </span>
        <span style={{ color: "rgba(255,255,255,0.6)", fontSize: 11, transform: open ? "rotate(180deg)" : "none", transition: "transform var(--dur-fast)" }}>▾</span>
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", left: 0, minWidth: 240, zIndex: 60,
          background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-md)",
          boxShadow: "var(--shadow-lg)", overflow: "hidden",
        }}>
          <div style={{
            padding: "8px 12px", fontSize: "var(--text-2xs)", fontWeight: 700, letterSpacing: "var(--tracking-wide)",
            textTransform: "uppercase", color: "var(--muted)", borderBottom: "1px solid var(--line)",
          }}>
            Your teams · {teams.length}
          </div>
          {teams.map((t) => {
            const on = t.abbr === activeAbbr;
            return (
              <button
                key={t.abbr}
                onClick={() => { onChange && onChange(t.abbr); setOpen(false); }}
                className="nha-row"
                style={{
                  width: "100%", textAlign: "left", display: "flex", alignItems: "center", gap: 10,
                  padding: "9px 12px", background: on ? "var(--green-50)" : "var(--surface-card)",
                  border: "none", borderBottom: "1px solid var(--line)", cursor: "pointer",
                }}
              >
                <span style={{
                  width: 28, height: 28, borderRadius: "var(--radius-sm)", flex: "none",
                  background: on ? "var(--green-600)" : "var(--ink-900)", color: "#fff",
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 10,
                }}>{t.abbr}</span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block", fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-body)" }}>{t.name}</span>
                  <span style={{ display: "block", fontSize: "var(--text-xs)", color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>{rec(t)}{t.rank ? ` · #${t.rank}` : ""}</span>
                </span>
                {t.alerts > 0 && (
                  <span style={{
                    flex: "none", minWidth: 18, height: 18, padding: "0 5px", borderRadius: "var(--radius-pill)",
                    background: "var(--red-600)", color: "#fff", fontFamily: "var(--font-mono)", fontSize: "var(--text-2xs)",
                    fontWeight: 700, display: "inline-flex", alignItems: "center", justifyContent: "center",
                  }}>{t.alerts}</span>
                )}
                {on && <span style={{ flex: "none", color: "var(--green-600)", fontWeight: 700 }}>✓</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
