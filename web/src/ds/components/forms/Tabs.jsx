import React from "react";

/**
 * NHA Tabs — underline-style tab bar.
 * items: [{ value, label, count? }]
 */
export function Tabs({ items = [], value, onChange, size = "md", style = {} }) {
  const pad = size === "sm" ? "7px 2px" : "10px 2px";
  const fs = size === "sm" ? "var(--text-sm)" : "var(--text-base)";
  return (
    <div
      role="tablist"
      style={{
        display: "flex",
        gap: 20,
        borderBottom: "1px solid var(--line)",
        ...style,
      }}
    >
      {items.map((it) => {
        const active = it.value === value;
        return (
          <button
            key={it.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange && onChange(it.value)}
            style={{
              appearance: "none",
              background: "none",
              border: "none",
              padding: pad,
              marginBottom: -1,
              display: "inline-flex",
              alignItems: "center",
              gap: 7,
              fontFamily: "var(--font-body)",
              fontSize: fs,
              fontWeight: "var(--weight-semibold)",
              color: active ? "var(--text-body)" : "var(--muted)",
              borderBottom: `2px solid ${active ? "var(--action)" : "transparent"}`,
              cursor: "pointer",
              transition: "color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out)",
            }}
          >
            {it.label}
            {it.count != null && (
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-2xs)",
                  fontWeight: "var(--weight-bold)",
                  color: active ? "var(--action-hover)" : "var(--muted)",
                  background: active ? "var(--green-50)" : "var(--surface-3)",
                  borderRadius: "var(--radius-pill)",
                  padding: "1px 7px",
                }}
              >
                {it.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
