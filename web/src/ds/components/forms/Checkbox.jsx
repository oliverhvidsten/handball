import React from "react";

/**
 * NHA Checkbox — square check with a label, used in trade pickers.
 */
export function Checkbox({ checked = false, onChange, label = null, disabled = false, id, style = {}, ...rest }) {
  const box = (
    <span
      style={{
        position: "relative",
        display: "inline-flex",
        flex: "none",
        width: 18,
        height: 18,
        borderRadius: "var(--radius-xs)",
        border: `1.5px solid ${checked ? "var(--action)" : "var(--border-input)"}`,
        background: checked ? "var(--action)" : "var(--surface-card)",
        transition: "background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out)",
      }}
    >
      <input
        id={id}
        type="checkbox"
        className="nha-check"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        style={{
          position: "absolute",
          inset: 0,
          margin: 0,
          opacity: 0,
          cursor: disabled ? "not-allowed" : "pointer",
        }}
        {...rest}
      />
      {checked && (
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.5"
          strokeLinecap="round" strokeLinejoin="round"
          style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%,-50%)" }}>
          <polyline points="20 6 9 17 4 12" />
        </svg>
      )}
    </span>
  );
  if (!label) return box;
  return (
    <label
      htmlFor={id}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        fontSize: "var(--text-sm)",
        color: "var(--text-body)",
        ...style,
      }}
    >
      {box}
      <span>{label}</span>
    </label>
  );
}
