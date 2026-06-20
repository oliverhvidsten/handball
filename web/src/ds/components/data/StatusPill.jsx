import React from "react";

const STATUS = {
  proposed:  { bg: "var(--status-proposed-bg)",  fg: "var(--status-proposed-text)",  dot: "var(--amber-600)" },
  accepted:  { bg: "var(--status-accepted-bg)",  fg: "var(--status-accepted-text)",  dot: "var(--blue-600)" },
  committed: { bg: "var(--status-committed-bg)", fg: "var(--status-committed-text)", dot: "var(--green-600)" },
  rejected:  { bg: "var(--status-rejected-bg)",  fg: "var(--status-rejected-text)",  dot: "var(--red-600)" },
  cancelled: { bg: "var(--status-cancelled-bg)", fg: "var(--status-cancelled-text)", dot: "var(--slate-600)" },
};

/**
 * NHA StatusPill — trade lifecycle status.
 * Variants: proposed · accepted · committed · rejected · cancelled.
 */
export function StatusPill({ status = "proposed", dot = true, style = {}, children, ...rest }) {
  const key = String(status).toLowerCase();
  const s = STATUS[key] || STATUS.proposed;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 10px 3px 8px",
        fontFamily: "var(--font-body)",
        fontSize: "var(--text-xs)",
        fontWeight: "var(--weight-bold)",
        letterSpacing: "var(--tracking-wide)",
        textTransform: "uppercase",
        lineHeight: 1.5,
        color: s.fg,
        background: s.bg,
        borderRadius: "var(--radius-pill)",
        whiteSpace: "nowrap",
        ...style,
      }}
      {...rest}
    >
      {dot && (
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: s.dot, flex: "none" }} />
      )}
      {children || key}
    </span>
  );
}
