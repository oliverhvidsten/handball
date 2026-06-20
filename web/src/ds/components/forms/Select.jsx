import React from "react";

/**
 * NHA Select — native dropdown styled to match Input.
 * Pass options as [{value,label}] or render <option> children.
 */
export function Select({
  size = "md",
  invalid = false,
  label = null,
  options = null,
  id,
  style = {},
  children,
  ...rest
}) {
  const sizes = {
    sm: { height: 30, padding: "0 28px 0 9px", fontSize: "var(--text-sm)" },
    md: { height: 38, padding: "0 32px 0 11px", fontSize: "var(--text-base)" },
  };
  const s = sizes[size] || sizes.md;
  const caret =
    "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%235c6877' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'><polyline points='6 9 12 15 18 9'/></svg>\")";
  const field = (
    <select
      id={id}
      aria-invalid={invalid || undefined}
      className="nha-field"
      style={{
        width: "100%",
        height: s.height,
        padding: s.padding,
        fontFamily: "var(--font-body)",
        fontSize: s.fontSize,
        color: "var(--text-body)",
        background: `var(--surface-card) ${caret} no-repeat right 10px center`,
        border: `1px solid ${invalid ? "var(--red-600)" : "var(--border-input)"}`,
        borderRadius: "var(--radius-sm)",
        appearance: "none",
        WebkitAppearance: "none",
        cursor: "pointer",
        transition: "border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out)",
        ...style,
      }}
      {...rest}
    >
      {options ? options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>) : children}
    </select>
  );
  if (!label) return field;
  return (
    <label htmlFor={id} style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span style={{ fontSize: "var(--text-xs)", fontWeight: "var(--weight-semibold)", color: "var(--text-soft)" }}>
        {label}
      </span>
      {field}
    </label>
  );
}
