import React from "react";

/**
 * NHA Button — primary / secondary / ghost / danger, in sm / md sizes.
 */
export function Button({
  variant = "secondary",
  size = "md",
  type = "button",
  disabled = false,
  fullWidth = false,
  leadingIcon = null,
  trailingIcon = null,
  style = {},
  children,
  ...rest
}) {
  const sizes = {
    sm: { padding: "5px 10px", fontSize: "var(--text-sm)", height: 30, gap: 6 },
    md: { padding: "8px 16px", fontSize: "var(--text-base)", height: 38, gap: 8 },
    lg: { padding: "11px 22px", fontSize: "var(--text-md)", height: 46, gap: 8 },
  };
  const variants = {
    primary: {
      background: "var(--action)",
      color: "var(--text-on-green)",
      border: "1px solid var(--action)",
    },
    secondary: {
      background: "var(--surface-card)",
      color: "var(--text-body)",
      border: "1px solid var(--border-input)",
    },
    ghost: {
      background: "transparent",
      color: "var(--text-soft)",
      border: "1px solid transparent",
    },
    danger: {
      background: "var(--surface-card)",
      color: "var(--red-text)",
      border: "1px solid var(--red-soft)",
    },
  };
  const s = sizes[size] || sizes.md;
  const v = variants[variant] || variants.secondary;

  return (
    <button
      type={type}
      disabled={disabled}
      data-variant={variant}
      className="nha-btn"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: s.gap,
        height: s.height,
        padding: s.padding,
        width: fullWidth ? "100%" : "auto",
        fontFamily: "var(--font-body)",
        fontSize: s.fontSize,
        fontWeight: "var(--weight-semibold)",
        lineHeight: 1,
        whiteSpace: "nowrap",
        borderRadius: "var(--radius-sm)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
        transition: "background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out)",
        ...v,
        ...style,
      }}
      {...rest}
    >
      {leadingIcon}
      {children}
      {trailingIcon}
    </button>
  );
}
