import React from "react";

/**
 * NHA NotificationBadge — count bubble for trade requests / alerts.
 */
export function NotificationBadge({ count = 0, max = 99, tone = "red", dot = false, showZero = false, children, style = {} }) {
  const tones = {
    red: ["var(--red-600)", "#fff"],
    green: ["var(--green-600)", "#fff"],
    blue: ["var(--blue-600)", "#fff"],
    amber: ["var(--amber-600)", "#fff"],
  };
  const [bg, fg] = tones[tone] || tones.red;
  const visible = dot || count > 0 || showZero;
  const label = count > max ? `${max}+` : String(count);

  const badge = visible ? (
    <span
      style={{
        position: children ? "absolute" : "static",
        top: children ? -6 : undefined,
        right: children ? -8 : undefined,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: dot ? 8 : 18,
        height: dot ? 8 : 18,
        padding: dot ? 0 : "0 5px",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-2xs)",
        fontWeight: "var(--weight-bold)",
        lineHeight: 1,
        color: fg,
        background: bg,
        borderRadius: "var(--radius-pill)",
        border: children ? "2px solid var(--ink-950)" : "none",
        boxSizing: "border-box",
      }}
    >
      {!dot && label}
    </span>
  ) : null;

  if (!children) return badge;
  return (
    <span style={{ position: "relative", display: "inline-flex", ...style }}>
      {children}
      {badge}
    </span>
  );
}
