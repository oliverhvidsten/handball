import React from "react";

/**
 * NHA IconButton — compact square button for inline row controls.
 */
export function IconButton({
  size = "md",
  active = false,
  disabled = false,
  title,
  style = {},
  children,
  ...rest
}) {
  const dims = { sm: 24, md: 28, lg: 34 };
  const d = dims[size] || dims.md;
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      className="nha-iconbtn"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: d,
        height: d,
        padding: 0,
        fontFamily: "var(--font-body)",
        fontSize: "var(--text-xs)",
        fontWeight: "var(--weight-bold)",
        color: active ? "#fff" : "var(--text-soft)",
        background: active ? "var(--action)" : "var(--surface-card)",
        border: `1px solid ${active ? "var(--action)" : "var(--line)"}`,
        borderRadius: "var(--radius-sm)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.4 : 1,
        transition: "background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out)",
        ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
