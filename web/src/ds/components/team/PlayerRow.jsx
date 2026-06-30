import React from "react";
import { Tag } from "../forms/Tag.jsx";
import { StatChip } from "../data/StatChip.jsx";
import { IconButton } from "../forms/IconButton.jsx";
import { Menu } from "../forms/Menu.jsx";

const POS_TONE = { Forward: "neutral", Midfielder: "neutral", Defense: "neutral", Goalie: "neutral" };
const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"];

/**
 * NHA PlayerRow — one roster player: name, position tag, O/D/G stat chips,
 * an optional INJ flag, and (when editable) a ⋯ menu with move actions.
 */
export function PlayerRow({
  player,
  editable = false,
  slot = null,
  canMoveUp = false,
  canMoveDown = false,
  onMoveUp,
  onMoveDown,
  onSlot,
  onClick,
  showGoalie = null,
  showPosition = true,
  style = {},
}) {
  const p = player || {};
  const isGoalie = p.position === "Goalie";
  const goalieVisible = showGoalie != null ? showGoalie : isGoalie;

  // Any player may be placed at any position (Goalie included), so the move
  // menu offers every starter/bench position explicitly rather than a single
  // "Move to Starters/Bench" that auto-picked the player's card position.
  const menuItems = [
    { label: "Move up", onClick: onMoveUp, disabled: !canMoveUp },
    { label: "Move down", onClick: onMoveDown, disabled: !canMoveDown },
    { divider: true },
    ...POSITIONS.map((pos) => ({
      label: `Start: ${pos}`,
      onClick: onSlot ? () => onSlot("starters", pos) : undefined,
    })),
    { divider: true },
    ...POSITIONS.map((pos) => ({
      label: `Bench: ${pos}`,
      onClick: onSlot ? () => onSlot("bench", pos) : undefined,
    })),
    { divider: true },
    { label: "Move to Reserves", onClick: onSlot ? () => onSlot("reserves") : undefined },
  ];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: "8px 10px",
        background: "var(--surface-card)",
        border: `1px solid ${p.injured ? "var(--red-soft)" : "var(--line)"}`,
        borderLeft: p.injured ? "3px solid var(--red-600)" : "1px solid var(--line)",
        borderRadius: "var(--radius-sm)",
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {p.number != null && (
          <span style={{
            flex: "none", width: 26, textAlign: "center", fontFamily: "var(--font-mono)",
            fontWeight: "var(--weight-bold)", fontSize: "var(--text-sm)", color: "var(--muted)",
          }}>
            {p.number}
          </span>
        )}
        <span
          onClick={onClick}
          style={{
            flex: 1, minWidth: 0,
            fontWeight: "var(--weight-semibold)", fontSize: "var(--text-sm)", color: "var(--text-body)",
            cursor: onClick ? "pointer" : "default", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}
        >
          {p.name}
          {p.injured && <Tag tone="red" solid size="sm" style={{ marginLeft: 6, verticalAlign: "middle" }}>INJ</Tag>}
        </span>
        {editable && (
          <Menu
            style={{ flex: "none" }}
            items={menuItems}
            trigger={<IconButton size="sm" title="Move player">⋯</IconButton>}
          />
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
        {showPosition && (
          <Tag tone={POS_TONE[p.position] || "neutral"} size="sm" style={{ marginRight: 1 }}>{p.position}</Tag>
        )}
        {!isGoalie && <StatChip kind="offense" value={fmt(p.offense)} />}
        {!isGoalie && <StatChip kind="defense" value={fmt(p.defense)} />}
        {goalieVisible && <StatChip kind="goalie" value={fmt(p.goalie)} />}
      </div>
    </div>
  );
}

function fmt(v) {
  if (v == null) return "—";
  return typeof v === "number" ? v.toFixed(1) : v;
}
