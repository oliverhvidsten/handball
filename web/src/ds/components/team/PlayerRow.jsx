import React from "react";
import { Tag } from "../forms/Tag.jsx";
import { StatChip } from "../data/StatChip.jsx";
import { IconButton } from "../forms/IconButton.jsx";

const POS_TONE = { Forward: "green", Midfielder: "blue", Defense: "amber", Goalie: "purple" };

/**
 * NHA PlayerRow — one roster player: name, position tag, O/D/G stat chips,
 * an optional INJ flag, and (when editable) up/down + S/B/R move controls.
 */
export function PlayerRow({
  player,
  editable = false,
  slot = null,
  onMoveUp,
  onMoveDown,
  onSlot,
  onClick,
  showGoalie = null,
  style = {},
}) {
  const p = player || {};
  const isGoalie = p.position === "Goalie";
  const goalieVisible = showGoalie != null ? showGoalie : isGoalie;
  const slotKey = { starters: "S", bench: "B", reserves: "R" };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 10px",
        background: "var(--surface-card)",
        border: `1px solid ${p.injured ? "var(--red-soft)" : "var(--line)"}`,
        borderLeft: p.injured ? "3px solid var(--red-600)" : "1px solid var(--line)",
        borderRadius: "var(--radius-sm)",
        ...style,
      }}
    >
      {p.number != null && (
        <span style={{
          flex: "none", width: 26, textAlign: "center", fontFamily: "var(--font-mono)",
          fontWeight: "var(--weight-bold)", fontSize: "var(--text-sm)", color: "var(--muted)",
        }}>
          {p.number}
        </span>
      )}

      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <span
          onClick={onClick}
          style={{
            fontWeight: "var(--weight-semibold)", fontSize: "var(--text-sm)", color: "var(--text-body)",
            cursor: onClick ? "pointer" : "default", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}
        >
          {p.name}
          {p.injured && <Tag tone="red" solid size="sm" style={{ marginLeft: 6, verticalAlign: "middle" }}>INJ</Tag>}
        </span>
        <span>
          <Tag tone={POS_TONE[p.position] || "neutral"} size="sm">{p.position}</Tag>
        </span>
      </div>

      <div style={{ display: "flex", gap: 5, flex: "none" }}>
        <StatChip kind="offense" value={fmt(p.offense)} />
        <StatChip kind="defense" value={fmt(p.defense)} />
        {goalieVisible && <StatChip kind="goalie" value={fmt(p.goalie)} />}
      </div>

      {editable && (
        <div style={{ display: "flex", gap: 3, flex: "none", marginLeft: 2 }}>
          <IconButton size="sm" title="Move up" onClick={onMoveUp}>▲</IconButton>
          <IconButton size="sm" title="Move down" onClick={onMoveDown}>▼</IconButton>
          <span style={{ width: 6 }} />
          {["starters", "bench", "reserves"].map((g) => (
            <IconButton
              key={g}
              size="sm"
              active={slot === g}
              title={g[0].toUpperCase() + g.slice(1)}
              onClick={onSlot ? () => onSlot(g) : undefined}
            >
              {slotKey[g]}
            </IconButton>
          ))}
        </div>
      )}
    </div>
  );
}

function fmt(v) {
  if (v == null) return "—";
  return typeof v === "number" ? v.toFixed(1) : v;
}
