import React, { useEffect, useRef, useState } from "react";

/**
 * NHA CapMeter — a team's salary-cap standing as one figure over one track.
 *
 * `cap` is handball/signing_service.team_cap_report verbatim: payroll, cap_room,
 * mid_level_exception, hard_cap_room, projected_next_payroll, roster_size,
 * max_roster, roster_spots, and `limits` (salary_cap, first/second luxury
 * threshold, hard_cap). Every rule is decided server-side; this only draws it.
 *
 * Anatomy: a status pill (icon + words, never colour alone), the payroll as the
 * card's one hero figure, a meter whose fill carries severity (green under the cap,
 * amber in the tax tiers, red at the hard cap) over a track in a lighter step of the
 * same ramp, threshold ticks at the four league lines, a thinner second track for
 * what is already committed next season on the SAME scale, and a row of the four
 * numbers a manager acts on. Money is in $M, as everywhere else in the app.
 */

const fmt = (m) => (m < 0 ? `−$${Math.abs(m)}M` : `$${m}M`);

// Severity ramp: which zone the payroll sits in decides the fill, the track, and the
// pill. Steps come from the kit's tokens so the meter matches every other status cue.
const ZONES = {
  under:   { fill: "var(--green-500)", track: "var(--green-100)", pill: { bg: "var(--green-100)", fg: "var(--green-800)" },
             icon: "✓", label: "Under the cap" },
  over:    { fill: "var(--amber-600)", track: "var(--amber-soft)", pill: { bg: "var(--amber-soft)", fg: "var(--amber-text)" },
             icon: "◔", label: "Over the cap" },
  tax1:    { fill: "var(--amber-600)", track: "var(--amber-soft)", pill: { bg: "var(--amber-soft)", fg: "var(--amber-text)" },
             icon: "◑", label: "Luxury tax" },
  tax2:    { fill: "#d9731f",          track: "var(--amber-soft)", pill: { bg: "var(--amber-soft)", fg: "var(--amber-text)" },
             icon: "◕", label: "Second tax tier" },
  hard:    { fill: "var(--red-600)",   track: "var(--red-soft)",   pill: { bg: "var(--red-soft)", fg: "var(--red-text)" },
             icon: "!", label: "Over the hard cap" },
};

function zoneOf(cap) {
  const { payroll, limits: L } = cap;
  if (payroll > L.hard_cap) return "hard";
  if (payroll >= L.second_luxury_threshold) return "tax2";
  if (payroll >= L.first_luxury_threshold) return "tax1";
  if (payroll > L.salary_cap) return "over";
  return "under";
}

function Pill({ zone }) {
  const z = ZONES[zone];
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 10px 3px 8px",
      fontFamily: "var(--font-body)", fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)",
      letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", lineHeight: 1.5,
      color: z.pill.fg, background: z.pill.bg, borderRadius: 999, whiteSpace: "nowrap",
    }}>
      <span aria-hidden style={{ fontSize: 11 }}>{z.icon}</span>
      {z.label}
    </span>
  );
}

/** A horizontal meter: track, fill with a rounded data-end, and threshold ticks. */
function Track({ value, max, zone, height, ticks, label, valueLabel, muted = false }) {
  const pct = (v) => `${Math.min(100, Math.max(0, (v / max) * 100))}%`;
  const z = ZONES[zone];
  const fill = muted ? "var(--slate-400, #94a3b8)" : z.fill;
  const track = muted ? "var(--surface-3)" : z.track;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", rowGap: 4 }}>
      {(label || valueLabel) && (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
                      fontSize: "var(--text-xs)", color: "var(--muted)" }}>
          <span>{label}</span>
          <span style={{ fontFamily: "var(--font-body)", fontWeight: "var(--weight-semibold)", color: "var(--text-body)" }}>
            {valueLabel}
          </span>
        </div>
      )}
      <div
        role="meter"
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-label={label || "Payroll"}
        title={`${label || "Payroll"}: ${fmt(value)}`}
        style={{ position: "relative", height, background: track, borderRadius: 4, overflow: "visible" }}
      >
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0, width: pct(value),
          background: fill, borderRadius: value >= max ? 4 : "4px 4px 4px 4px",
          transition: "width var(--dur-med, 240ms) var(--ease-out, ease-out)",
        }} />
        {ticks.map((t) => (
          <span
            key={t.at}
            title={`${t.name}: ${fmt(t.at)}`}
            style={{
              position: "absolute", top: -3, bottom: -3, left: pct(t.at), width: 2, marginLeft: -1,
              background: t.strong ? "var(--ink-900, #1f2937)" : "var(--line-strong)",
              borderRadius: 1,
            }}
          />
        ))}
      </div>
    </div>
  );
}

