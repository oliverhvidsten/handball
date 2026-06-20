import React from "react";
import { PlayerRow } from "./PlayerRow.jsx";

const POSITIONS = ["Forward", "Midfielder", "Defense", "Goalie"];

/**
 * NHA RosterColumns — the four-position grid used for Starters and Bench.
 * byPosition: { Forward:[player], Midfielder:[player], Defense:[player], Goalie:[player] }
 */
export function RosterColumns({
  byPosition = {},
  caps = null,
  editable = false,
  slot = null,
  onMove,
  onSlot,
  onPlayerClick,
  positions = POSITIONS,
  style = {},
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${positions.length}, 1fr)`,
        gap: 12,
        ...style,
      }}
    >
      {positions.map((pos) => {
        const list = byPosition[pos] || [];
        const cap = caps && caps[pos];
        return (
          <div key={pos} style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
              <h4 style={{
                fontFamily: "var(--font-body)", fontSize: "var(--text-2xs)", fontWeight: "var(--weight-bold)",
                letterSpacing: "var(--tracking-wide)", textTransform: "uppercase", color: "var(--muted)", margin: 0,
              }}>
                {pos}
              </h4>
              {cap != null && (
                <span style={{
                  fontFamily: "var(--font-mono)", fontSize: "var(--text-2xs)",
                  color: list.length > cap ? "var(--red-text)" : "var(--muted)",
                }}>
                  {list.length}/{cap}
                </span>
              )}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {list.length === 0 ? (
                <div style={{
                  padding: "10px 8px", textAlign: "center", fontSize: "var(--text-xs)", color: "var(--muted)",
                  border: "1px dashed var(--line-strong)", borderRadius: "var(--radius-sm)",
                }}>
                  Empty
                </div>
              ) : (
                list.map((pl) => (
                  <PlayerRow
                    key={pl.id ?? pl.name}
                    player={pl}
                    editable={editable}
                    slot={slot}
                    onMoveUp={onMove ? () => onMove(pl, -1) : undefined}
                    onMoveDown={onMove ? () => onMove(pl, 1) : undefined}
                    onSlot={onSlot ? (g) => onSlot(pl, g) : undefined}
                    onClick={onPlayerClick ? () => onPlayerClick(pl) : undefined}
                  />
                ))
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
