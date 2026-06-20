import React from "react";

/**
 * NHA EmptyState — centered placeholder for empty lists. Optional icon + action.
 */
export function EmptyState({ icon = null, title, message = null, action = null, compact = false, style = {} }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        gap: 8,
        padding: compact ? "28px 20px" : "52px 24px",
        background: "var(--surface-2)",
        border: "1px dashed var(--line-strong)",
        borderRadius: "var(--radius-lg)",
        ...style,
      }}
    >
      {icon && (
        <div style={{
          width: 44, height: 44, marginBottom: 4,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          borderRadius: "var(--radius-pill)", background: "var(--surface-3)", color: "var(--muted)",
        }}>
          {icon}
        </div>
      )}
      <div style={{ fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: "var(--text-lg)", color: "var(--text-soft)" }}>
        {title}
      </div>
      {message && <div style={{ fontSize: "var(--text-sm)", color: "var(--muted)", maxWidth: 360 }}>{message}</div>}
      {action && <div style={{ marginTop: 6 }}>{action}</div>}
    </div>
  );
}
