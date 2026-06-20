import React from "react";

const TONES = {
  success: { accent: "var(--green-500)", icon: "✓" },
  error:   { accent: "var(--red-600)",   icon: "!" },
  info:    { accent: "var(--blue-600)",  icon: "i" },
};

/**
 * NHA Toast — transient confirmation. Render in a fixed corner stack.
 */
export function Toast({ tone = "success", title, message = null, onClose, style = {} }) {
  const t = TONES[tone] || TONES.success;
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        width: 320,
        padding: "12px 14px",
        background: "var(--surface-card)",
        border: "1px solid var(--line)",
        borderLeft: `3px solid ${t.accent}`,
        borderRadius: "var(--radius-md)",
        boxShadow: "var(--shadow-lg)",
        ...style,
      }}
    >
      <span style={{
        flex: "none", width: 20, height: 20,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        borderRadius: "50%", background: t.accent, color: "#fff",
        fontFamily: "var(--font-display)", fontWeight: "var(--weight-bold)", fontSize: 12,
      }}>
        {t.icon}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: "var(--weight-semibold)", fontSize: "var(--text-sm)", color: "var(--text-body)" }}>{title}</div>
        {message && <div style={{ fontSize: "var(--text-sm)", color: "var(--muted)", marginTop: 2 }}>{message}</div>}
      </div>
      {onClose && (
        <button
          onClick={onClose}
          aria-label="Dismiss"
          style={{ flex: "none", background: "none", border: "none", cursor: "pointer", color: "var(--muted)", fontSize: 16, lineHeight: 1, padding: 2 }}
        >
          ×
        </button>
      )}
    </div>
  );
}