/** Width of the card, so labels can thin out before they collide. */
function useWidth(ref) {
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

// Below this card width the two middle thresholds keep their tick marks (and hover
// names) but lose their labels: at phone width four labels overlap into noise, and
// the cap and hard-cap anchors are the two a manager reads first.
const LABEL_ALL_MIN_WIDTH = 560;

function TickLabels({ ticks, max, compact }) {
  const pct = (v) => `${Math.min(100, (v / max) * 100)}%`;
  const shown = compact ? ticks.filter((t, i) => i === 0 || t.strong) : ticks;
  return (
    <div style={{ position: "relative", height: 16, fontSize: "var(--text-2xs)", color: "var(--muted)",
                  fontVariantNumeric: "tabular-nums" }}>
      {shown.map((t, i) => {
        const last = i === shown.length - 1;
        return (
          <span key={t.at} style={{
            position: "absolute", left: pct(t.at), top: 0, whiteSpace: "nowrap",
            transform: last && t.at >= max ? "translateX(-100%)" : "translateX(-50%)",
            fontWeight: t.strong ? "var(--weight-bold)" : "var(--weight-medium)",
            color: t.strong ? "var(--text-body)" : "var(--muted)",
          }}>
            {t.short}
          </span>
        );
      })}
    </div>
  );
}

function Fact({ label, value, sub, tone }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)", letterSpacing: "var(--tracking-wide)",
                    textTransform: "uppercase", color: "var(--muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontFamily: "var(--font-body)", fontSize: "var(--text-lg)", fontWeight: "var(--weight-semibold)",
                    color: tone === "bad" ? "var(--red-text)" : "var(--text-body)", lineHeight: 1.15 }}>{value}</div>
      {sub && <div style={{ fontSize: "var(--text-xs)", color: "var(--muted)" }}>{sub}</div>}
    </div>
  );
}

export function CapMeter({ cap, title = "Salary cap", actions = null, style = {} }) {
  const ref = useRef(null);
  const width = useWidth(ref);
  if (!cap) return null;
  const compact = width > 0 && width < LABEL_ALL_MIN_WIDTH;
  const L = cap.limits;
  const zone = zoneOf(cap);
  const z = ZONES[zone];
  // The scale runs to the hard cap; a team above it pushes the scale out so the
  // overrun is visible rather than clipped at a full bar.
  const max = Math.max(L.hard_cap, cap.payroll, cap.projected_next_payroll);
  const ticks = [
    { at: L.salary_cap, name: "Salary cap", short: `${fmt(L.salary_cap)} cap` },
    { at: L.first_luxury_threshold, name: "First luxury-tax threshold", short: fmt(L.first_luxury_threshold) },
    { at: L.second_luxury_threshold, name: "Second luxury-tax threshold", short: fmt(L.second_luxury_threshold) },
    { at: L.hard_cap, name: "Hard cap", short: `${fmt(L.hard_cap)} hard`, strong: true },
  ];
  const overBy = cap.payroll - L.salary_cap;
  const subline =
    zone === "hard" ? `${fmt(cap.payroll - L.hard_cap)} over the hard cap — no signings until it comes down`
    : zone === "under" ? `${fmt(cap.cap_room)} of room under the ${fmt(L.salary_cap)} cap`
    : `${fmt(overBy)} over the ${fmt(L.salary_cap)} cap · ${fmt(cap.hard_cap_room)} to the hard cap`;

  return (
    <div ref={ref} style={{
      background: "var(--surface-card)", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)",
      boxShadow: "var(--shadow-sm)", padding: "16px 18px 14px", position: "relative", overflow: "hidden", ...style,
    }}>
      <span style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 4, background: z.fill }} />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--text-xs)", fontWeight: "var(--weight-bold)", letterSpacing: "var(--tracking-wide)",
                       textTransform: "uppercase", color: "var(--muted)" }}>{title}</span>
        <Pill zone={zone} />
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap", margin: "6px 0 2px" }}>
        <span style={{ fontFamily: "var(--font-body)", fontSize: 44, fontWeight: "var(--weight-bold)", lineHeight: 1,
                       letterSpacing: "-0.02em", color: "var(--text-body)" }}>
          {fmt(cap.payroll)}
        </span>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--muted)" }}>payroll this season</span>
      </div>
      <div style={{ fontSize: "var(--text-sm)", color: "var(--muted)", marginBottom: 14 }}>{subline}</div>

      <Track value={cap.payroll} max={max} zone={zone} height={14} ticks={ticks} />
      <TickLabels ticks={ticks} max={max} compact={compact} />

      <div style={{ marginTop: 10 }}>
        <Track
          value={cap.projected_next_payroll} max={max} zone={zone} height={6} ticks={ticks} muted
          label="Committed next season" valueLabel={fmt(cap.projected_next_payroll)}
        />
      </div>

      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 14,
        marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--line)",
      }}>
        <Fact label="Cap room" value={fmt(cap.cap_room)} sub={cap.cap_room > 0 ? "to spend outright" : "none — MLE only"} />
        <Fact label="Mid-level exception" value={fmt(cap.mid_level_exception)}
              sub={cap.mid_level_exception > 0 ? "for one outside signing" : "none at this payroll"} />
        <Fact label="Hard-cap headroom" value={fmt(cap.hard_cap_room)} tone={cap.hard_cap_room <= 0 ? "bad" : undefined}
              sub={cap.hard_cap_room <= 0 ? "must shed salary" : "absolute limit"} />
        <Fact label="Roster" value={`${cap.roster_size}/${cap.max_roster}`}
              sub={cap.roster_spots > 0 ? `${cap.roster_spots} spot${cap.roster_spots === 1 ? "" : "s"} open` : "full"}
              tone={cap.roster_spots === 0 ? undefined : undefined} />
      </div>

      {actions && <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>{actions}</div>}
    </div>
  );
}
