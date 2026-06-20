import React from "react";

const TONES = {
  error:   { bg: "var(--red-soft)",   border: "var(--red-600)",   fg: "var(--red-text)",   icon: "!" },
  success: { bg: "var(--green-50)",   border: "var(--green-500)", fg: "var(--green-700)",  icon: "✓" },
  info:    { bg: "var(--blue-soft)",  border: "var(--blue-600)",  fg: "var(--blue-text)",  icon: "i" },
  warning: { bg: "var(--amber-soft)", border: "var(--amber-600)", fg: "var(--amber-text)", icon: "!" },
};

/**
 * NHA Alert — inline callout. Pass `items` for a bulleted validation problem list.
 */
export function Alert({ tone = "info", title = null, items = null, style = {}, children }) {
  const t = TONES[tone] || TONES.info;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      style={{
        display: "flex",
        gap: 10,
        padding: "11px 13px",
        background: t.bg,
        border: `1px solid ${t.border}`,
        borderRadius: "var(--radius-md)",
        color: t.fg,
        fontSize: "var(--text-sm)",
        lineHeight: "var(--leading-normal)",
        ...style,
      }}
    >
      <span style={{
        flex: "none", width: 18, height: 18, marginTop: 1,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        borderRadius: "50%", background: t.border, color: "#fff",
        fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: 12,
      }}>
        {t.icon}
      </span>
      <div style={{ flex: 1 }}>
        {title && <div style={{ fontWeight: "var(--weight-bold)", marginBottom: items || children ? 4 : 0 }}>{title}</div>}
        {children}
        {items && (
          <ul style={{ margin: title ? "0" : 0, paddingLeft: 18 }}>
            {items.map((it, i) => <li key={i} style={{ marginTop: i ? 2 : 0 }}>{it}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}
