import React from "react";

/**
 * NHA Tag — small label/keyword chip. Tones: neutral, green, blue, amber, red, purple.
 */
export function Tag({ tone = "neutral", solid = false, size = "md", style = {}, children, ...rest }) {
  const tones = {
    neutral: { soft: ["var(--slate-soft)", "var(--slate-text)"], solid: ["var(--slate-600)", "#fff"] },
    green:   { soft: ["var(--green-50)", "var(--green-700)"], solid: ["var(--green-600)", "#fff"] },
    blue:    { soft: ["var(--blue-soft)", "var(--blue-text)"], solid: ["var(--blue-600)", "#fff"] },
    amber:   { soft: ["var(--amber-soft)", "var(--amber-text)"], solid: ["var(--amber-600)", "#fff"] },
    red:     { soft: ["var(--red-soft)", "var(--red-text)"], solid: ["var(--red-600)", "#fff"] },
    purple:  { soft: ["var(--purple-soft)", "var(--purple-text)"], solid: ["var(--purple-600)", "#fff"] },
  };
  const [bg, fg] = (tones[tone] || tones.neutral)[solid ? "solid" : "soft"];
  const sizes = {
    sm: { padding: "1px 6px", fontSize: "var(--text-2xs)" },
    md: { padding: "2px 8px", fontSize: "var(--text-xs)" },
  };
  const s = sizes[size] || sizes.md;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: s.padding,
        fontFamily: "var(--font-body)",
        fontSize: s.fontSize,
        fontWeight: "var(--weight-bold)",
        letterSpacing: "var(--tracking-wide)",
        textTransform: "uppercase",
        lineHeight: 1.4,
        color: fg,
        background: bg,
        borderRadius: "var(--radius-xs)",
        whiteSpace: "nowrap",
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}
