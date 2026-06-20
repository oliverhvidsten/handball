import React from "react";

/**
 * NHA Input — single-line text field. Supports label, error state, and sizes.
 */
export function Input({
  type = "text",
  size = "md",
  invalid = false,
  label = null,
  hint = null,
  id,
  style = {},
  ...rest
}) {
  const sizes = {
    sm: { height: 30, padding: "0 9px", fontSize: "var(--text-sm)" },
    md: { height: 38, padding: "0 11px", fontSize: "var(--text-base)" },
    lg: { height: 46, padding: "0 13px", fontSize: "var(--text-md)" },
  };
  const s = sizes[size] || sizes.md;
  const field = (
    <input
      id={id}
      type={type}
      aria-invalid={invalid || undefined}
      className="nha-field"
      style={{
        width: "100%",
        height: s.height,
        padding: s.padding,
        fontFamily: "var(--font-body)",
        fontSize: s.fontSize,
        color: "var(--text-body)",
        background: "var(--surface-card)",
        border: `1px solid ${invalid ? "var(--red-600)" : "var(--border-input)"}`,
        borderRadius: "var(--radius-sm)",
        transition: "border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out)",
        ...style,
      }}
      {...rest}
    />
  );
  if (!label && !hint) return field;
  return (
    <label htmlFor={id} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      {label && (
        <span style={{ fontSize: "var(--text-xs)", fontWeight: "var(--weight-semibold)", color: "var(--text-soft)" }}>
          {label}
        </span>
      )}
      {field}
      {hint && (
        <span style={{ fontSize: "var(--text-xs)", color: invalid ? "var(--red-text)" : "var(--muted)" }}>
          {hint}
        </span>
      )}
    </label>
  );
}
